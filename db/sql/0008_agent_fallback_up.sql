-- The copilot's fallback model is the full Flash model, not Flash-Lite (agent spec change log,
-- 2026-11-19: Flash-Lite words path A answers; the fallback for tool use is gemini-3.5-flash,
-- logical name gemini-flash-fallback). Fixes the seed and the rows seeded by it, and only
-- those: a row someone has changed (updated_by is not 'system') is left alone.

CREATE OR REPLACE FUNCTION pw.seed_default_agent() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pw, pg_temp
AS $$
BEGIN
  INSERT INTO pw.agent (tenant_id, id, version, status, allowed_scopes, primary_model,
                        fallback_model, prompt_version, max_steps, daily_token_budget, updated_by)
  VALUES (NEW.id, 'copilot', '1', 'ACTIVE', ARRAY['inventory.read', 'orders.read', 'proposal.create'],
          'gemini-flash', 'gemini-flash-fallback', 'system.v1', 5, 2000000, 'system')
  ON CONFLICT DO NOTHING;
  RETURN NEW;
END $$;

-- No tenant context in a migration: lift forced row-level security inside this transaction only.
ALTER TABLE pw.agent NO FORCE ROW LEVEL SECURITY;
UPDATE pw.agent
   SET fallback_model = 'gemini-flash-fallback', updated_at = now()
 WHERE id = 'copilot' AND updated_by = 'system' AND fallback_model = 'gemini-flash-lite';
ALTER TABLE pw.agent FORCE ROW LEVEL SECURITY;
