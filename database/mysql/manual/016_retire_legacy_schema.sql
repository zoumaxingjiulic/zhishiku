-- Destructive retirement. On an existing database, run ONLY through
-- deploy/retire-legacy-schema.sh. The initdb bootstrap wrapper is for a new
-- empty MySQL data directory and is never a production-upgrade command.
-- after a verified full backup, temporary-database restore, policy comparison,
-- compatible application rollout and maintenance-window preflight.
-- DDL implicitly commits: a mid-migration failure needs forward repair or a
-- restore from the verified backup, not an application-only rollback.

SET NAMES utf8mb4;

DROP PROCEDURE IF EXISTS retire_legacy_schema_016;
DELIMITER $$
CREATE PROCEDURE retire_legacy_schema_016()
BEGIN
    DECLARE unsafe_count BIGINT DEFAULT 0;
    DECLARE expected_tables INT DEFAULT 0;
    DECLARE expected_columns INT DEFAULT 0;

    IF COALESCE(@legacy_cleanup_backup_verified, 0) <> 1
       OR @legacy_cleanup_backup_sha256 IS NULL
       OR CHAR_LENGTH(@legacy_cleanup_backup_sha256) <> 64 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Verified full backup is required';
    END IF;
    IF COALESCE(@legacy_cleanup_policy_verified, 0) <> 1 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Effective retrieval policy comparison is required';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM schema_migration WHERE version = 15)
       OR EXISTS (SELECT 1 FROM schema_migration WHERE version = 16) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Migration order or ledger mismatch';
    END IF;

    SELECT COUNT(*) INTO expected_tables FROM information_schema.tables
    WHERE table_schema = DATABASE()
      AND table_name IN ('workflow_run', 'user_role', 'app_role',
                         'agent_department_acl', 'document_asset');
    IF expected_tables <> 5 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Legacy table inventory mismatch';
    END IF;
    SELECT COUNT(*) INTO expected_columns FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND (table_name = 'agent' AND column_name IN ('llm_model', 'agent_type')
           OR table_name = 'agent_knowledge_base'
              AND column_name = 'retrieval_config_json');
    IF expected_columns <> 3 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Legacy column inventory mismatch';
    END IF;

    SELECT COUNT(*) INTO unsafe_count FROM workflow_run;
    IF unsafe_count <> 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Workflow history is not empty';
    END IF;
    SELECT COUNT(*) INTO unsafe_count FROM agent WHERE launch_mode <> 'chat';
    IF unsafe_count <> 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Unsupported agent launch modes exist';
    END IF;
    SELECT COUNT(*) INTO unsafe_count FROM agent_revision
    WHERE JSON_UNQUOTE(JSON_EXTRACT(snapshot_json, '$.launch_mode')) NOT IN ('chat', '')
       OR JSON_LENGTH(JSON_EXTRACT(snapshot_json, '$.steps')) > 0;
    IF unsafe_count <> 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Historical workflow revision exists';
    END IF;
    SELECT COUNT(*) INTO unsafe_count FROM document_asset;
    IF unsafe_count <> 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Document assets exist';
    END IF;
    SELECT COUNT(*) INTO unsafe_count FROM agent
    WHERE llm_model IS NOT NULL AND TRIM(llm_model) <> '';
    IF unsafe_count <> 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Legacy model override still exists';
    END IF;

    SELECT COUNT(*) INTO unsafe_count FROM (
        SELECT DISTINCT ud.user_id, ada.agent_id
        FROM agent_department_acl ada
        JOIN agent a ON a.id = ada.agent_id AND a.status = 'active'
        JOIN department d ON d.id = ada.department_id AND d.status = 1
        JOIN user_department ud ON ud.department_id = ada.department_id
        JOIN app_user u ON u.id = ud.user_id AND u.status = 1 AND u.deleted_at IS NULL
        WHERE ada.permission = 'use'
          AND NOT EXISTS (
              SELECT 1 FROM user_agent_acl ua
              WHERE ua.user_id = ud.user_id AND ua.agent_id = ada.agent_id
                AND ua.permission = 'use'
          )
    ) missing_grants;
    IF unsafe_count <> 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Legacy agent ACL grants are not fully materialized';
    END IF;

    SELECT COUNT(*) INTO unsafe_count
    FROM agent_knowledge_base ak JOIN agent a ON a.id = ak.agent_id
    WHERE ak.retrieval_config_json IS NOT NULL
      AND (JSON_EXTRACT(a.settings_json, '$.retrieval') IS NULL
           OR JSON_TYPE(JSON_EXTRACT(a.settings_json, '$.retrieval')) = 'NULL');
    IF unsafe_count <> 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Legacy retrieval policy has not been migrated';
    END IF;

    -- Only user_role points at app_role among the retiring tables. Check for
    -- extensions added outside this repository before the first irreversible DDL.
    SELECT COUNT(*) INTO unsafe_count FROM information_schema.key_column_usage
    WHERE referenced_table_schema = DATABASE()
      AND referenced_table_name IN ('workflow_run', 'user_role', 'app_role',
                                    'agent_department_acl', 'document_asset')
      AND table_name NOT IN ('workflow_run', 'user_role', 'app_role',
                             'agent_department_acl', 'document_asset');
    IF unsafe_count <> 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'External foreign key references legacy table';
    END IF;

    DROP TABLE workflow_run;
    DROP TABLE user_role;
    DROP TABLE app_role;
    DROP TABLE agent_department_acl;
    DROP TABLE document_asset;
    ALTER TABLE agent DROP COLUMN llm_model, DROP COLUMN agent_type;
    ALTER TABLE agent_knowledge_base DROP COLUMN retrieval_config_json;

    INSERT INTO schema_migration (version, name)
    VALUES (16, 'retire_legacy_schema');
END$$
DELIMITER ;

CALL retire_legacy_schema_016();
DROP PROCEDURE retire_legacy_schema_016;
