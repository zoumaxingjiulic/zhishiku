-- Enterprise agent platform foundations: model gateway, personal prompts,
-- agent requests, explicit agent ACLs and non-chat launch metadata.
-- Apply once after 009_knowledge_folders.sql.

SET NAMES utf8mb4;

CREATE TABLE llm_gateway_profile (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    code VARCHAR(64) NOT NULL,
    name VARCHAR(128) NOT NULL,
    provider_type VARCHAR(32) NOT NULL COMMENT 'openai|azure_openai|deepseek|qwen|ollama|custom',
    base_url VARCHAR(1024) NOT NULL,
    api_key_ciphertext TEXT NULL COMMENT 'Fernet encrypted; never returned by API',
    model_name VARCHAR(255) NOT NULL,
    capabilities_json JSON NULL,
    config_json JSON NULL COMMENT 'Non-secret provider options only',
    status VARCHAR(32) NOT NULL DEFAULT 'active' COMMENT 'active|disabled',
    created_by BIGINT UNSIGNED NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    UNIQUE KEY uk_llm_gateway_profile_code (code),
    KEY idx_llm_gateway_status (status),
    CONSTRAINT fk_llm_gateway_created_by FOREIGN KEY (created_by) REFERENCES app_user (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

ALTER TABLE agent
    ADD COLUMN llm_gateway_profile_id BIGINT UNSIGNED NULL AFTER llm_model,
    ADD COLUMN launch_mode VARCHAR(32) NOT NULL DEFAULT 'chat' COMMENT 'chat|form|workflow|dashboard|external' AFTER agent_type,
    ADD COLUMN icon VARCHAR(32) NULL AFTER launch_mode,
    ADD COLUMN category VARCHAR(64) NULL AFTER icon,
    ADD CONSTRAINT fk_agent_llm_gateway FOREIGN KEY (llm_gateway_profile_id) REFERENCES llm_gateway_profile (id);

CREATE TABLE agent_department_acl (
    agent_id BIGINT UNSIGNED NOT NULL,
    department_id BIGINT UNSIGNED NOT NULL,
    permission VARCHAR(16) NOT NULL DEFAULT 'use',
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (agent_id, department_id),
    KEY idx_agent_acl_department (department_id, agent_id),
    CONSTRAINT fk_agent_acl_agent FOREIGN KEY (agent_id) REFERENCES agent (id) ON DELETE CASCADE,
    CONSTRAINT fk_agent_acl_department FOREIGN KEY (department_id) REFERENCES department (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE prompt_template (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    owner_user_id BIGINT UNSIGNED NOT NULL,
    name VARCHAR(128) NOT NULL,
    description VARCHAR(512) NULL,
    content MEDIUMTEXT NOT NULL,
    variables_json JSON NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    KEY idx_prompt_owner_updated (owner_user_id, updated_at),
    CONSTRAINT fk_prompt_owner FOREIGN KEY (owner_user_id) REFERENCES app_user (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE agent_request (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    request_no VARCHAR(32) NOT NULL,
    applicant_user_id BIGINT UNSIGNED NOT NULL,
    department_id BIGINT UNSIGNED NOT NULL,
    title VARCHAR(128) NOT NULL,
    business_problem TEXT NOT NULL,
    expected_outcome TEXT NOT NULL,
    data_sources_json JSON NULL,
    frequency VARCHAR(32) NULL,
    urgency VARCHAR(16) NOT NULL DEFAULT 'normal',
    status VARCHAR(32) NOT NULL DEFAULT 'submitted' COMMENT 'submitted|reviewing|approved|rejected|delivered|closed',
    admin_comment TEXT NULL,
    reviewed_by BIGINT UNSIGNED NULL,
    reviewed_at DATETIME(3) NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    UNIQUE KEY uk_agent_request_no (request_no),
    KEY idx_agent_request_applicant (applicant_user_id, created_at),
    KEY idx_agent_request_status (status, created_at),
    CONSTRAINT fk_agent_request_applicant FOREIGN KEY (applicant_user_id) REFERENCES app_user (id),
    CONSTRAINT fk_agent_request_department FOREIGN KEY (department_id) REFERENCES department (id),
    CONSTRAINT fk_agent_request_reviewer FOREIGN KEY (reviewed_by) REFERENCES app_user (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Preserve current behavior for existing RAG agents through explicit department grants.
INSERT IGNORE INTO agent_department_acl (agent_id, department_id, permission)
SELECT DISTINCT ak.agent_id, acl.department_id, 'use'
FROM agent_knowledge_base ak
JOIN knowledge_base_department_acl acl ON acl.knowledge_base_id=ak.knowledge_base_id;
