-- Only deterministic public fields participate. Each query is a separately
-- ordered part of the development baseline; psql's output is hashed by CI.
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SELECT id, description, config_schema FROM inkcre.ai_dialects ORDER BY id;
SELECT name, nickname FROM inkcre.extensions ORDER BY name;
SELECT id, description, parameters_schema, default_timeout_seconds FROM inkcre.job_types ORDER BY id;
SELECT id, description, config_schema, writable FROM inkcre.storage_types ORDER BY id;
SELECT id, description, config_schema, collect_config_schema, backfill_config_schema FROM inkcre.sources_types ORDER BY id;
SELECT id, type, nickname, config FROM inkcre.storages ORDER BY id;
SELECT id, name, application_version, labels, config, config_schema, capabilities,
       lease_expires_at, created_at
FROM inkcre.peers WHERE id = '00000000-0000-4000-8000-000000000001';
COMMIT;
