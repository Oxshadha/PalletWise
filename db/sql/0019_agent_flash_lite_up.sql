-- The copilot's primary model becomes Flash-Lite (ADR 0001 amendment 3, final comparison
-- 2026-11-20-b7ee94a): on prompt v3 it met every quality gate and the tier-3 targets (highlights
-- 3.1 s, headline 4.1 s, complete 5.4 s at p95) where 3.8 Flash missed highlights (8.4 s). The
-- fallback stays gemini-flash-fallback. System-seeded rows only. A new version rather than an edit
-- of v1, because audit entries name the prompt version that produced each answer. Fixes the seed
-- and the rows seeded by it, and only those: a row someone has changed is left alone.

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

-- No tenant context in a migration: lift forced row-level security inside this transaction only.
ALTER TABLE pw.agent NO FORCE ROW LEVEL SECURITY;
UPDATE pw.agent
   SET primary_model = 'gemini-flash-lite', updated_at = now()
 WHERE id = 'copilot' AND updated_by = 'system' AND primary_model = 'gemini-flash';
ALTER TABLE pw.agent FORCE ROW LEVEL SECURITY;
