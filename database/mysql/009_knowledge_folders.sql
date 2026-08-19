-- Production-oriented virtual folder tree for knowledge-base documents.

SET NAMES utf8mb4;

CREATE TABLE knowledge_folder (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    knowledge_base_id BIGINT UNSIGNED NOT NULL,
    parent_id BIGINT UNSIGNED NULL,
    name VARCHAR(128) NOT NULL,
    sort_order INT NOT NULL DEFAULT 0,
    row_version INT UNSIGNED NOT NULL DEFAULT 1,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    created_by BIGINT UNSIGNED NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    deleted_at DATETIME(3) NULL,
    parent_scope BIGINT UNSIGNED GENERATED ALWAYS AS (IFNULL(parent_id,0)) STORED,
    active_name VARCHAR(128) GENERATED ALWAYS AS (IF(deleted_at IS NULL,name,NULL)) STORED,
    PRIMARY KEY (id),
    UNIQUE KEY uk_folder_active_sibling (knowledge_base_id,parent_scope,active_name),
    KEY idx_folder_tree (knowledge_base_id,parent_id,status,sort_order,id),
    CONSTRAINT fk_folder_knowledge_base FOREIGN KEY (knowledge_base_id) REFERENCES knowledge_base (id) ON DELETE CASCADE,
    CONSTRAINT fk_folder_parent FOREIGN KEY (parent_id) REFERENCES knowledge_folder (id),
    CONSTRAINT fk_folder_created_by FOREIGN KEY (created_by) REFERENCES app_user (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

ALTER TABLE document
    ADD COLUMN folder_id BIGINT UNSIGNED NULL AFTER knowledge_base_id,
    ADD COLUMN row_version INT UNSIGNED NOT NULL DEFAULT 1 AFTER current_version_no,
    ADD KEY idx_document_kb_folder_status (knowledge_base_id,folder_id,status),
    ADD CONSTRAINT fk_document_folder FOREIGN KEY (folder_id) REFERENCES knowledge_folder (id) ON DELETE SET NULL;
