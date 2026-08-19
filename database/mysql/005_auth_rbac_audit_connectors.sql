-- Authentication, authorization, audit, and connector foundations.
-- Apply once after 004_fix_platform_seed_encoding_binary.sql.

SET NAMES utf8mb4;

ALTER TABLE app_user
    ADD COLUMN password_hash VARCHAR(255) NULL AFTER email,
    ADD COLUMN password_changed_at DATETIME(3) NULL AFTER password_hash,
    ADD COLUMN last_login_at DATETIME(3) NULL AFTER password_changed_at,
    ADD COLUMN created_by BIGINT UNSIGNED NULL AFTER status,
    ADD KEY idx_app_user_status (status),
    ADD CONSTRAINT fk_app_user_created_by FOREIGN KEY (created_by) REFERENCES app_user (id);

CREATE TABLE audit_log (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    user_id BIGINT UNSIGNED NULL,
    action VARCHAR(64) NOT NULL,
    resource_type VARCHAR(64) NOT NULL,
    resource_id VARCHAR(128) NULL,
    detail_json JSON NULL,
    ip_address VARCHAR(64) NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    KEY idx_audit_user_created (user_id, created_at),
    KEY idx_audit_resource_created (resource_type, resource_id, created_at),
    CONSTRAINT fk_audit_user FOREIGN KEY (user_id) REFERENCES app_user (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE system_connector (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    code VARCHAR(64) NOT NULL,
    name VARCHAR(128) NOT NULL,
    connector_type VARCHAR(32) NOT NULL COMMENT 'erp|plm|mom|custom',
    description TEXT NULL,
    base_url VARCHAR(1024) NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'draft' COMMENT 'draft|active|disabled',
    config_json JSON NULL COMMENT 'Non-secret configuration only',
    created_by BIGINT UNSIGNED NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    UNIQUE KEY uk_system_connector_code (code),
    CONSTRAINT fk_connector_created_by FOREIGN KEY (created_by) REFERENCES app_user (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

INSERT INTO department (code, name, parent_id)
SELECT 'HR', '人力资源部', 1
WHERE NOT EXISTS (SELECT 1 FROM department WHERE code='HR');

UPDATE knowledge_base
SET owner_department_id=(SELECT id FROM department WHERE code='HR')
WHERE id=1;

DELETE FROM knowledge_base_department_acl WHERE knowledge_base_id=1;
INSERT INTO knowledge_base_department_acl (knowledge_base_id, department_id, permission)
SELECT 1, id, 'manage' FROM department WHERE code='HR';

UPDATE document
SET owner_department_id=(SELECT id FROM department WHERE code='HR')
WHERE knowledge_base_id=1;

DELETE acl
FROM document_department_acl acl
JOIN document d ON d.id=acl.document_id
WHERE d.knowledge_base_id=1;

INSERT INTO document_department_acl (document_id, department_id, permission)
SELECT d.id, dep.id, 'manage'
FROM document d
JOIN department dep ON dep.code='HR'
WHERE d.knowledge_base_id=1
ON DUPLICATE KEY UPDATE permission=VALUES(permission);

