-- Enterprise assistant intent decisions and reusable skill catalog.
-- Apply once after 012_platform_quality_runtime.sql. This migration only adds
-- assistant-owned tables and an idempotent reserved agent record.

SET NAMES utf8mb4;

CREATE TABLE assistant_intent_decision (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 task_id CHAR(36) NOT NULL,
 user_id BIGINT UNSIGNED NOT NULL,
 intent_type VARCHAR(32) NOT NULL,
 confidence DECIMAL(5,4) NOT NULL,
 capability_snapshot JSON NOT NULL,
 decision_json JSON NOT NULL,
 created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
 UNIQUE KEY uk_assistant_intent_task(task_id),
 FOREIGN KEY(task_id) REFERENCES chat_task(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE assistant_skill (
 id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
 code VARCHAR(64) NOT NULL UNIQUE,
 name VARCHAR(128) NOT NULL,
 description VARCHAR(1000) NULL,
 instruction MEDIUMTEXT NOT NULL,
 input_schema_json JSON NOT NULL,
 trigger_examples_json JSON NOT NULL,
 version INT NOT NULL DEFAULT 1,
 status VARCHAR(20) NOT NULL DEFAULT 'active',
 created_by BIGINT UNSIGNED NOT NULL,
 created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
 updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE assistant_skill_department (
 skill_id BIGINT UNSIGNED NOT NULL,
 department_id BIGINT UNSIGNED NOT NULL,
 PRIMARY KEY(skill_id, department_id),
 FOREIGN KEY(skill_id) REFERENCES assistant_skill(id) ON DELETE CASCADE,
 FOREIGN KEY(department_id) REFERENCES department(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE assistant_skill_knowledge_base (
 skill_id BIGINT UNSIGNED NOT NULL,
 knowledge_base_id BIGINT UNSIGNED NOT NULL,
 PRIMARY KEY(skill_id, knowledge_base_id),
 FOREIGN KEY(skill_id) REFERENCES assistant_skill(id) ON DELETE CASCADE,
 FOREIGN KEY(knowledge_base_id) REFERENCES knowledge_base(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE assistant_skill_tool (
 skill_id BIGINT UNSIGNED NOT NULL,
 connector_tool_id BIGINT UNSIGNED NOT NULL,
 PRIMARY KEY(skill_id, connector_tool_id),
 FOREIGN KEY(skill_id) REFERENCES assistant_skill(id) ON DELETE CASCADE,
 FOREIGN KEY(connector_tool_id) REFERENCES connector_tool(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE assistant_skill_agent (
 skill_id BIGINT UNSIGNED NOT NULL,
 agent_id BIGINT UNSIGNED NOT NULL,
 PRIMARY KEY(skill_id, agent_id),
 FOREIGN KEY(skill_id) REFERENCES assistant_skill(id) ON DELETE CASCADE,
 FOREIGN KEY(agent_id) REFERENCES agent(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO agent (code, name, description, agent_type, system_prompt, launch_mode, icon, category, status)
VALUES (
 'ENTERPRISE_ASSISTANT',
 '企业总助手',
 '统一识别员工意图，并在当前用户授权范围内调度知识、只读工具、Skill 和专业智能体。',
 'orchestrator',
 '你是企业总助手。仅在当前用户授权范围内选择知识、只读工具、Skill 或专业智能体；保留能力快照和意图决策。资料或权限不足时明确说明，不得编造企业事实。',
 'chat',
 '✦',
 '企业助手',
 'active'
)
ON DUPLICATE KEY UPDATE
 name=VALUES(name),
 description=VALUES(description),
 agent_type=VALUES(agent_type),
 system_prompt=VALUES(system_prompt),
 launch_mode=VALUES(launch_mode),
 icon=VALUES(icon),
 category=VALUES(category),
 status='active';
