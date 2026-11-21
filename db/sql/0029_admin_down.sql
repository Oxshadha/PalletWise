DROP FUNCTION pw.set_agent_status(text, text);
DROP POLICY admin_unanswered ON pw.copilot_run;
REVOKE SELECT (tenant_id, question, outcome, error_code, created_at) ON pw.copilot_run FROM pw_admin_read;
REVOKE SELECT ON pw.audit_event, pw.model_usage, pw.agent, pw.agent_budget_day FROM pw_admin_read;
REVOKE EXECUTE ON FUNCTION pw.current_tenant(), pw.current_subject() FROM pw_admin_read;
REVOKE USAGE ON SCHEMA pw FROM pw_admin_read;
