-- Reverses 0009.
DROP FUNCTION IF EXISTS sem.setting(uuid, text);
DROP VIEW IF EXISTS sem.current_settings;
DROP VIEW IF EXISTS sem.settings_version;
DROP TRIGGER IF EXISTS seed_default_settings ON pw.site;
DROP FUNCTION IF EXISTS pw.seed_default_settings();
DROP FUNCTION IF EXISTS pw.create_settings_version(uuid, uuid, int, jsonb, text, text);
DROP TABLE IF EXISTS pw.settings_version;
DROP FUNCTION IF EXISTS pw.settings_version_immutable();
DROP FUNCTION IF EXISTS pw.default_settings();
DROP FUNCTION IF EXISTS pw.settings_valid(int, jsonb);
