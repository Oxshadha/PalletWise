DROP FUNCTION pw.set_governed_query(text, boolean);
ALTER TABLE pw.agent NO FORCE ROW LEVEL SECURITY;
UPDATE pw.agent SET allowed_scopes = array_remove(allowed_scopes, 'query.governed')
 WHERE id = 'copilot' AND updated_by = 'system';
ALTER TABLE pw.agent FORCE ROW LEVEL SECURITY;
CREATE OR REPLACE FUNCTION pw.seed_default_agent() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pw, pg_temp
AS $$
BEGIN
  INSERT INTO pw.agent (tenant_id, id, version, status, allowed_scopes, primary_model,
                        fallback_model, prompt_version, max_steps, daily_token_budget, updated_by)
  VALUES (NEW.id, 'copilot', '1', 'ACTIVE', ARRAY['inventory.read', 'orders.read', 'proposal.create'],
          'gemini-flash-lite', 'gemini-flash-fallback', 'system.v3', 5, 2000000, 'system')
  ON CONFLICT DO NOTHING;
  RETURN NEW;
END $$;
ALTER TABLE pw.agent DROP COLUMN governed_query_enabled;
