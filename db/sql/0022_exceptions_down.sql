-- Reverses 0022_exceptions_up.sql. Settings versions are append-only, so the schema version 2
-- rows stay; the validator keeps accepting them.
DROP FUNCTION pw.evaluate_site_exceptions(uuid);
DROP TYPE pw.exception_candidate;
DROP FUNCTION sem.exception_candidates(uuid, timestamptz, uuid);
DROP FUNCTION sem.impact_score(uuid, timestamptz, integer, timestamptz, numeric);
DROP VIEW sem.exception_state;
DROP TABLE pw.exception_engine_state;
DROP TABLE pw.exception_evaluation;
DROP FUNCTION pw.exception_evaluation_immutable();
DROP TABLE pw.exception_order;
DROP TABLE pw.exception_bin;
DROP TABLE pw.exception_record;
REVOKE ALL ON ALL TABLES IN SCHEMA sem FROM pw_evaluator;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA sem FROM pw_evaluator;
REVOKE EXECUTE ON FUNCTION pw.current_tenant() FROM pw_evaluator;
REVOKE USAGE ON SCHEMA pw FROM pw_evaluator;

CREATE OR REPLACE FUNCTION pw.seed_default_settings() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pw, pg_temp
AS $$
BEGIN
  INSERT INTO pw.settings_version (tenant_id, site_id, version_no, schema_version, "values", created_by, note)
  VALUES (NEW.tenant_id, NEW.id, 1, 1, pw.default_settings(), 'system', 'defaults')
  ON CONFLICT DO NOTHING;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION pw.default_settings() RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path = pw, pg_temp
AS $$
  SELECT jsonb_build_object(
    'dead_stock_days', 30,
    'slow_in_prime_days', 14,
    'capacity_threshold_pct', 90,
    'stockout_horizon_hours', 24,
    'trailing_window_operating_days', 28)
$$;
