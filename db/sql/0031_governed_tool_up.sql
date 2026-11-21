-- Governed query tool, step 3 (ADR 0002 section 7, slice-5-acceptance D4 and D5). Runs as
-- pw_owner.
--
-- The per-tenant switch: on by default, the Admin can switch it off and on (an admin endpoint now,
-- the admin panel screen in Slice 7); checked on every call like the kill switch. The tool's scope
-- query.governed joins the copilot's allowed scopes, in the seed and in system-seeded rows.

ALTER TABLE pw.agent ADD COLUMN governed_query_enabled boolean NOT NULL DEFAULT true;

CREATE OR REPLACE FUNCTION pw.seed_default_agent() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pw, pg_temp
AS $$
BEGIN
  INSERT INTO pw.agent (tenant_id, id, version, status, allowed_scopes, primary_model,
                        fallback_model, prompt_version, max_steps, daily_token_budget, updated_by)
  VALUES (NEW.id, 'copilot', '1', 'ACTIVE',
          ARRAY['inventory.read', 'orders.read', 'proposal.create', 'query.governed'],
          'gemini-flash-lite', 'gemini-flash-fallback', 'system.v3', 5, 2000000, 'system')
  ON CONFLICT DO NOTHING;
  RETURN NEW;
END $$;

-- No tenant context in a migration: lift forced row-level security inside this transaction only.
ALTER TABLE pw.agent NO FORCE ROW LEVEL SECURITY;
UPDATE pw.agent
   SET allowed_scopes = allowed_scopes || ARRAY['query.governed'], updated_at = now()
 WHERE id = 'copilot' AND updated_by = 'system' AND NOT 'query.governed' = ANY (allowed_scopes);
ALTER TABLE pw.agent FORCE ROW LEVEL SECURITY;

-- The switch (D5). Outcomes: 'changed', 'unchanged', 'not_found'. The caller audits it in the same
-- transaction.
CREATE FUNCTION pw.set_governed_query(p_agent text, p_enabled boolean)
RETURNS TABLE (outcome text, before_enabled boolean, after_enabled boolean)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pw, pg_temp
AS $$
DECLARE v_before boolean;
BEGIN
  SELECT a.governed_query_enabled INTO v_before FROM pw.agent a WHERE a.id = p_agent FOR UPDATE;
  IF NOT FOUND THEN
    RETURN QUERY SELECT 'not_found', NULL::boolean, NULL::boolean;
    RETURN;
  END IF;
  IF v_before = p_enabled THEN
    RETURN QUERY SELECT 'unchanged', v_before, v_before;
    RETURN;
  END IF;
  UPDATE pw.agent SET governed_query_enabled = p_enabled, updated_at = now() WHERE id = p_agent;
  RETURN QUERY SELECT 'changed', v_before, p_enabled;
END $$;

REVOKE EXECUTE ON FUNCTION pw.set_governed_query(text, boolean) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION pw.set_governed_query(text, boolean) TO pw_app;
