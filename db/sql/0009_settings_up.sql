-- Versioned site settings (CWM 9.1, Slice 2 acceptance D3). Runs as pw_owner.
--
-- A new version is a new row; the highest version_no of a site is in effect. Every semantic
-- function takes a settings_version_id, so all numbers in one answer share one version.
-- Schema version 1 holds the keys Slice 2 uses. Later slices add their keys (ranking weights,
-- festival uplift, proposal expiry, dispatch lead time) under schema version 2 and up.

-- ------------------------------------------------------------ validation

-- The one validator for stored settings: used by the table's CHECK constraint, so no invalid
-- version can exist whoever writes it. Unknown keys are refused, like unknown tool arguments.
CREATE FUNCTION pw.settings_valid(p_schema_version int, p_values jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path = pw, pg_temp
AS $$
  SELECT p_schema_version = 1
     AND jsonb_typeof(p_values) = 'object'
     AND (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(p_values) AS k)
         = ARRAY['capacity_threshold_pct', 'dead_stock_days', 'slow_in_prime_days',
                 'stockout_horizon_hours', 'trailing_window_operating_days']
     AND jsonb_typeof(p_values->'dead_stock_days') = 'number'
     AND (p_values->>'dead_stock_days')::numeric BETWEEN 1 AND 365
     AND (p_values->>'dead_stock_days')::numeric = trunc((p_values->>'dead_stock_days')::numeric)
     AND jsonb_typeof(p_values->'slow_in_prime_days') = 'number'
     AND (p_values->>'slow_in_prime_days')::numeric BETWEEN 1 AND 365
     AND (p_values->>'slow_in_prime_days')::numeric = trunc((p_values->>'slow_in_prime_days')::numeric)
     AND jsonb_typeof(p_values->'capacity_threshold_pct') = 'number'
     AND (p_values->>'capacity_threshold_pct')::numeric > 0
     AND (p_values->>'capacity_threshold_pct')::numeric <= 100
     AND jsonb_typeof(p_values->'stockout_horizon_hours') = 'number'
     AND (p_values->>'stockout_horizon_hours')::numeric BETWEEN 1 AND 168
     AND jsonb_typeof(p_values->'trailing_window_operating_days') = 'number'
     AND (p_values->>'trailing_window_operating_days')::numeric BETWEEN 1 AND 365
     AND (p_values->>'trailing_window_operating_days')::numeric
         = trunc((p_values->>'trailing_window_operating_days')::numeric)
$$;

-- Defaults from the semantic spec: sections 3 (W = 28) and 6 (D = 30, P = 14, T = 90, H = 24).
CREATE FUNCTION pw.default_settings() RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path = pw, pg_temp
AS $$
  SELECT jsonb_build_object(
    'dead_stock_days', 30,
    'slow_in_prime_days', 14,
    'capacity_threshold_pct', 90,
    'stockout_horizon_hours', 24,
    'trailing_window_operating_days', 28)
$$;

-- ------------------------------------------------------------ table

CREATE TABLE pw.settings_version (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       uuid NOT NULL,
  site_id         uuid NOT NULL,
  version_no      int NOT NULL CHECK (version_no >= 1),
  schema_version  int NOT NULL,
  "values"        jsonb NOT NULL,
  created_by      text NOT NULL,
  created_at      timestamptz NOT NULL DEFAULT now(),
  note            text,
  CHECK (pw.settings_valid(schema_version, "values")),
  UNIQUE (site_id, version_no),
  UNIQUE (tenant_id, site_id, id),
  FOREIGN KEY (tenant_id, site_id) REFERENCES pw.site (tenant_id, id)
);

ALTER TABLE pw.settings_version ENABLE ROW LEVEL SECURITY;
ALTER TABLE pw.settings_version FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON pw.settings_version
  USING (tenant_id = (SELECT pw.current_tenant()))
  WITH CHECK (tenant_id = (SELECT pw.current_tenant()));

-- Settings are history: a version is never changed or removed, only superseded.
CREATE FUNCTION pw.settings_version_immutable() RETURNS trigger
LANGUAGE plpgsql SET search_path = pw, pg_temp
AS $$
BEGIN
  RAISE EXCEPTION 'settings versions are append-only' USING ERRCODE = 'insufficient_privilege';
END $$;

CREATE TRIGGER settings_version_append_only BEFORE UPDATE OR DELETE ON pw.settings_version
  FOR EACH ROW EXECUTE FUNCTION pw.settings_version_immutable();

-- ------------------------------------------------------------ writing

-- The only write path (the Admin editor arrives in Slice 6). Versions are numbered under a
-- lock on the site row, so two concurrent saves cannot claim the same number.
CREATE FUNCTION pw.create_settings_version(
  p_tenant uuid, p_site uuid, p_schema_version int, p_values jsonb, p_created_by text, p_note text
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pw, pg_temp
AS $$
DECLARE
  v_next int;
  v_id uuid;
BEGIN
  PERFORM pw.assert_tenant(p_tenant);
  PERFORM 1 FROM pw.site WHERE id = p_site AND tenant_id = p_tenant FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'unknown site' USING ERRCODE = 'foreign_key_violation';
  END IF;
  SELECT coalesce(max(version_no), 0) + 1 INTO v_next FROM pw.settings_version WHERE site_id = p_site;
  INSERT INTO pw.settings_version (tenant_id, site_id, version_no, schema_version, "values", created_by, note)
  VALUES (p_tenant, p_site, v_next, p_schema_version, p_values, p_created_by, p_note)
  RETURNING id INTO v_id;
  RETURN v_id;
END $$;

-- Every new site starts with version 1 holding the defaults (CWM 9.1: a new site starts with
-- a copy of existing settings; with no Admin editor yet, the defaults are that copy).
CREATE FUNCTION pw.seed_default_settings() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pw, pg_temp
AS $$
BEGIN
  INSERT INTO pw.settings_version (tenant_id, site_id, version_no, schema_version, "values", created_by, note)
  VALUES (NEW.tenant_id, NEW.id, 1, 1, pw.default_settings(), 'system', 'defaults')
  ON CONFLICT DO NOTHING;
  RETURN NEW;
END $$;

CREATE TRIGGER seed_default_settings AFTER INSERT ON pw.site
  FOR EACH ROW EXECUTE FUNCTION pw.seed_default_settings();

-- Sites that already exist. The migration has no tenant context, so it lifts the forced
-- row-level security for these tables inside its own transaction only.
ALTER TABLE pw.site NO FORCE ROW LEVEL SECURITY;
ALTER TABLE pw.settings_version NO FORCE ROW LEVEL SECURITY;
INSERT INTO pw.settings_version (tenant_id, site_id, version_no, schema_version, "values", created_by, note)
SELECT tenant_id, id, 1, 1, pw.default_settings(), 'system', 'defaults' FROM pw.site
ON CONFLICT DO NOTHING;
ALTER TABLE pw.site FORCE ROW LEVEL SECURITY;
ALTER TABLE pw.settings_version FORCE ROW LEVEL SECURITY;

-- ------------------------------------------------------------ reading (semantic layer)

-- Every version, so a request can name the one it read at its start.
CREATE VIEW sem.settings_version AS
SELECT id AS settings_version_id, site_id, version_no, schema_version, "values", created_at
  FROM pw.settings_version;

-- The version in effect per site: the highest version_no.
CREATE VIEW sem.current_settings AS
SELECT DISTINCT ON (site_id) settings_version_id, site_id, version_no, schema_version, "values"
  FROM sem.settings_version
 ORDER BY site_id, version_no DESC;

-- One setting of one version, as a number. A missing version or key is an error, never a
-- silent default: every number in an answer must trace to a stored version.
CREATE FUNCTION sem.setting(p_settings uuid, p_key text) RETURNS numeric
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path = sem, pg_temp
AS $$
DECLARE
  v numeric;
BEGIN
  SELECT ("values"->>p_key)::numeric INTO v FROM sem.settings_version WHERE settings_version_id = p_settings;
  IF v IS NULL THEN
    RAISE EXCEPTION 'setting % not found in version %', p_key, p_settings USING ERRCODE = 'no_data_found';
  END IF;
  RETURN v;
END $$;

-- ------------------------------------------------------------ grants

REVOKE ALL ON pw.settings_version FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION pw.create_settings_version(uuid, uuid, int, jsonb, text, text),
  pw.seed_default_settings(), pw.settings_version_immutable() FROM PUBLIC;
GRANT SELECT ON pw.settings_version TO pw_app;
GRANT EXECUTE ON FUNCTION pw.create_settings_version(uuid, uuid, int, jsonb, text, text) TO pw_app;
GRANT SELECT ON sem.settings_version, sem.current_settings TO pw_agent_read;
GRANT EXECUTE ON FUNCTION sem.setting(uuid, text) TO pw_agent_read;
