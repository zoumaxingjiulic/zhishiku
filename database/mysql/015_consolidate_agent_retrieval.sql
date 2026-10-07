-- Non-destructive retirement preparation. Execute once after 014.
-- Run deploy/verify-legacy-migration.py --phase before first; compare its
-- effective-policy output with --phase after before deploying new code.

SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS schema_migration (
    version INT UNSIGNED NOT NULL PRIMARY KEY,
    name VARCHAR(128) NOT NULL,
    applied_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

DROP PROCEDURE IF EXISTS migrate_agent_retrieval_015;
DELIMITER $$
CREATE PROCEDURE migrate_agent_retrieval_015()
BEGIN
    DECLARE invalid_count BIGINT DEFAULT 0;
    DECLARE conflict_count BIGINT DEFAULT 0;

    IF EXISTS (SELECT 1 FROM schema_migration WHERE version = 15) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Migration 015 already applied';
    END IF;

    SELECT COUNT(*) INTO invalid_count FROM agent
    WHERE settings_json IS NOT NULL AND JSON_TYPE(settings_json) <> 'OBJECT';
    IF invalid_count <> 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Invalid agent settings JSON';
    END IF;

    SELECT COUNT(*) INTO invalid_count
    FROM agent a JOIN agent_knowledge_base ak ON ak.agent_id = a.id
    WHERE ak.retrieval_config_json IS NOT NULL
      AND (JSON_TYPE(ak.retrieval_config_json) <> 'OBJECT'
        OR (JSON_CONTAINS_PATH(a.settings_json, 'one', '$.retrieval') = 1
            AND JSON_TYPE(JSON_EXTRACT(a.settings_json, '$.retrieval')) NOT IN ('OBJECT', 'NULL')));
    IF invalid_count <> 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Invalid legacy or published retrieval JSON';
    END IF;

    SELECT COUNT(*) INTO conflict_count FROM (
        SELECT ak.agent_id
        FROM agent_knowledge_base ak JOIN agent a ON a.id = ak.agent_id
        WHERE ak.retrieval_config_json IS NOT NULL
          AND (JSON_EXTRACT(a.settings_json, '$.retrieval') IS NULL
               OR JSON_TYPE(JSON_EXTRACT(a.settings_json, '$.retrieval')) = 'NULL'
               OR JSON_LENGTH(JSON_EXTRACT(a.settings_json, '$.retrieval')) = 0)
        GROUP BY ak.agent_id
        HAVING COUNT(DISTINCT CAST(ak.retrieval_config_json AS CHAR)) > 1
    ) conflicts;
    IF conflict_count <> 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Conflicting legacy retrieval policies';
    END IF;

    START TRANSACTION;
    UPDATE agent a JOIN (
        SELECT agent_id, MIN(CAST(retrieval_config_json AS CHAR)) AS config_json
        FROM agent_knowledge_base
        WHERE retrieval_config_json IS NOT NULL
        GROUP BY agent_id
    ) legacy ON legacy.agent_id = a.id
    SET a.settings_json = JSON_SET(
        COALESCE(a.settings_json, JSON_OBJECT()), '$.retrieval',
        JSON_EXTRACT(legacy.config_json, '$')
    )
    WHERE JSON_EXTRACT(a.settings_json, '$.retrieval') IS NULL
       OR JSON_TYPE(JSON_EXTRACT(a.settings_json, '$.retrieval')) = 'NULL'
       OR JSON_LENGTH(JSON_EXTRACT(a.settings_json, '$.retrieval')) = 0;

    INSERT INTO schema_migration (version, name)
    VALUES (15, 'consolidate_agent_retrieval');
    COMMIT;
END$$
DELIMITER ;

CALL migrate_agent_retrieval_015();
DROP PROCEDURE migrate_agent_retrieval_015;
