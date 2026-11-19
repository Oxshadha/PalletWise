-- The copilot's prompt version 2 (Slice 2 step 5): prefer the tool made for the question, use
-- query_inventory for combinations and working-set follow-ups. A new version rather than an edit
-- of v1, because audit entries name the prompt version that produced each answer. Fixes the seed
-- and the rows seeded by it, and only those: a row someone has changed is left alone.

CREATE OR REPLACE FUNCTION pw.seed_default_agent() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pw, pg_temp
AS $$
BEGIN
  INSERT INTO pw.agent (tenant_id, id, version, status, allowed_scopes, primary_model,
                        fallback_model, prompt_version, max_steps, daily_token_budget, updated_by)
  VALUES (NEW.id, 'copilot', '1', 'ACTIVE', ARRAY['inventory.read', 'orders.read', 'proposal.create'],
          'gemini-flash', 'gemini-flash-fallback', 'system.v2', 5, 2000000, 'system')
  ON CONFLICT DO NOTHING;
  RETURN NEW;
END $$;

-- No tenant context in a migration: lift forced row-level security inside this transaction only.
ALTER TABLE pw.agent NO FORCE ROW LEVEL SECURITY;
UPDATE pw.agent
   SET prompt_version = 'system.v2', updated_at = now()
 WHERE id = 'copilot' AND updated_by = 'system' AND prompt_version = 'system.v1';
ALTER TABLE pw.agent FORCE ROW LEVEL SECURITY;
