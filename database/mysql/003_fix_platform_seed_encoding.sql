-- Correct legacy mojibake caused when 002 was manually applied with a non-UTF-8 client.
-- Apply once after 002_agent_platform.sql.

SET NAMES utf8mb4;

UPDATE knowledge_base
SET name = CONVERT(CONVERT(name USING latin1) USING utf8mb4),
    description = CONVERT(CONVERT(description USING latin1) USING utf8mb4)
WHERE id = 1;

UPDATE agent
SET name = CONVERT(CONVERT(name USING latin1) USING utf8mb4),
    description = CONVERT(CONVERT(description USING latin1) USING utf8mb4),
    system_prompt = CONVERT(CONVERT(system_prompt USING latin1) USING utf8mb4)
WHERE id = 1;

UPDATE app_role
SET name = CONVERT(CONVERT(name USING latin1) USING utf8mb4),
    description = CONVERT(CONVERT(description USING latin1) USING utf8mb4)
WHERE code IN ('platform_admin', 'knowledge_base_admin', 'employee');

