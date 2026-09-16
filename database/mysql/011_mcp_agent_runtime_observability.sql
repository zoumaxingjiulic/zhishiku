-- MCP connectors, per-agent tool grants, tool-call audit and RAG run traces.
-- Apply once after 010_enterprise_agent_platform.sql.

SET NAMES utf8mb4;

ALTER TABLE system_connector
    ADD COLUMN transport_type VARCHAR(32) NOT NULL DEFAULT 'streamable_http' AFTER connector_type,
    ADD COLUMN auth_type VARCHAR(32) NOT NULL DEFAULT 'bearer' AFTER transport_type,
    ADD COLUMN credential_ciphertext TEXT NULL AFTER base_url,
    ADD COLUMN protocol_version VARCHAR(32) NOT NULL DEFAULT '2025-06-18' AFTER credential_ciphertext,
    ADD COLUMN last_checked_at DATETIME(3) NULL AFTER config_json,
    ADD COLUMN last_error VARCHAR(1000) NULL AFTER last_checked_at,
    ADD COLUMN tool_count INT UNSIGNED NOT NULL DEFAULT 0 AFTER last_error;

CREATE TABLE connector_tool (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    connector_id BIGINT UNSIGNED NOT NULL,
    tool_name VARCHAR(128) NOT NULL,
    title VARCHAR(255) NULL,
    description TEXT NULL,
    input_schema_json JSON NOT NULL,
    output_schema_json JSON NULL,
    annotations_json JSON NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    last_discovered_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    UNIQUE KEY uk_connector_tool_name (connector_id, tool_name),
    KEY idx_connector_tool_status (connector_id, status),
    CONSTRAINT fk_connector_tool_connector FOREIGN KEY (connector_id) REFERENCES system_connector (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE agent_connector_tool (
    agent_id BIGINT UNSIGNED NOT NULL,
    connector_tool_id BIGINT UNSIGNED NOT NULL,
    permission VARCHAR(16) NOT NULL DEFAULT 'read',
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (agent_id, connector_tool_id),
    KEY idx_agent_tool_tool (connector_tool_id, agent_id),
    CONSTRAINT fk_agent_tool_agent FOREIGN KEY (agent_id) REFERENCES agent (id) ON DELETE CASCADE,
    CONSTRAINT fk_agent_tool_tool FOREIGN KEY (connector_tool_id) REFERENCES connector_tool (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

ALTER TABLE chat_message
    ADD COLUMN tool_calls_json JSON NULL AFTER citations_json;

CREATE TABLE agent_run (
    id CHAR(36) NOT NULL,
    session_id CHAR(36) NOT NULL,
    agent_id BIGINT UNSIGNED NOT NULL,
    user_id BIGINT UNSIGNED NOT NULL,
    question_hash CHAR(64) NOT NULL,
    route VARCHAR(32) NOT NULL DEFAULT 'rag' COMMENT 'rag|tool|hybrid|chat',
    status VARCHAR(32) NOT NULL DEFAULT 'running' COMMENT 'running|succeeded|failed',
    candidate_counts_json JSON NULL,
    timings_json JSON NULL,
    tool_events_json JSON NULL,
    error_type VARCHAR(128) NULL,
    started_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    finished_at DATETIME(3) NULL,
    PRIMARY KEY (id),
    KEY idx_agent_run_agent_started (agent_id, started_at),
    KEY idx_agent_run_status_started (status, started_at),
    KEY idx_agent_run_session (session_id, started_at),
    CONSTRAINT fk_agent_run_session FOREIGN KEY (session_id) REFERENCES chat_session (id) ON DELETE CASCADE,
    CONSTRAINT fk_agent_run_agent FOREIGN KEY (agent_id) REFERENCES agent (id),
    CONSTRAINT fk_agent_run_user FOREIGN KEY (user_id) REFERENCES app_user (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

INSERT INTO system_connector (code,name,connector_type,transport_type,auth_type,description,base_url,status)
VALUES
    ('ERP_U9','ERP U9 Cloud','erp','streamable_http','bearer','按准确料号查询授权组织内的料品档案。','http://192.168.1.33:18001/mcp','draft'),
    ('OA_LANDRAY','OA 通讯录','oa','streamable_http','bearer','查询 OA 可见通讯录、部门人员和人数。','http://192.168.1.33:3211/mcp','draft')
ON DUPLICATE KEY UPDATE name=VALUES(name),connector_type=VALUES(connector_type),transport_type=VALUES(transport_type),
    auth_type=VALUES(auth_type),description=VALUES(description),base_url=VALUES(base_url);

INSERT INTO agent (code,name,description,agent_type,system_prompt,launch_mode,icon,category,status)
VALUES ('ENTERPRISE_SYSTEM_ASSISTANT','企业系统查询助手','通过已授权的只读工具查询 ERP 料品档案和 OA 通讯录。',
        'tool','你是企业系统查询助手。需要实时业务数据时必须调用已授权工具，不得猜测。ERP 料号必须准确并保留前导零；缺少料号时先询问。OA 通讯录遵守隐私字段与其统计口径。工具没有返回数据时明确说明，不得伪造。',
        'chat','⌘','企业系统','active')
ON DUPLICATE KEY UPDATE name=VALUES(name),description=VALUES(description),agent_type=VALUES(agent_type),
    system_prompt=VALUES(system_prompt),launch_mode=VALUES(launch_mode),icon=VALUES(icon),category=VALUES(category),status='active';

INSERT IGNORE INTO agent_department_acl (agent_id,department_id,permission)
SELECT a.id,d.id,'use' FROM agent a JOIN department d ON d.code='PLATFORM_ADMIN'
WHERE a.code='ENTERPRISE_SYSTEM_ASSISTANT';
