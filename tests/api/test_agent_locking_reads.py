import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared" / "python"))
sys.path.insert(0, str(ROOT / "services" / "api"))


class Cursor:
    def __init__(self):
        self.statements = []
        self.parameters = []
        self.current = None

    def execute(self, statement, parameters=()):
        normalized = " ".join(statement.split())
        self.statements.append(normalized)
        self.parameters.append(parameters)
        if "FROM document d" in normalized:
            self.current = {"id": 51, "knowledge_base_id": 2}
        elif "FROM document_department_acl" in normalized:
            self.current = [{"department_id": 7}]
        elif "FROM agent " in normalized:
            self.current = {"id": 3, "status": "active"}
        else:
            self.current = []

    def fetchone(self):
        return self.current

    def fetchall(self):
        return self.current


def test_final_authorization_repository_reads_are_locking_current_reads():
    from app.domains.agents.repository import AgentRepository

    cursor = Cursor()
    repository = AgentRepository(cursor)
    repository.get_agent(3, for_update=True)
    repository.agent_knowledge_base_ids(3, for_update=True)
    repository.accessible_knowledge_base_ids(
        {"department_ids": [7], "is_platform_admin": False}, for_update=True
    )
    repository.has_agent_department_access(3, [7], for_update=True)
    repository.bound_tools(3, for_update=True)
    repository.bound_tools_by_ids(3, [31], for_update=True)
    repository.get_session("s", 3, 8, for_update=True)
    repository.get_task("t", 8, for_update=True)

    assert len(cursor.statements) == 8
    assert all(statement.endswith("FOR UPDATE") for statement in cursor.statements)


def test_citation_lock_explicitly_locks_document_and_acl_rows():
    from app.domains.agents.repository import AgentRepository

    cursor = Cursor()
    result = AgentRepository(cursor).citation_document(51, [2], [7], for_update=True)

    assert result == {"id": 51, "knowledge_base_id": 2}
    assert len(cursor.statements) == 2
    assert "FROM document d" in cursor.statements[0]
    assert "FROM document_department_acl" in cursor.statements[1]
    assert all(statement.endswith("FOR UPDATE") for statement in cursor.statements)
