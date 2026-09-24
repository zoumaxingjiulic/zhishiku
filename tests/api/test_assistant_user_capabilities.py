"""Enterprise-assistant capability boundary over permanent user grants."""

import sqlite3
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared" / "python"))
sys.path.insert(0, str(ROOT / "services" / "api"))


USER = {"id": 8, "department_ids": [2], "is_platform_admin": False}


def database_and_cursor():
    database = sqlite3.connect(":memory:")
    database.row_factory = sqlite3.Row
    database.executescript(
        """
        CREATE TABLE app_user(id,status,deleted_at);
        INSERT INTO app_user VALUES(8,1,NULL);
        CREATE TABLE user_department(user_id,department_id,is_primary);
        INSERT INTO user_department VALUES(8,2,1);
                CREATE TABLE knowledge_base(id,code,name,description,status);
        INSERT INTO knowledge_base VALUES
          (1,'HR','人资知识库','HR','active'),
          (2,'TECH','技术知识库','TECH','active'),
          (3,'OLD','旧知识库','OLD','archived');
        CREATE TABLE department(id,code,name,status);
        INSERT INTO department VALUES(2,'HR','人资部',1),(3,'TECH','技术部',1);
        CREATE TABLE knowledge_base_department_acl(knowledge_base_id,department_id,permission);
        INSERT INTO knowledge_base_department_acl VALUES(1,2,'manage'),(2,3,'manage');
        CREATE TABLE user_knowledge_base_acl(user_id,knowledge_base_id,permission);
        INSERT INTO user_knowledge_base_acl VALUES(8,2,'read'),(8,3,'read');

        CREATE TABLE system_connector(id,code,name,status);
        INSERT INTO system_connector VALUES(4,'ERP','ERP','active');
        CREATE TABLE connector_tool(id,connector_id,tool_name,title,description,input_schema_json,annotations_json,status);
        INSERT INTO connector_tool VALUES
          (11,4,'stock','库存查询','查询库存','{}','{"readOnlyHint":true}','active'),
          (12,4,'write_stock','库存写入','写入库存','{}','{"readOnlyHint":false}','active');
        CREATE TABLE user_connector_tool_acl(user_id,connector_tool_id,permission);
        INSERT INTO user_connector_tool_acl VALUES(8,11,'use'),(8,12,'use');

        CREATE TABLE agent(id,code,name,description,status,launch_mode);
        INSERT INTO agent VALUES(7,'HR_AGENT','人资助手','HR','active','chat');
        CREATE TABLE user_agent_acl(user_id,agent_id,permission);
        INSERT INTO user_agent_acl VALUES(8,7,'use');

        CREATE TABLE assistant_skill(id,code,name,description,status);
        INSERT INTO assistant_skill VALUES
          (21,'SAFE','安全技能','safe','active'),
          (22,'AGENT_BOUND','智能体依赖','agent','active'),
          (23,'UNAUTHORIZED_TOOL','未授权工具','tool','active'),
          (24,'DIRECT_KB','直授知识库','kb','active');
        CREATE TABLE assistant_skill_department(skill_id,department_id);
        INSERT INTO assistant_skill_department VALUES(21,2),(22,2),(23,2),(24,2);
        CREATE TABLE assistant_skill_knowledge_base(skill_id,knowledge_base_id);
        INSERT INTO assistant_skill_knowledge_base VALUES(21,1),(24,2);
        CREATE TABLE assistant_skill_tool(skill_id,connector_tool_id);
        INSERT INTO assistant_skill_tool VALUES(21,11),(23,12);
        CREATE TABLE assistant_skill_agent(skill_id,agent_id);
        INSERT INTO assistant_skill_agent VALUES(22,7);
        """
    )

    class Cursor:
        def execute(self, statement, parameters=None):
            self.result = database.execute(statement.replace("%s", "?"), parameters or ())

        def fetchall(self):
            return [dict(row) for row in self.result.fetchall()]

        def fetchone(self):
            row = self.result.fetchone()
            return dict(row) if row else None

    return database, Cursor()


def test_enterprise_assistant_uses_department_and_direct_kbs_direct_readonly_tools_only():
    from app.domains.assistant.capabilities import CapabilityCatalog
    from app.domains.assistant.repository import AssistantRepository

    database, cursor = database_and_cursor()
    try:
        snapshot = CapabilityCatalog(AssistantRepository(cursor)).for_user(USER)
    finally:
        database.close()

    assert [item.id for item in snapshot.knowledge_bases] == [1, 2]
    assert [item.id for item in snapshot.tools] == [11]
    assert snapshot.agents == ()
    assert [item.id for item in snapshot.skills] == [21, 24]
    assert snapshot.tool_authority == {}
    assert snapshot.tool_authority_agent_ids == ()


def test_agent_task_intent_is_never_routed_by_enterprise_assistant():
    from app.domains.assistant.intent import IntentRouter
    from app.domains.assistant.schemas import CapabilityCatalogSnapshot

    class Model:
        def decide(self, **kwargs):
            return {
                "intent_type": "agent_task",
                "confidence": 0.99,
                "selection": {"agent_ids": [7]},
                "reason": "delegate",
            }

    decision = IntentRouter().route("让人资助手处理", CapabilityCatalogSnapshot(), model=Model())

    assert decision.intent_type == "clarification"
    assert decision.selection.agent_ids == []


def test_direct_tool_revocation_fails_execution_time_revalidation():
    from app.core.errors import AuthorizationError
    from app.domains.assistant.capabilities import CapabilityCatalog
    from app.domains.assistant.repository import AssistantRepository
    from app.domains.assistant.schemas import CapabilitySelection

    database, cursor = database_and_cursor()
    try:
        repository = AssistantRepository(cursor)
        catalog = CapabilityCatalog(repository)
        snapshot = catalog.for_user(USER)
        database.execute("DELETE FROM user_connector_tool_acl WHERE user_id=8 AND connector_tool_id=11")
        with pytest.raises(AuthorizationError):
            catalog.validate_selection(USER, snapshot, CapabilitySelection(tool_ids=[11]))
    finally:
        database.close()