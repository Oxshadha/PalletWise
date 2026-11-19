-- PalletWise copilot runtime data (Slice 1): conversations, runs, feedback, model usage,
-- the daily token budget and rate limits. Runs as pw_owner.
-- Specs: agent-tools-and-api.md 3, 9.1, 12 and 17; evals-and-observability.md 14;
-- requirements FR-COP-06, FR-COP-11, FR-COP-14, NFR-CST-01, NFR-CST-02.
--
-- Times here are wall-clock times: they record when things really happened and drive money
-- and abuse limits, which follow real time, not the simulated site clock.

-- The signed-in user, set per transaction like the tenant (FR-COP-11: each user sees only
-- their own conversations). Fails closed when missing.
CREATE FUNCTION pw.current_subject() RETURNS text
LANGUAGE plpgsql STABLE SET search_path = pw, pg_temp
AS $$
DECLARE v text := current_setting('app.user_subject', true);
BEGIN
  IF v IS NULL OR v = '' THEN
    RAISE EXCEPTION 'no user context' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN v;
END $$;

-- ------------------------------------------------------------ conversations (pw_memory)

CREATE TABLE pw.conversation (
  id            uuid PRIMARY KEY,
  tenant_id     uuid NOT NULL REFERENCES pw.tenant (id),
  site_id       uuid NOT NULL,
  user_subject  text NOT NULL,
  title         text NOT NULL CHECK (length(title) <= 60),
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, id),
  FOREIGN KEY (tenant_id, site_id) REFERENCES pw.site (tenant_id, id)
);
CREATE INDEX conversation_owner ON pw.conversation (tenant_id, site_id, user_subject, updated_at DESC);

CREATE TABLE pw.copilot_run (
  id                uuid PRIMARY KEY,
  tenant_id         uuid NOT NULL,
  conversation_id   uuid NOT NULL,
  site_id           uuid NOT NULL,
  user_subject      text NOT NULL,
  seq               integer NOT NULL CHECK (seq > 0),
  question          text NOT NULL CHECK (length(question) <= 1000),
  path              text CHECK (path IN ('A', 'B', 'C')),
  status            text NOT NULL CHECK (status IN ('RUNNING', 'COMPLETED', 'CANCELLED', 'FAILED')),
  outcome           text CHECK (outcome IN ('ANSWER', 'NO_RESULTS', 'DECLINE', 'STALE', 'DENIED', 'ERROR')),
  answer_text       text,
  results           jsonb NOT NULL DEFAULT '[]',   -- result envelopes, with highlights and evidence
  suggestions       jsonb NOT NULL DEFAULT '[]',
  entities          jsonb NOT NULL DEFAULT '[]',   -- the working set this run produced (max 500)
  error_code        text,
  trace_id          text NOT NULL,
  agent_id          text NOT NULL,
  agent_version     text,
  prompt_version    text,
  cancel_requested  boolean NOT NULL DEFAULT false,
  created_at        timestamptz NOT NULL DEFAULT now(),
  finished_at       timestamptz,
  UNIQUE (conversation_id, seq),
  FOREIGN KEY (tenant_id, conversation_id) REFERENCES pw.conversation (tenant_id, id)
);

CREATE TABLE pw.run_feedback (
  run_id        uuid PRIMARY KEY REFERENCES pw.copilot_run (id),
  tenant_id     uuid NOT NULL REFERENCES pw.tenant (id),
  user_subject  text NOT NULL,
  rating        text NOT NULL CHECK (rating IN ('HELPFUL', 'NOT_HELPFUL')),
  comment       text CHECK (length(comment) <= 1000),
  trace_id      text NOT NULL,
  created_at    timestamptz NOT NULL DEFAULT now()
);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['conversation', 'copilot_run', 'run_feedback'] LOOP
    EXECUTE format('ALTER TABLE pw.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE pw.%I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format(
      'CREATE POLICY owner_isolation ON pw.%I '
      'USING (tenant_id = (SELECT pw.current_tenant()) AND user_subject = (SELECT pw.current_subject())) '
      'WITH CHECK (tenant_id = (SELECT pw.current_tenant()) AND user_subject = (SELECT pw.current_subject()))', t);
  END LOOP;
END $$;

-- ------------------------------------------------------------ model usage (evals spec 14)

CREATE TABLE pw.model_usage (
  id              uuid PRIMARY KEY,
  tenant_id       uuid NOT NULL REFERENCES pw.tenant (id),
  site_id         uuid,
  run_id          uuid,
  agent_id        text NOT NULL,
  agent_version   text,
  purpose         text NOT NULL CHECK (purpose IN ('WORDING', 'AGENT', 'CLASSIFIER')),
  model           text NOT NULL,
  region          text NOT NULL,
  input_tokens    integer NOT NULL CHECK (input_tokens >= 0),
  output_tokens   integer NOT NULL CHECK (output_tokens >= 0),
  cached_tokens   integer NOT NULL DEFAULT 0 CHECK (cached_tokens >= 0),
  cost_estimated  numeric(14, 6) NOT NULL,
  latency_ms      integer NOT NULL,
  outcome         text NOT NULL,
  occurred_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX model_usage_time ON pw.model_usage (tenant_id, occurred_at);

-- Tokens reserved and used per agent and site-local (wall-clock) day.
CREATE TABLE pw.agent_budget_day (
  tenant_id  uuid NOT NULL REFERENCES pw.tenant (id),
  agent_id   text NOT NULL,
  day        date NOT NULL,
  reserved   bigint NOT NULL DEFAULT 0 CHECK (reserved >= 0),
  used       bigint NOT NULL DEFAULT 0 CHECK (used >= 0),
  PRIMARY KEY (tenant_id, agent_id, day)
);

-- ------------------------------------------------------------ rate limits

-- Fixed one-minute windows per user and key (a tool name, or "run"). Rows older than an
-- hour are removed as new windows open.
CREATE TABLE pw.rate_window (
  tenant_id     uuid NOT NULL,
  user_subject  text NOT NULL,
  key           text NOT NULL,
  window_start  timestamptz NOT NULL,
  hits          integer NOT NULL,
  PRIMARY KEY (tenant_id, user_subject, key, window_start)
);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['model_usage', 'agent_budget_day', 'rate_window'] LOOP
    EXECUTE format('ALTER TABLE pw.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE pw.%I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format(
      'CREATE POLICY tenant_isolation ON pw.%I USING (tenant_id = (SELECT pw.current_tenant())) '
      'WITH CHECK (tenant_id = (SELECT pw.current_tenant()))', t);
  END LOOP;
END $$;

-- Atomic reservation (evals spec 14): the day row is locked, so two concurrent calls can never
-- both pass on the same remainder. Returns false when the estimate does not fit.
CREATE FUNCTION pw.reserve_budget(p_tenant uuid, p_agent text, p_day date, p_estimate bigint, p_budget bigint)
RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pw, pg_temp
AS $$
DECLARE d pw.agent_budget_day;
BEGIN
  PERFORM pw.assert_tenant(p_tenant);
  IF p_estimate <= 0 OR p_budget <= 0 THEN
    RAISE EXCEPTION 'estimate and budget must be positive' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  INSERT INTO pw.agent_budget_day (tenant_id, agent_id, day) VALUES (p_tenant, p_agent, p_day)
  ON CONFLICT DO NOTHING;
  SELECT * INTO d FROM pw.agent_budget_day
   WHERE tenant_id = p_tenant AND agent_id = p_agent AND day = p_day FOR UPDATE;
  IF d.used + d.reserved + p_estimate > p_budget THEN
    RETURN false;
  END IF;
  UPDATE pw.agent_budget_day SET reserved = reserved + p_estimate
   WHERE tenant_id = p_tenant AND agent_id = p_agent AND day = p_day;
  RETURN true;
END $$;

-- Releases a reservation and records what the provider reported (0 for a failed or
-- cancelled call).
CREATE FUNCTION pw.settle_budget(p_tenant uuid, p_agent text, p_day date, p_reserved bigint, p_used bigint)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pw, pg_temp
AS $$
BEGIN
  PERFORM pw.assert_tenant(p_tenant);
  IF p_reserved < 0 OR p_used < 0 THEN
    RAISE EXCEPTION 'reserved and used must not be negative' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  UPDATE pw.agent_budget_day
     SET reserved = greatest(reserved - p_reserved, 0), used = used + p_used
   WHERE tenant_id = p_tenant AND agent_id = p_agent AND day = p_day;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'no budget day to settle' USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
END $$;

CREATE FUNCTION pw.record_model_usage(p_tenant uuid, p_usage jsonb) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pw, pg_temp
AS $$
DECLARE u pw.model_usage;
BEGIN
  PERFORM pw.assert_tenant(p_tenant);
  u := jsonb_populate_record(NULL::pw.model_usage, p_usage);
  u.id := pw.uuidv7();
  u.tenant_id := p_tenant;
  u.occurred_at := now();
  INSERT INTO pw.model_usage SELECT u.*;
  RETURN u.id;
END $$;

-- Counts a hit in the current minute; true while the user is within the limit.
CREATE FUNCTION pw.rate_limit_hit(p_tenant uuid, p_subject text, p_key text, p_limit integer)
RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pw, pg_temp
AS $$
DECLARE
  w timestamptz := date_trunc('minute', clock_timestamp());
  n integer;
BEGIN
  PERFORM pw.assert_tenant(p_tenant);
  DELETE FROM pw.rate_window
   WHERE tenant_id = p_tenant AND user_subject = p_subject AND window_start < w - interval '1 hour';
  INSERT INTO pw.rate_window AS r (tenant_id, user_subject, key, window_start, hits)
  VALUES (p_tenant, p_subject, p_key, w, 1)
  ON CONFLICT (tenant_id, user_subject, key, window_start) DO UPDATE SET hits = r.hits + 1
  RETURNING hits INTO n;
  RETURN n <= p_limit;
END $$;

-- ------------------------------------------------------------ grants

REVOKE ALL ON pw.conversation, pw.copilot_run, pw.run_feedback, pw.model_usage,
              pw.agent_budget_day, pw.rate_window FROM PUBLIC;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA pw FROM PUBLIC;

-- pw_memory: the conversation store (CWM 11.3). No delete: retention is a later job.
GRANT USAGE ON SCHEMA pw TO pw_memory;
GRANT SELECT, INSERT, UPDATE ON pw.conversation, pw.copilot_run, pw.run_feedback TO pw_memory;
GRANT EXECUTE ON FUNCTION pw.current_tenant(), pw.current_subject() TO pw_memory;

-- pw_app: usage, budget and rate limits only through the functions above.
GRANT EXECUTE ON FUNCTION
  pw.reserve_budget(uuid, text, date, bigint, bigint),
  pw.settle_budget(uuid, text, date, bigint, bigint),
  pw.record_model_usage(uuid, jsonb),
  pw.rate_limit_hit(uuid, text, text, integer)
TO pw_app;
