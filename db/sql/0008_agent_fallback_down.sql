-- Reverses 0008: the seed and the system-seeded rows go back to Flash-Lite.

CREATE OR REPLACE FUNCTION pw.seed_default_agent() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pw, pg_temp
AS $$
BEGIN
  INSERT INTO pw.agent (tenant_id, id, version, status, allowed_scopes, primary_model,
                        fallback_model, prompt_version, max_steps, daily_token_budget, updated_by)
  VALUES (NEW.id, 'copilot', '1', 'ACTIVE', ARRAY['inventory.read', 'orders.read', 'proposal.create'],
          'gemini-flash', 'gemini-flash-lite', 'system.v1', 5, 2000000, 'system')
  ON CONFLICT DO NOTHING;
  RETURN NEW;
END $$;

ALTER TABLE pw.agent NO FORCE ROW LEVEL SECURITY;
UPDATE pw.agent
   SET fallback_model = 'gemini-flash-lite', updated_at = now()
 WHERE id = 'copilot' AND updated_by = 'system' AND fallback_model = 'gemini-flash-fallback';
ALTER TABLE pw.agent FORCE ROW LEVEL SECURITY;
