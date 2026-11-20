-- The copilot's prompt version 3 (ADR 0001, product owner approved): keep the question's shape and
-- every condition the user gives, decline change and restricted requests without looking up
-- something else, and never state a count for a total. A new version rather than an edit
-- of v1, because audit entries name the prompt version that produced each answer. Fixes the seed
-- and the rows seeded by it, and only those: a row someone has changed is left alone.

CREATE OR REPLACE FUNCTION pw.seed_default_agent() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pw, pg_temp
AS $$
BEGIN
  INSERT INTO pw.agent (tenant_id, id, version, status, allowed_scopes, primary_model,
                        fallback_model, prompt_version, max_steps, daily_token_budget, updated_by)
  VALUES (NEW.id, 'copilot', '1', 'ACTIVE', ARRAY['inventory.read', 'orders.read', 'proposal.create'],
          'gemini-flash', 'gemini-flash-fallback', 'system.v3', 5, 2000000, 'system')
  ON CONFLICT DO NOTHING;
  RETURN NEW;
END $$;

-- No tenant context in a migration: lift forced row-level security inside this transaction only.
ALTER TABLE pw.agent NO FORCE ROW LEVEL SECURITY;
UPDATE pw.agent
   SET prompt_version = 'system.v3', updated_at = now()
 WHERE id = 'copilot' AND updated_by = 'system' AND prompt_version = 'system.v2';
ALTER TABLE pw.agent FORCE ROW LEVEL SECURITY;
