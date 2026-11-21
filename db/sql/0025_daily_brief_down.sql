-- Reverses 0025_daily_brief_up.sql.
REVOKE SELECT ON sem.exception_state FROM pw_agent_read;
DROP FUNCTION sem.data_freshness(uuid, timestamptz);
DROP FUNCTION sem.throughput(uuid, timestamptz);
DROP FUNCTION sem.today_horizon_hours(uuid, timestamptz);
DROP FUNCTION sem.business_date(uuid, timestamptz);
ALTER TABLE pw.model_usage DROP COLUMN brief_id;
-- Usage history is kept (and row-level security keeps the migrator from touching it): the old
-- check applies to new rows only, so recorded BRIEF usage stays as it was.
ALTER TABLE pw.model_usage DROP CONSTRAINT model_usage_purpose_check;
ALTER TABLE pw.model_usage ADD CONSTRAINT model_usage_purpose_check
  CHECK (purpose IN ('WORDING', 'AGENT', 'CLASSIFIER')) NOT VALID;
DROP TABLE pw.daily_brief_claim;
DROP TABLE pw.daily_brief;
DROP FUNCTION pw.daily_brief_immutable();
REVOKE ALL ON SCHEMA pw FROM pw_brief;
REVOKE EXECUTE ON FUNCTION pw.current_tenant() FROM pw_brief;
