-- Reverses 0006. Destroys conversations, usage and budgets: development and CI round trip only.
DROP FUNCTION IF EXISTS pw.rate_limit_hit(uuid, text, text, integer);
DROP FUNCTION IF EXISTS pw.record_model_usage(uuid, jsonb);
DROP FUNCTION IF EXISTS pw.settle_budget(uuid, text, date, bigint, bigint);
DROP FUNCTION IF EXISTS pw.reserve_budget(uuid, text, date, bigint, bigint);
DROP TABLE IF EXISTS pw.rate_window;
DROP TABLE IF EXISTS pw.agent_budget_day;
DROP TABLE IF EXISTS pw.model_usage;
DROP TABLE IF EXISTS pw.run_feedback;
DROP TABLE IF EXISTS pw.copilot_run;
DROP TABLE IF EXISTS pw.conversation;
REVOKE USAGE ON SCHEMA pw FROM pw_memory;
DROP FUNCTION IF EXISTS pw.current_subject();
