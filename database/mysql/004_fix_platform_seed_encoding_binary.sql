-- Correct mojibake by reinterpreting the stored Windows-1252/latin1 bytes as UTF-8.
-- Apply once after 003_fix_platform_seed_encoding.sql.

SET NAMES utf8mb4;

UPDATE knowledge_base
SET name = CONVERT(CAST(CONVERT(name USING latin1) AS BINARY) USING utf8mb4),
    description = CONVERT(CAST(CONVERT(description USING latin1) AS BINARY) USING utf8mb4)
WHERE id = 1;

UPDATE agent
SET name = CONVERT(CAST(CONVERT(name USING latin1) AS BINARY) USING utf8mb4),
    description = CONVERT(CAST(CONVERT(description USING latin1) AS BINARY) USING utf8mb4),
    system_prompt = CONVERT(CAST(CONVERT(system_prompt USING latin1) AS BINARY) USING utf8mb4)
WHERE id = 1;

UPDATE app_role
SET name = CONVERT(CAST(CONVERT(name USING latin1) AS BINARY) USING utf8mb4),
    description = CONVERT(CAST(CONVERT(description USING latin1) AS BINARY) USING utf8mb4)
WHERE code IN ('platform_admin', 'knowledge_base_admin', 'employee');

