import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared" / "python"))
sys.path.insert(0, str(ROOT / "services" / "api"))


def test_evaluation_runner_releases_snapshot_before_external_retrieval_and_uses_short_final_uow():
    """Model/search calls cannot hold a database transaction open."""
    from app.runtime.evaluations import EvaluationDependencies, run_evaluation

    events = []

    class Uow:
        cursor = object()

        def __enter__(self):
            events.append("uow:enter")
            return self

        def __exit__(self, *args):
            events.append("uow:exit")

        def commit(self):
            events.append("commit")

    class Repository:
        def __init__(self, cursor):
            pass

        def evaluation_snapshot(self, run_id):
            return {
                "id": run_id, "agent_id": 7, "created_by": 1,
                "config": {}, "cases": [{"id": 3, "question": "年假？", "expected_document_ids": [11]}],
            }

        def mark_evaluation_succeeded(self, run_id, results, metrics):
            events.append("succeeded")

        def mark_evaluation_failed(self, run_id, error_code, results):
            events.append(("failed", error_code))

        def write_audit(self, *args):
            events.append("audit")

    def authorize(user_id, agent_id, for_update=False, uow=None):
        events.append(("authorize", for_update))
        return {"id": user_id}, {"id": agent_id, "knowledge_base_ids": [2]}

    def retrieve(user, agent, question, policy):
        assert events[-1] == "uow:exit"
        events.append("retrieve")
        return {"units": [{"document_id": 11}], "latency_ms": 4.0, "warnings": []}

    run_evaluation({"id": "eval-1"}, EvaluationDependencies(
        uow_factory=Uow, repository_factory=Repository,
        authorize=authorize, retrieve=retrieve,
    ))

    assert events.count("retrieve") == 1
    assert events.count("uow:enter") == 2
    assert events[-4:] == ["succeeded", "audit", "commit", "uow:exit"]


def test_evaluation_failure_persists_only_safe_error_type():
    """Questions, prompts and upstream exception text must not leak to persistence."""
    from app.runtime.evaluations import EvaluationDependencies, run_evaluation

    stored = {}

    class Uow:
        cursor = object()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def commit(self):
            stored["committed"] = True

    class Repository:
        def __init__(self, cursor):
            pass

        def evaluation_snapshot(self, run_id):
            return {"id": run_id, "agent_id": 7, "created_by": 1, "config": {},
                    "cases": [{"id": 1, "question": "SECRET QUESTION", "expected_document_ids": []}]}

        def mark_evaluation_failed(self, run_id, error_code, results):
            stored.update(error_code=error_code, results=results)

        def write_audit(self, user_id, action, resource_type, resource_id, detail, ip_address=None):
            stored["audit"] = detail

    def explode(*args):
        raise RuntimeError("Bearer TOP-SECRET and prompt SECRET QUESTION")

    run_evaluation({"id": "eval-2"}, EvaluationDependencies(
        Uow, Repository, lambda *args, **kwargs: ({"id": 1}, {"id": 7}), explode,
    ))

    rendered = repr(stored)
    assert stored["error_code"] == "RuntimeError"
    assert stored["audit"] == {"run_id": "eval-2", "error_type": "RuntimeError"}
    assert "TOP-SECRET" not in rendered
    assert "SECRET QUESTION" not in rendered
