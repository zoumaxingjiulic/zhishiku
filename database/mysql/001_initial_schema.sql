-- Enterprise knowledge base MVP: initial metadata schema.
-- This file runs only when the MySQL data directory is empty.

CREATE TABLE IF NOT EXISTS department (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    code VARCHAR(64) NOT NULL,
    name VARCHAR(128) NOT NULL,
    parent_id BIGINT UNSIGNED NULL,
    status TINYINT UNSIGNED NOT NULL DEFAULT 1,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    UNIQUE KEY uk_department_code (code),
    CONSTRAINT fk_department_parent FOREIGN KEY (parent_id) REFERENCES department (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS app_user (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    external_id VARCHAR(128) NOT NULL COMMENT 'SSO/HR system user identifier',
    username VARCHAR(128) NOT NULL,
    display_name VARCHAR(128) NOT NULL,
    email VARCHAR(255) NULL,
    status TINYINT UNSIGNED NOT NULL DEFAULT 1,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    UNIQUE KEY uk_app_user_external_id (external_id),
    UNIQUE KEY uk_app_user_username (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS user_department (
    user_id BIGINT UNSIGNED NOT NULL,
    department_id BIGINT UNSIGNED NOT NULL,
    is_primary TINYINT(1) NOT NULL DEFAULT 0,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (user_id, department_id),
    KEY idx_user_department_department (department_id),
    CONSTRAINT fk_user_department_user FOREIGN KEY (user_id) REFERENCES app_user (id) ON DELETE CASCADE,
    CONSTRAINT fk_user_department_department FOREIGN KEY (department_id) REFERENCES department (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS document (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    document_no VARCHAR(128) NULL COMMENT 'Business document number',
    title VARCHAR(512) NOT NULL,
    source_type VARCHAR(32) NOT NULL DEFAULT 'upload' COMMENT 'upload|sync',
    mime_type VARCHAR(255) NOT NULL,
    file_extension VARCHAR(32) NULL,
    owner_department_id BIGINT UNSIGNED NOT NULL,
    security_level VARCHAR(32) NOT NULL DEFAULT 'internal' COMMENT 'public|internal|confidential|secret',
    status VARCHAR(32) NOT NULL DEFAULT 'active' COMMENT 'active|archived|deleted',
    current_version_no INT UNSIGNED NOT NULL DEFAULT 0,
    created_by BIGINT UNSIGNED NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    deleted_at DATETIME(3) NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_document_no (document_no),
    KEY idx_document_owner_status (owner_department_id, status),
    KEY idx_document_title (title(191)),
    CONSTRAINT fk_document_owner_department FOREIGN KEY (owner_department_id) REFERENCES department (id),
    CONSTRAINT fk_document_created_by FOREIGN KEY (created_by) REFERENCES app_user (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS document_version (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    document_id BIGINT UNSIGNED NOT NULL,
    version_no INT UNSIGNED NOT NULL,
    original_filename VARCHAR(512) NOT NULL,
    object_key VARCHAR(1024) NOT NULL COMMENT 'MinIO object key; never a local filesystem path',
    object_etag VARCHAR(128) NULL,
    sha256 CHAR(64) NOT NULL,
    file_size_bytes BIGINT UNSIGNED NOT NULL,
    parser_name VARCHAR(128) NULL,
    parser_version VARCHAR(64) NULL,
    extraction_status VARCHAR(32) NOT NULL DEFAULT 'pending' COMMENT 'pending|processing|succeeded|failed',
    extracted_at DATETIME(3) NULL,
    created_by BIGINT UNSIGNED NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    UNIQUE KEY uk_document_version (document_id, version_no),
    UNIQUE KEY uk_document_version_sha256 (document_id, sha256),
    KEY idx_document_version_status (extraction_status, created_at),
    CONSTRAINT fk_document_version_document FOREIGN KEY (document_id) REFERENCES document (id) ON DELETE CASCADE,
    CONSTRAINT fk_document_version_created_by FOREIGN KEY (created_by) REFERENCES app_user (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS document_department_acl (
    document_id BIGINT UNSIGNED NOT NULL,
    department_id BIGINT UNSIGNED NOT NULL,
    permission VARCHAR(16) NOT NULL DEFAULT 'read' COMMENT 'read|manage',
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (document_id, department_id),
    KEY idx_document_acl_department (department_id, document_id),
    CONSTRAINT fk_document_acl_document FOREIGN KEY (document_id) REFERENCES document (id) ON DELETE CASCADE,
    CONSTRAINT fk_document_acl_department FOREIGN KEY (department_id) REFERENCES department (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS document_asset (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    document_version_id BIGINT UNSIGNED NOT NULL,
    asset_type VARCHAR(32) NOT NULL COMMENT 'original|extracted_text|page_image|preview|ocr_json',
    object_key VARCHAR(1024) NOT NULL,
    mime_type VARCHAR(255) NOT NULL,
    page_no INT UNSIGNED NULL,
    metadata_json JSON NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    UNIQUE KEY uk_document_asset (document_version_id, asset_type, object_key(191)),
    KEY idx_document_asset_version_type (document_version_id, asset_type),
    CONSTRAINT fk_document_asset_version FOREIGN KEY (document_version_id) REFERENCES document_version (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS content_unit (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    document_version_id BIGINT UNSIGNED NOT NULL,
    unit_type VARCHAR(32) NOT NULL DEFAULT 'chunk' COMMENT 'chunk|table|image_caption|drawing_region',
    sequence_no INT UNSIGNED NOT NULL,
    page_start INT UNSIGNED NULL,
    page_end INT UNSIGNED NULL,
    content_text MEDIUMTEXT NOT NULL,
    content_hash CHAR(64) NOT NULL,
    token_count INT UNSIGNED NULL,
    metadata_json JSON NULL,
    vector_status VARCHAR(32) NOT NULL DEFAULT 'pending' COMMENT 'pending|indexed|failed|deleted',
    fulltext_status VARCHAR(32) NOT NULL DEFAULT 'pending' COMMENT 'pending|indexed|failed|deleted',
    milvus_pk VARCHAR(128) NULL,
    opensearch_id VARCHAR(128) NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    UNIQUE KEY uk_content_unit_sequence (document_version_id, unit_type, sequence_no),
    UNIQUE KEY uk_content_unit_hash (document_version_id, content_hash),
    KEY idx_content_unit_vector_status (vector_status, updated_at),
    KEY idx_content_unit_fulltext_status (fulltext_status, updated_at),
    CONSTRAINT fk_content_unit_version FOREIGN KEY (document_version_id) REFERENCES document_version (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS ingestion_job (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    document_version_id BIGINT UNSIGNED NOT NULL,
    job_type VARCHAR(32) NOT NULL COMMENT 'extract|ocr|chunk|embed|index|delete',
    status VARCHAR(32) NOT NULL DEFAULT 'queued' COMMENT 'queued|running|succeeded|failed|cancelled',
    attempt_count INT UNSIGNED NOT NULL DEFAULT 0,
    idempotency_key VARCHAR(191) NOT NULL,
    payload_json JSON NULL,
    error_message TEXT NULL,
    started_at DATETIME(3) NULL,
    finished_at DATETIME(3) NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    UNIQUE KEY uk_ingestion_job_idempotency (idempotency_key),
    KEY idx_ingestion_job_status (status, job_type, created_at),
    CONSTRAINT fk_ingestion_job_version FOREIGN KEY (document_version_id) REFERENCES document_version (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

INSERT INTO department (id, code, name, parent_id)
VALUES (1, 'COMPANY', '公司', NULL)
ON DUPLICATE KEY UPDATE name = VALUES(name);

