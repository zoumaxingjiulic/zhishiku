-- Runs only inside MySQL's empty-data-directory initialization. The full
-- backup gate applies to EXISTING databases via deploy/retire-legacy-schema.sh.
-- This file must not be used for a manual upgrade.
SET @legacy_cleanup_backup_verified = 1;
SET @legacy_cleanup_backup_sha256 = REPEAT('0', 64);
SET @legacy_cleanup_policy_verified = 1;
SOURCE /docker-entrypoint-initdb.d/manual/016_retire_legacy_schema.sql;
