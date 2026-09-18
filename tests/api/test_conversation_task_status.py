import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared" / "python"))
sys.path.insert(0, str(ROOT / "services" / "api"))


class Cursor:
    def __init__(self, *, one=None, many=None):
        self.one = one
        self.many = many or []
        self.statements = []
        self.parameters = []

    def execute(self, statement, parameters=()):
        self.statements.append(" ".join(statement.split()))
        self.parameters.append(parameters)

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.many


def test_list_sessions_projects_latest_owned_task_status_in_one_query():
    from app.domains.agents.repository import AgentRepository

    rows = [{"id": "s-1", "latest_task_status": "failed"}]
    cursor = Cursor(many=rows)

    assert AgentRepository(cursor).list_sessions(7, 8) == rows
    assert len(cursor.statements) == 1
    statement = cursor.statements[0]
    assert "FROM chat_task lt" in statement
    assert "lt.session_id=s.id" in statement
    assert "lt.agent_id=s.agent_id" in statement
    assert "lt.user_id=s.user_id" in statement
    assert "ORDER BY lt.created_at DESC,lt.id DESC LIMIT 1" in statement
    assert "cancel_requested" in statement
    assert cursor.parameters == [(7, 8)]


def test_session_detail_projects_latest_owned_task_status_without_extra_query():
    from app.domains.agents.repository import AgentRepository

    row = {"id": "s-1", "latest_task_status": "cancelled"}
    cursor = Cursor(one=row)

    assert AgentRepository(cursor).get_session("s-1", 7, 8) == row
    assert len(cursor.statements) == 1
    statement = cursor.statements[0]
    assert "FROM chat_task lt" in statement
    assert "lt.session_id=s.id" in statement
    assert "lt.agent_id=s.agent_id" in statement
    assert "lt.user_id=s.user_id" in statement
    assert cursor.parameters == [("s-1", 7, 8)]


def test_conversation_service_returns_latest_status_in_detail():
    from app.domains.agents.service import AgentService

    class Repository:
        def get_session(self, session_id, agent_id, user_id, for_update=False):
            return {"id": session_id, "title": "测试", "latest_task_status": "failed"}

        def list_messages(self, session_id):
            return []

    service = object.__new__(AgentService)
    service.repository = Repository()
    service._chat_agent = lambda user, agent_id: {"id": agent_id}

    detail = service.get_conversation({"id": 8}, 7, "s-1")

    assert detail["latest_task_status"] == "failed"
