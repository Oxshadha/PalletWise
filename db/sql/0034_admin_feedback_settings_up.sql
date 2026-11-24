-- Admin panel: answer feedback (FR-COP-14) and site settings (FR-ADM-04). Runs as pw_owner.
--
-- Feedback. Conversations stay private to their user (owner_isolation, migration 0006; the admin
-- read role sees only unanswered runs, migration 0029). A rating of "not helpful" is sent for
-- review: the form says so, and the question is copied onto the feedback row when it is given. The
-- admin read role reads feedback rows only, in its tenant, and only the columns the view needs;
-- never the answer, the results or the user's other questions. A "helpful" rating carries no question.
--
-- Settings. pw_app gains one function, pw.save_site_settings(), which numbers a new version under a
-- lock on the site row and refuses a stale edit (optimistic concurrency). The caller appends the
-- audit entry in the same transaction. The table's CHECK (pw.settings_valid) stays the validator.

-- ------------------------------------------------------------ feedback

ALTER TABLE pw.run_feedback ADD COLUMN question text CHECK (length(question) <= 4000);

GRANT SELECT (tenant_id, run_id, rating, comment, question, trace_id, created_at)
  ON pw.run_feedback TO pw_admin_read;
CREATE POLICY admin_feedback ON pw.run_feedback FOR SELECT TO pw_admin_read
  USING (tenant_id = (SELECT pw.current_tenant()));

-- The feedback view names the tools each rated run used, from the audit trail (one indexed lookup
-- per run instead of a scan of the whole trail).
CREATE INDEX audit_event_run ON pw.audit_event (tenant_id, run_id) WHERE run_id IS NOT NULL;

-- ------------------------------------------------------------ settings

GRANT SELECT ON pw.settings_version TO pw_admin_read;

-- Saves new values for a site of the caller's tenant. Outcomes: 'changed' (a new version),
-- 'unchanged' (the same values: no new version), 'conflict' (the site's current version is not the
-- one the editor started from), 'not_found' (no such site in this tenant).
CREATE FUNCTION pw.save_site_settings(
  p_site uuid, p_expected_version int, p_values jsonb, p_created_by text, p_note text
) RETURNS TABLE (outcome text, version_no int, before_values jsonb)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pw, pg_temp
AS $$
DECLARE
  v_tenant uuid := pw.current_tenant();
  v_current pw.settings_version;
BEGIN
  PERFORM 1 FROM pw.site WHERE id = p_site AND tenant_id = v_tenant FOR UPDATE;
  IF NOT FOUND THEN
    RETURN QUERY SELECT 'not_found', NULL::int, NULL::jsonb;
    RETURN;
  END IF;
  SELECT * INTO v_current FROM pw.settings_version s
   WHERE s.site_id = p_site ORDER BY s.version_no DESC LIMIT 1;
  IF v_current.version_no IS DISTINCT FROM p_expected_version THEN
    RETURN QUERY SELECT 'conflict', v_current.version_no, v_current."values";
    RETURN;
  END IF;
  IF v_current."values" = p_values THEN
    RETURN QUERY SELECT 'unchanged', v_current.version_no, v_current."values";
    RETURN;
  END IF;
  INSERT INTO pw.settings_version (tenant_id, site_id, version_no, schema_version, "values", created_by, note)
  VALUES (v_tenant, p_site, v_current.version_no + 1, 2, p_values, p_created_by, p_note);
  RETURN QUERY SELECT 'changed', v_current.version_no + 1, v_current."values";
END $$;

REVOKE EXECUTE ON FUNCTION pw.save_site_settings(uuid, int, jsonb, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION pw.save_site_settings(uuid, int, jsonb, text, text) TO pw_app;
