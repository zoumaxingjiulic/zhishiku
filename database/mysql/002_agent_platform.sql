-- Platform schema migration. Apply once after 001_initial_schema.sql.

CREATE TABLE knowledge_base (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    code VARCHAR(64) NOT NULL,
    name VARCHAR(128) NOT NULL,
    description TEXT NULL,
    owner_department_id BIGINT UNSIGNED NOT NULL,
    security_level VARCHAR(32) NOT NULL DEFAULT 'internal',
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    created_by BIGINT UNSIGNED NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    UNIQUE KEY uk_knowledge_base_code (code),
    KEY idx_knowledge_base_owner_status (owner_department_id, status),
    CONSTRAINT fk_knowledge_base_owner_department FOREIGN KEY (owner_department_id) REFERENCES department (id),
    CONSTRAINT fk_knowledge_base_created_by FOREIGN KEY (created_by) REFERENCES app_user (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE knowledge_base_department_acl (
    knowledge_base_id BIGINT UNSIGNED NOT NULL,
    department_id BIGINT UNSIGNED NOT NULL,
    permission VARCHAR(16) NOT NULL DEFAULT 'read',
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (knowledge_base_id, department_id),
    KEY idx_kb_acl_department (department_id, knowledge_base_id),
    CONSTRAINT fk_kb_acl_knowledge_base FOREIGN KEY (knowledge_base_id) REFERENCES knowledge_base (id) ON DELETE CASCADE,
    CONSTRAINT fk_kb_acl_department FOREIGN KEY (department_id) REFERENCES department (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE app_role (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    code VARCHAR(64) NOT NULL,
    name VARCHAR(128) NOT NULL,
    description VARCHAR(512) NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    UNIQUE KEY uk_app_role_code (code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE user_role (
    user_id BIGINT UNSIGNED NOT NULL,
    role_id BIGINT UNSIGNED NOT NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (user_id, role_id),
    CONSTRAINT fk_user_role_user FOREIGN KEY (user_id) REFERENCES app_user (id) ON DELETE CASCADE,
    CONSTRAINT fk_user_role_role FOREIGN KEY (role_id) REFERENCES app_role (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE agent (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    code VARCHAR(64) NOT NULL,
    name VARCHAR(128) NOT NULL,
    description TEXT NULL,
    agent_type VARCHAR(32) NOT NULL DEFAULT 'rag',
    system_prompt MEDIUMTEXT NOT NULL,
    llm_model VARCHAR(255) NULL,
    settings_json JSON NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    created_by BIGINT UNSIGNED NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    UNIQUE KEY uk_agent_code (code),
    CONSTRAINT fk_agent_created_by FOREIGN KEY (created_by) REFERENCES app_user (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE agent_knowledge_base (
    agent_id BIGINT UNSIGNED NOT NULL,
    knowledge_base_id BIGINT UNSIGNED NOT NULL,
    retrieval_config_json JSON NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (agent_id, knowledge_base_id),
    CONSTRAINT fk_agent_kb_agent FOREIGN KEY (agent_id) REFERENCES agent (id) ON DELETE CASCADE,
    CONSTRAINT fk_agent_kb_knowledge_base FOREIGN KEY (knowledge_base_id) REFERENCES knowledge_base (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE chat_session (
    id CHAR(36) NOT NULL,
    agent_id BIGINT UNSIGNED NOT NULL,
    user_id BIGINT UNSIGNED NULL,
    title VARCHAR(512) NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    KEY idx_chat_session_agent_user (agent_id, user_id, updated_at),
    CONSTRAINT fk_chat_session_agent FOREIGN KEY (agent_id) REFERENCES agent (id),
    CONSTRAINT fk_chat_session_user FOREIGN KEY (user_id) REFERENCES app_user (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE chat_message (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    session_id CHAR(36) NOT NULL,
    role VARCHAR(16) NOT NULL,
    content MEDIUMTEXT NOT NULL,
    citations_json JSON NULL,
    model_name VARCHAR(255) NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    KEY idx_chat_message_session_created (session_id, created_at),
    CONSTRAINT fk_chat_message_session FOREIGN KEY (session_id) REFERENCES chat_session (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Seed the first knowledge base and agent. Department 1 is the company root.
INSERT INTO knowledge_base (id, code, name, description, owner_department_id)
VALUES (1, 'HR_POLICY', '人资制度知识库', '人资部门制度、流程与员工手册试点资料', 1)
ON DUPLICATE KEY UPDATE name = VALUES(name), description = VALUES(description);

INSERT INTO knowledge_base_department_acl (knowledge_base_id, department_id, permission)
VALUES (1, 1, 'manage')
ON DUPLICATE KEY UPDATE permission = VALUES(permission);

ALTER TABLE document ADD COLUMN knowledge_base_id BIGINT UNSIGNED NULL AFTER id;
UPDATE document SET knowledge_base_id = 1 WHERE knowledge_base_id IS NULL;
ALTER TABLE document
    MODIFY COLUMN knowledge_base_id BIGINT UNSIGNED NOT NULL,
    ADD KEY idx_document_knowledge_base_status (knowledge_base_id, status),
    ADD CONSTRAINT fk_document_knowledge_base FOREIGN KEY (knowledge_base_id) REFERENCES knowledge_base (id);

INSERT INTO app_role (code, name, description) VALUES
    ('platform_admin', '平台管理员', '管理用户、智能体和全局配置'),
    ('knowledge_base_admin', '知识库管理员', '管理授权范围内的知识库和文档'),
    ('employee', '员工', '在授权范围内使用智能体问答')
ON DUPLICATE KEY UPDATE name = VALUES(name), description = VALUES(description);

INSERT INTO agent (id, code, name, description, agent_type, system_prompt) VALUES
    (1, 'HR_POLICY_ASSISTANT', '人资制度问答助手', '仅根据已授权的人资制度资料回答问题，并返回引用来源。', 'rag',
     '你是企业人资制度问答助手。仅依据检索到的授权资料回答；必须附上来源。资料不足时明确说明无法从现有资料确认，不得编造。')
ON DUPLICATE KEY UPDATE name = VALUES(name), description = VALUES(description), system_prompt = VALUES(system_prompt);

INSERT INTO agent_knowledge_base (agent_id, knowledge_base_id, retrieval_config_json)
VALUES (1, 1, JSON_OBJECT('top_k', 8, 'candidate_k', 40, 'rerank_enabled', true))
ON DUPLICATE KEY UPDATE retrieval_config_json = VALUES(retrieval_config_json);

