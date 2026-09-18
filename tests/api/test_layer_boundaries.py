import ast
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "services" / "api" / "app"


def _python_files(folder: Path) -> list[Path]:
    return sorted(path for path in folder.rglob("*.py") if "__pycache__" not in path.parts)


def _module_name(path: Path) -> str:
    return ".".join(path.relative_to(APP.parent).with_suffix("").parts)


def _resolve_from(module_name: str, node: ast.ImportFrom) -> str:
    if not node.level:
        return node.module or ""
    package = module_name.split(".")[:-1]
    keep = max(0, len(package) - node.level + 1)
    base = package[:keep]
    if node.module:
        base.extend(node.module.split("."))
    return ".".join(base)


def _imports(path: Path, source: str | None = None) -> list[tuple[str, int]]:
    source = source if source is not None else path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    module_name = _module_name(path)
    imports: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend((alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = _resolve_from(module_name, node)
            imports.extend(
                ((f"{base}.{alias.name}" if base else alias.name), node.lineno)
                for alias in node.names
            )
    return imports


def _bound_imports(path: Path, source: str) -> dict[str, str]:
    tree = ast.parse(source, filename=str(path))
    module_name = _module_name(path)
    bindings: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    bindings[alias.asname] = alias.name
                else:
                    root = alias.name.split(".")[0]
                    bindings[root] = root
        elif isinstance(node, ast.ImportFrom):
            base = _resolve_from(module_name, node)
            for alias in node.names:
                bindings[alias.asname or alias.name] = f"{base}.{alias.name}" if base else alias.name
    return bindings


def _attribute_target(node: ast.AST, bindings: dict[str, str]) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name) or node.id not in bindings:
        return None
    return ".".join([bindings[node.id], *reversed(parts)])


def _forbidden_root_references(path: Path, source: str) -> list[str]:
    tree = ast.parse(source, filename=str(path))
    bindings = _bound_imports(path, source)
    violations = [target for target, _ in _imports(path, source) if target == "app.main"]
    for node in ast.walk(tree):
        target = _attribute_target(node, bindings)
        if target and (target == "app.main" or target.startswith("app.main.")):
            violations.append(target)
    return sorted(set(violations))


def _router_sql_violations(path: Path, source: str) -> list[str]:
    tree = ast.parse(source, filename=str(path))
    violations = [
        target for target, _ in _imports(path, source)
        if target == "pymysql" or target.startswith("pymysql.")
        or target == "mysql.connector" or target.startswith("mysql.connector.")
        or target == "MySQLdb" or target.startswith("MySQLdb.")
    ]
    database_receivers = {"cursor", "cur", "connection", "conn", "db", "database"}

    def receiver_is_database(node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            name = node.id.lower()
            return name in database_receivers or name.endswith(("_cursor", "_connection", "_conn"))
        if isinstance(node, ast.Attribute):
            return node.attr.lower() in database_receivers or receiver_is_database(node.value)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            return node.func.attr.lower() == "cursor" or receiver_is_database(node.func.value)
        return False

    def contains_raw_sql(node: ast.Call) -> bool:
        if not node.args:
            return False
        value = node.args[0]
        text = ""
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            text = value.value
        elif isinstance(value, ast.JoinedStr) and value.values \
                and isinstance(value.values[0], ast.Constant) and isinstance(value.values[0].value, str):
            text = value.values[0].value
        text = text.lstrip().upper()
        return text.startswith(("SELECT ", "INSERT ", "UPDATE ", "DELETE ", "REPLACE ", "WITH ",
                                "ALTER ", "CREATE ", "DROP ", "TRUNCATE ", "GRANT ", "REVOKE "))

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr in {"execute", "executemany"} \
                and (receiver_is_database(node.func.value) or contains_raw_sql(node)):
            violations.append(node.func.attr)
    return violations


def _legacy_locator_calls(path: Path, source: str) -> list[str]:
    tree = ast.parse(source, filename=str(path))
    bindings = _bound_imports(path, source)
    violations = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = _attribute_target(node.func, bindings)
        if target in {"app.tasks.core", "app.platform.install"}:
            violations.append(target)
    return violations


def test_repositories_are_framework_independent() -> None:
    """Catch repository code coupled to HTTP framework types or dependencies."""
    violations = []
    for path in APP.glob("domains/*/repository.py"):
        for imported, line in _imports(path):
            if imported == "fastapi" or imported.startswith("fastapi."):
                violations.append(f"{path.relative_to(ROOT)}:{line}:{imported}")
    assert violations == []


def test_domains_and_runtime_do_not_import_composition_root() -> None:
    """Catch service-locator regressions that make domain/runtime code import app.main."""
    violations = []
    for folder in (APP / "domains", APP / "runtime"):
        for path in _python_files(folder):
            for imported, line in _imports(path):
                if imported == "app.main" or imported.startswith("app.main."):
                    violations.append(f"{path.relative_to(ROOT)}:{line}:{imported}")
    assert violations == []


def test_routers_do_not_import_mysql_drivers() -> None:
    """Catch HTTP routers bypassing services/repositories for direct database access."""
    violations = []
    for path in APP.glob("domains/*/router.py"):
        for item in _router_sql_violations(path, path.read_text(encoding="utf-8")):
            violations.append(f"{path.relative_to(ROOT)}:{item}")
    assert violations == []


def test_boundary_analyzer_rejects_aliases_relative_main_and_router_sql() -> None:
    probe = APP / "runtime" / "probe.py"
    samples = (
        "from app import main\n",
        "from .. import main\n",
        "import app.main as root\nroot.create_app()\n",
    )
    assert all(_forbidden_root_references(probe, sample) for sample in samples)

    task_samples = (
        "import app.tasks as work\nwork.core()\n",
        "from app import tasks as work\nwork.core()\n",
        "import app.tasks\napp.tasks.core()\n",
        "from .. import tasks as work\nwork.core()\n",
    )
    for sample in task_samples:
        assert _legacy_locator_calls(probe, sample) == ["app.tasks.core"]

    router_probe = APP / "domains" / "probe" / "router.py"
    assert _router_sql_violations(router_probe, "import mysql.connector\ncursor.execute('SELECT 1')\n")
    assert _router_sql_violations(router_probe, "import MySQLdb\ncursor.executemany('SELECT 1', [])\n")
    assert _router_sql_violations(router_probe, "import pymysql\nconnection.cursor().execute('SELECT 1')\n")
    assert _router_sql_violations(router_probe, "service.execute('SELECT 1')\n")
    assert _router_sql_violations(router_probe, "service.execute(payload)\n") == []
    assert _router_sql_violations(router_probe, "service.execute(f'{payload}')\n") == []

    platform_samples = (
        "import app.platform as platform\nplatform.install()\n",
        "from app import platform as platform\nplatform.install()\n",
        "import app.platform\napp.platform.install()\n",
        "from .. import platform as platform\nplatform.install()\n",
    )
    for sample in platform_samples:
        assert _legacy_locator_calls(probe, sample) == ["app.platform.install"]


def test_production_import_graph_has_no_cycles_or_cross_layer_private_imports() -> None:
    modules = {_module_name(path): path for path in _python_files(APP)}
    graph: dict[str, set[str]] = defaultdict(set)
    private_cross_layer = []
    for module, path in modules.items():
        source_layer = module.split(".")[1] if len(module.split(".")) > 1 else ""
        for target, line in _imports(path):
            candidates = [target]
            while candidates[-1] not in modules and "." in candidates[-1]:
                candidates.append(candidates[-1].rsplit(".", 1)[0])
            imported_module = next((candidate for candidate in candidates if candidate in modules), None)
            if imported_module:
                graph[module].add(imported_module)
            parts = target.split(".")
            target_layer = parts[1] if len(parts) > 1 else ""
            if source_layer != target_layer and parts[-1].startswith("_"):
                private_cross_layer.append(f"{path.relative_to(ROOT)}:{line}:{target}")

    index = 0
    indexes: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    cycles: list[list[str]] = []

    def visit(node: str) -> None:
        nonlocal index
        indexes[node] = lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for successor in graph.get(node, set()):
            if successor not in indexes:
                visit(successor)
                lowlinks[node] = min(lowlinks[node], lowlinks[successor])
            elif successor in on_stack:
                lowlinks[node] = min(lowlinks[node], indexes[successor])
        if lowlinks[node] == indexes[node]:
            component = []
            while True:
                member = stack.pop()
                on_stack.remove(member)
                component.append(member)
                if member == node:
                    break
            if len(component) > 1:
                cycles.append(sorted(component))

    for module in modules:
        if module not in indexes:
            visit(module)

    assert {"private_cross_layer": private_cross_layer, "cycles": cycles} == {
        "private_cross_layer": [], "cycles": []
    }


def test_composition_root_is_small_and_legacy_service_locators_are_absent() -> None:
    """Catch duplicated application assembly and old runtime service locators."""
    main_path = APP / "main.py"
    tree = ast.parse(main_path.read_text(encoding="utf-8"), filename=str(main_path))
    assignments = [
        node for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "app" for target in node.targets)
    ]
    calls = [node.value.func.id for node in assignments if isinstance(node.value, ast.Call)
             and isinstance(node.value.func, ast.Name)]

    assert len(main_path.read_text(encoding="utf-8").splitlines()) <= 100
    assert calls == ["create_app"]

    forbidden_calls = []
    for path in _python_files(APP):
        forbidden_calls.extend(
            f"{path.relative_to(ROOT)}:{target}"
            for target in _legacy_locator_calls(path, path.read_text(encoding="utf-8"))
        )
    assert forbidden_calls == []
