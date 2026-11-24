-- Reverses 0034_admin_feedback_settings_up.sql. Settings versions saved through the function stay
-- (versions are append-only history); only the function and the grants go.

DROP FUNCTION pw.save_site_settings(uuid, int, jsonb, text, text);
REVOKE SELECT ON pw.settings_version FROM pw_admin_read;

DROP INDEX pw.audit_event_run;
DROP POLICY admin_feedback ON pw.run_feedback;
REVOKE SELECT (tenant_id, run_id, rating, comment, question, trace_id, created_at)
  ON pw.run_feedback FROM pw_admin_read;
ALTER TABLE pw.run_feedback DROP COLUMN question;
