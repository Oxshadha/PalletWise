-- Admin panel core (FR-ADM-03, 05, 06, 07). Runs as pw_owner.
--
-- Reading: a new role pw_admin_read (AGENTS.md rule 7: its own path) reads the audit trail, model
-- usage, copilot runs and the agent registry of its tenant (forced RLS underneath). Writing: pw_app
-- gains one function, set_agent_status() (the kill switch), and no table privilege.

GRANT USAGE ON SCHEMA pw TO pw_admin_read;
GRANT EXECUTE ON FUNCTION pw.current_tenant(), pw.current_subject() TO pw_admin_read;
GRANT SELECT ON pw.audit_event, pw.model_usage, pw.agent, pw.agent_budget_day TO pw_admin_read;

-- Unanswered questions (FR-ADM-07): copilot runs are private to their user (owner_isolation). The
-- admin read role sees, in its tenant, only runs that ended without an answer, and only the
-- columns that view needs; answers, results and every other conversation stay private.
GRANT SELECT (tenant_id, question, outcome, error_code, created_at) ON pw.copilot_run TO pw_admin_read;
CREATE POLICY admin_unanswered ON pw.copilot_run FOR SELECT TO pw_admin_read
  USING (tenant_id = (SELECT pw.current_tenant()) AND outcome IN ('DECLINE', 'ERROR', 'NO_RESULTS'));

-- Suspends or resumes an agent of the caller's tenant (FR-ADM-03). Outcomes: 'changed',
-- 'unchanged', 'not_found'. The caller appends the audit entry in the same transaction.
CREATE FUNCTION pw.set_agent_status(p_agent text, p_status text)
RETURNS TABLE (outcome text, before_status text, after_status text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pw, pg_temp
AS $$
DECLARE v_before text;
BEGIN
  IF p_status NOT IN ('ACTIVE', 'SUSPENDED') THEN
    RAISE EXCEPTION 'unknown agent status' USING ERRCODE = '22023';
  END IF;
  SELECT a.status INTO v_before FROM pw.agent a WHERE a.id = p_agent FOR UPDATE;
  IF NOT FOUND THEN
    RETURN QUERY SELECT 'not_found', NULL::text, NULL::text;
    RETURN;
  END IF;
  IF v_before = p_status THEN
    RETURN QUERY SELECT 'unchanged', v_before, v_before;
    RETURN;
  END IF;
  UPDATE pw.agent SET status = p_status WHERE id = p_agent;
  RETURN QUERY SELECT 'changed', v_before, p_status;
END $$;

REVOKE EXECUTE ON FUNCTION pw.set_agent_status(text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION pw.set_agent_status(text, text) TO pw_app;
