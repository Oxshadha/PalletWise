-- PalletWise governance, Slice 1: agent registry and the hash-chained audit log.
-- Specs: identity-and-access.md 5 (registry), evals-and-observability.md 15 (audit),
-- requirements NFR-AUD-01 to NFR-AUD-03. Runs as pw_owner.
--
-- Audit time is database wall time, not the site clock: audit records when something
-- really happened, whatever the simulated business time was.

-- ------------------------------------------------------------ agent registry

CREATE TABLE pw.agent (
  tenant_id           uuid NOT NULL REFERENCES pw.tenant (id),
  id                  text NOT NULL,
  version             text NOT NULL,
  status              text NOT NULL CHECK (status IN ('ACTIVE', 'SUSPENDED')),
  allowed_scopes      text[] NOT NULL,
  primary_model       text NOT NULL,
  fallback_model      text,
  prompt_version      text NOT NULL,
  max_steps           integer NOT NULL CHECK (max_steps BETWEEN 1 AND 20),
  daily_token_budget  bigint NOT NULL CHECK (daily_token_budget > 0),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  updated_by          text NOT NULL,
  PRIMARY KEY (tenant_id, id)
);

ALTER TABLE pw.agent ENABLE ROW LEVEL SECURITY;
ALTER TABLE pw.agent FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON pw.agent
  USING (tenant_id = (SELECT pw.current_tenant())) WITH CHECK (tenant_id = (SELECT pw.current_tenant()));

-- Every tenant starts with the copilot (identity spec 5: one seeded row; the Admin UI that
-- changes it arrives later). Model names are logical; the LLM provider resolves them.
CREATE FUNCTION pw.seed_default_agent() RETURNS trigger
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

CREATE TRIGGER seed_default_agent AFTER INSERT ON pw.tenant
  FOR EACH ROW EXECUTE FUNCTION pw.seed_default_agent();

-- Tenants that already exist. The migration has no tenant context, so it lifts the forced
-- row-level security for these two tables inside its own transaction only.
ALTER TABLE pw.tenant NO FORCE ROW LEVEL SECURITY;
ALTER TABLE pw.agent NO FORCE ROW LEVEL SECURITY;
INSERT INTO pw.agent (tenant_id, id, version, status, allowed_scopes, primary_model,
                      fallback_model, prompt_version, max_steps, daily_token_budget, updated_by)
SELECT id, 'copilot', '1', 'ACTIVE', ARRAY['inventory.read', 'orders.read', 'proposal.create'],
       'gemini-flash', 'gemini-flash-lite', 'system.v1', 5, 2000000, 'system'
  FROM pw.tenant
ON CONFLICT DO NOTHING;
ALTER TABLE pw.tenant FORCE ROW LEVEL SECURITY;
ALTER TABLE pw.agent FORCE ROW LEVEL SECURITY;

-- ------------------------------------------------------------ audit log

CREATE TABLE pw.audit_event (
  tenant_id        uuid NOT NULL REFERENCES pw.tenant (id),
  seq              bigint NOT NULL CHECK (seq > 0),
  id               uuid NOT NULL UNIQUE,
  occurred_at      timestamptz NOT NULL,
  site_id          uuid,
  actor_type       text NOT NULL CHECK (actor_type IN ('USER', 'AGENT', 'SYSTEM')),
  user_subject     text,
  user_role        text,
  agent_id         text,
  agent_version    text,
  model            text,
  prompt_version   text,
  run_id           uuid,
  action           text NOT NULL,
  scope            text,
  tool             text,
  parameters       jsonb,
  decision         text CHECK (decision IN ('ALLOW', 'DENY')),
  deny_reasons     text[],
  outcome          text NOT NULL,
  row_count        integer,
  latency_ms       integer,
  input_tokens     integer,
  output_tokens    integer,
  cost_estimated   numeric(14, 6),
  trace_id         text NOT NULL CHECK (trace_id ~ '^[0-9a-f]{32}$'),
  hash_version     smallint NOT NULL,
  prev_hash        bytea NOT NULL CHECK (length(prev_hash) = 32),
  hash             bytea NOT NULL CHECK (length(hash) = 32),
  PRIMARY KEY (tenant_id, seq)
);

CREATE TABLE pw.audit_chain_head (
  tenant_id  uuid PRIMARY KEY REFERENCES pw.tenant (id),
  last_seq   bigint NOT NULL DEFAULT 0,
  last_hash  bytea NOT NULL DEFAULT '\x0000000000000000000000000000000000000000000000000000000000000000'
);

ALTER TABLE pw.audit_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE pw.audit_event FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON pw.audit_event
  USING (tenant_id = (SELECT pw.current_tenant())) WITH CHECK (tenant_id = (SELECT pw.current_tenant()));
ALTER TABLE pw.audit_chain_head ENABLE ROW LEVEL SECURITY;
ALTER TABLE pw.audit_chain_head FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON pw.audit_chain_head
  USING (tenant_id = (SELECT pw.current_tenant())) WITH CHECK (tenant_id = (SELECT pw.current_tenant()));

-- Append-only even for the owner (NFR-AUD-01): no runtime role has UPDATE or DELETE, and this
-- trigger also stops the owner. Retention of sealed segments (Slice 7) will replace it with a
-- narrower rule.
CREATE FUNCTION pw.audit_is_append_only() RETURNS trigger
LANGUAGE plpgsql SET search_path = pw, pg_temp
AS $$
BEGIN
  RAISE EXCEPTION 'audit events are append-only' USING ERRCODE = 'insufficient_privilege';
END $$;

CREATE TRIGGER audit_is_append_only BEFORE UPDATE OR DELETE ON pw.audit_event
  FOR EACH ROW EXECUTE FUNCTION pw.audit_is_append_only();
CREATE TRIGGER audit_no_truncate BEFORE TRUNCATE ON pw.audit_event
  FOR EACH STATEMENT EXECUTE FUNCTION pw.audit_is_append_only();

-- Canonical serialization, version 1 (evals spec 15): object keys sorted by code point, no
-- insignificant whitespace, numbers as decimal strings, null as null, arrays in order.
CREATE FUNCTION pw.canonical_json(j jsonb) RETURNS text
LANGUAGE plpgsql IMMUTABLE SET search_path = pw, pg_temp
AS $$
DECLARE t text := jsonb_typeof(j);
BEGIN
  IF j IS NULL OR t = 'null' THEN
    RETURN 'null';
  ELSIF t = 'object' THEN
    RETURN '{' || coalesce((
      SELECT string_agg(to_json(k)::text || ':' || pw.canonical_json(v), ',' ORDER BY k COLLATE "C")
        FROM jsonb_each(j) AS e (k, v)), '') || '}';
  ELSIF t = 'array' THEN
    RETURN '[' || coalesce((
      SELECT string_agg(pw.canonical_json(v), ',' ORDER BY i)
        FROM jsonb_array_elements(j) WITH ORDINALITY AS a (v, i)), '') || ']';
  ELSIF t = 'number' THEN
    RETURN to_json((j #>> '{}')::numeric::text)::text;
  END IF;
  RETURN j::text;  -- string or boolean, already in JSON form
END $$;

-- The hashed content of an event: every column except the hashes themselves.
CREATE FUNCTION pw.audit_hash(e pw.audit_event) RETURNS bytea
LANGUAGE sql IMMUTABLE SET search_path = pw, pg_temp
AS $$
  SELECT sha256(convert_to(encode(e.prev_hash, 'hex') || pw.canonical_json(jsonb_build_object(
    'v', e.hash_version, 'tenant', e.tenant_id, 'seq', e.seq, 'id', e.id,
    'time', to_char(e.occurred_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
    'site', e.site_id, 'actor_type', e.actor_type, 'user_subject', e.user_subject,
    'user_role', e.user_role, 'agent_id', e.agent_id, 'agent_version', e.agent_version,
    'model', e.model, 'prompt_version', e.prompt_version, 'run_id', e.run_id,
    'action', e.action, 'scope', e.scope, 'tool', e.tool, 'parameters', e.parameters,
    'decision', e.decision, 'deny_reasons', to_jsonb(e.deny_reasons), 'outcome', e.outcome,
    'row_count', e.row_count, 'latency_ms', e.latency_ms, 'input_tokens', e.input_tokens,
    'output_tokens', e.output_tokens, 'cost_estimated', e.cost_estimated,
    'trace_id', e.trace_id)), 'UTF8'))
$$;

-- The only way to write audit. Locks the tenant's chain head, so concurrent appends form
-- one linear chain. Unknown fields are refused rather than silently dropped.
CREATE FUNCTION pw.append_audit(p_tenant uuid, p_event jsonb) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pw, pg_temp
AS $$
DECLARE
  e pw.audit_event;
  head pw.audit_chain_head;
  unknown text;
BEGIN
  PERFORM pw.assert_tenant(p_tenant);
  SELECT string_agg(k, ', ') INTO unknown FROM jsonb_object_keys(p_event) AS k
   WHERE k NOT IN ('site_id', 'actor_type', 'user_subject', 'user_role', 'agent_id', 'agent_version',
                   'model', 'prompt_version', 'run_id', 'action', 'scope', 'tool', 'parameters',
                   'decision', 'deny_reasons', 'outcome', 'row_count', 'latency_ms', 'input_tokens',
                   'output_tokens', 'cost_estimated', 'trace_id');
  IF unknown IS NOT NULL THEN
    RAISE EXCEPTION 'unknown audit fields: %', unknown USING ERRCODE = 'invalid_parameter_value';
  END IF;

  INSERT INTO pw.audit_chain_head (tenant_id) VALUES (p_tenant) ON CONFLICT DO NOTHING;
  SELECT * INTO head FROM pw.audit_chain_head WHERE tenant_id = p_tenant FOR UPDATE;

  e := jsonb_populate_record(NULL::pw.audit_event, p_event);
  e.tenant_id := p_tenant;
  e.seq := head.last_seq + 1;
  e.id := pw.uuidv7();
  e.occurred_at := clock_timestamp();
  e.hash_version := 1;
  e.prev_hash := head.last_hash;
  e.hash := pw.audit_hash(e);
  INSERT INTO pw.audit_event SELECT e.*;
  UPDATE pw.audit_chain_head SET last_seq = e.seq, last_hash = e.hash WHERE tenant_id = p_tenant;
  RETURN e.id;
END $$;

-- Recomputes the chain. Detects modified events (hash), missing events (sequence gap or a
-- head beyond the last event) and reordered events (broken link).
CREATE FUNCTION pw.verify_audit_chain(p_tenant uuid)
RETURNS TABLE (ok boolean, checked bigint, bad_seq bigint, problem text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pw, pg_temp
AS $$
DECLARE
  e pw.audit_event;
  expected_seq bigint := 1;
  expected_prev bytea := '\x0000000000000000000000000000000000000000000000000000000000000000';
  head pw.audit_chain_head;
BEGIN
  PERFORM pw.assert_tenant(p_tenant);
  FOR e IN SELECT * FROM pw.audit_event WHERE tenant_id = p_tenant ORDER BY seq LOOP
    IF e.seq <> expected_seq THEN
      RETURN QUERY SELECT false, expected_seq - 1, expected_seq, 'missing'; RETURN;
    END IF;
    IF e.prev_hash <> expected_prev THEN
      RETURN QUERY SELECT false, expected_seq - 1, e.seq, 'broken link'; RETURN;
    END IF;
    IF e.hash_version <> 1 OR e.hash <> pw.audit_hash(e) THEN
      RETURN QUERY SELECT false, expected_seq - 1, e.seq, 'modified'; RETURN;
    END IF;
    expected_prev := e.hash;
    expected_seq := expected_seq + 1;
  END LOOP;
  SELECT * INTO head FROM pw.audit_chain_head WHERE tenant_id = p_tenant;
  IF coalesce(head.last_seq, 0) <> expected_seq - 1
     OR coalesce(head.last_hash, expected_prev) <> expected_prev THEN
    RETURN QUERY SELECT false, expected_seq - 1, expected_seq, 'missing'; RETURN;
  END IF;
  RETURN QUERY SELECT true, expected_seq - 1, NULL::bigint, NULL::text;
END $$;

-- ------------------------------------------------------------ grants

REVOKE ALL ON pw.agent, pw.audit_event, pw.audit_chain_head FROM PUBLIC;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA pw FROM PUBLIC;
GRANT SELECT ON pw.agent TO pw_app;
GRANT EXECUTE ON FUNCTION pw.append_audit(uuid, jsonb), pw.verify_audit_chain(uuid) TO pw_app;
