-- Direct per-user capability grants and one-time migration of legacy
-- department-level agent distribution. Apply once after
-- 013_enterprise_assistant.sql.
--
-- This migration only adds authorization metadata. It does not alter, rebuild,
-- or delete knowledge bases, documents, chunks, vectors, or full-text indexes.

SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS user_knowledge_base_acl (
    user_id BIGINT UNSIGNED NOT NULL,
    knowledge_base_id BIGINT UNSIGNED NOT NULL,
    permission VARCHAR(16) NOT NULL DEFAULT 'read' COMMENT 'read|manage',
    granted_by BIGINT UNSIGNED NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (user_id, knowledge_base_id),
    KEY idx_user_kb_acl_knowledge_base (knowledge_base_id, user_id),
    KEY idx_user_kb_acl_granted_by (granted_by),
    CONSTRAINT fk_user_kb_acl_user FOREIGN KEY (user_id) REFERENCES app_user (id) ON DELETE CASCADE,
    CONSTRAINT fk_user_kb_acl_knowledge_base FOREIGN KEY (knowledge_base_id) REFERENCES knowledge_base (id) ON DELETE CASCADE,
    CONSTRAINT fk_user_kb_acl_granted_by FOREIGN KEY (granted_by) REFERENCES app_user (id) ON DELETE SET NULL,
    CONSTRAINT chk_user_kb_acl_permission CHECK (permission IN ('read', 'manage'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS user_connector_tool_acl (
    user_id BIGINT UNSIGNED NOT NULL,
    connector_tool_id BIGINT UNSIGNED NOT NULL,
    permission VARCHAR(16) NOT NULL DEFAULT 'use' COMMENT 'use',
    granted_by BIGINT UNSIGNED NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (user_id, connector_tool_id),
    KEY idx_user_tool_acl_tool (connector_tool_id, user_id),
    KEY idx_user_tool_acl_granted_by (granted_by),
    CONSTRAINT fk_user_tool_acl_user FOREIGN KEY (user_id) REFERENCES app_user (id) ON DELETE CASCADE,
    CONSTRAINT fk_user_tool_acl_tool FOREIGN KEY (connector_tool_id) REFERENCES connector_tool (id) ON DELETE CASCADE,
    CONSTRAINT fk_user_tool_acl_granted_by FOREIGN KEY (granted_by) REFERENCES app_user (id) ON DELETE SET NULL,
    CONSTRAINT chk_user_tool_acl_permission CHECK (permission IN ('use'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS user_agent_acl (
    user_id BIGINT UNSIGNED NOT NULL,
    agent_id BIGINT UNSIGNED NOT NULL,
    permission VARCHAR(16) NOT NULL DEFAULT 'use' COMMENT 'use',
    granted_by BIGINT UNSIGNED NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (user_id, agent_id),
    KEY idx_user_agent_acl_agent (agent_id, user_id),
    KEY idx_user_agent_acl_granted_by (granted_by),
    CONSTRAINT fk_user_agent_acl_user FOREIGN KEY (user_id) REFERENCES app_user (id) ON DELETE CASCADE,
    CONSTRAINT fk_user_agent_acl_agent FOREIGN KEY (agent_id) REFERENCES agent (id) ON DELETE CASCADE,
    CONSTRAINT fk_user_agent_acl_granted_by FOREIGN KEY (granted_by) REFERENCES app_user (id) ON DELETE SET NULL,
    CONSTRAINT chk_user_agent_acl_permission CHECK (permission IN ('use'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Preserve who can use each existing agent by materializing memberships of
-- active departments as direct user grants. The legacy table is retained only
-- as an audit/backfill source for one compatibility window; new application
-- code no longer reads or writes it and it is not a current rollback source.
INSERT IGNORE INTO user_agent_acl (user_id, agent_id, permission, created_at)
SELECT
    ud.user_id,
    ada.agent_id,
    'use',
    MIN(ada.created_at)
FROM agent_department_acl ada
JOIN agent a ON a.id = ada.agent_id AND a.status = 'active'
JOIN department d ON d.id = ada.department_id AND d.status = 1
JOIN user_department ud ON ud.department_id = ada.department_id
JOIN app_user u ON u.id = ud.user_id AND u.status = 1 AND u.deleted_at IS NULL
WHERE ada.permission = 'use'
GROUP BY ud.user_id, ada.agent_id;
