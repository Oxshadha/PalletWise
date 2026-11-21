-- Daily brief (Slice 3 step 5, slice-3-acceptance 2.2 as revised 2026-11-21, D6). Runs as pw_owner.
--
-- One brief per site, site-local business date and simulation run, read by every Manager and
-- Admin of the site (product owner revision: generated once per site per day, not per user). A
-- simulation reset starts a new run, so the day gets a new brief and the old one is never served
-- as the new warehouse state. Written only by pw_brief (AGENTS.md rule 7).
--
-- Generation (product owner review of step 5): no database transaction is held across the model
-- call. A short transaction claims the generation with a lease; the facts are read and worded
-- outside any pw_brief transaction; a second short transaction stores the complete brief and
-- clears the claim. A concurrent request waits while a valid lease exists and reads the stored
-- brief; a crashed generator's lease expires and the next request takes over. Only a complete
-- brief is ever stored or readable. Items are stored with their rows, evidence and highlights,
-- so every later read returns the same grounded snapshot.

CREATE TABLE pw.daily_brief (
  id                   uuid PRIMARY KEY,
  tenant_id            uuid NOT NULL,
  site_id              uuid NOT NULL,
  business_date        date NOT NULL,
  simulation_run_id    uuid,  -- null on a site fed by a real WMS
  items                jsonb NOT NULL CHECK (jsonb_typeof(items) = 'array'),
  wording_source       text NOT NULL CHECK (wording_source IN ('model', 'template', 'mixed')),
  data_watermark       text NOT NULL,
  as_of                timestamptz NOT NULL,
  settings_version_id  uuid NOT NULL,
  prompt_version       text NOT NULL,
  generated_at         timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (tenant_id, site_id) REFERENCES pw.site (tenant_id, id),
  FOREIGN KEY (tenant_id, site_id, simulation_run_id) REFERENCES pw.simulation_run (tenant_id, site_id, id),
  FOREIGN KEY (tenant_id, site_id, settings_version_id)
    REFERENCES pw.settings_version (tenant_id, site_id, id)
);
CREATE UNIQUE INDEX daily_brief_once ON pw.daily_brief
  (tenant_id, site_id, business_date, coalesce(simulation_run_id, '00000000-0000-0000-0000-000000000000'));

-- Who is generating which brief, until when. `token` is the brief id the owner will store.
CREATE TABLE pw.daily_brief_claim (
  tenant_id      uuid NOT NULL,
  site_id        uuid NOT NULL,
  business_date  date NOT NULL,
  run_key        text NOT NULL,  -- the simulation run id, or '-' for a real WMS
  token          uuid NOT NULL,
  expires_at     timestamptz NOT NULL,
  PRIMARY KEY (tenant_id, site_id, business_date, run_key),
  FOREIGN KEY (tenant_id, site_id) REFERENCES pw.site (tenant_id, id)
);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['daily_brief', 'daily_brief_claim'] LOOP
    EXECUTE format('ALTER TABLE pw.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE pw.%I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON pw.%I USING (tenant_id = (SELECT pw.current_tenant()))'
                   ' WITH CHECK (tenant_id = (SELECT pw.current_tenant()))', t);
  END LOOP;
END $$;

-- A stored brief is never changed: a refresh returns it as it is.
CREATE FUNCTION pw.daily_brief_immutable() RETURNS trigger
LANGUAGE plpgsql SET search_path = pw, pg_temp
AS $$
BEGIN
  RAISE EXCEPTION 'a daily brief is not changed once stored' USING ERRCODE = 'insufficient_privilege';
END $$;
CREATE TRIGGER daily_brief_immutable BEFORE UPDATE ON pw.daily_brief
  FOR EACH ROW EXECUTE FUNCTION pw.daily_brief_immutable();

-- Usage of the brief's wording is told apart from the copilot's (product owner review): purpose
-- BRIEF, and the brief it was for. It still counts against the copilot's daily budget.
ALTER TABLE pw.model_usage DROP CONSTRAINT model_usage_purpose_check;
ALTER TABLE pw.model_usage ADD CONSTRAINT model_usage_purpose_check
  CHECK (purpose IN ('WORDING', 'AGENT', 'CLASSIFIER', 'BRIEF'));
ALTER TABLE pw.model_usage ADD COLUMN brief_id uuid;
COMMENT ON COLUMN pw.model_usage.brief_id IS 'The daily brief a BRIEF call worded (no foreign key: usage is recorded before the brief is stored, and a failed generation stores none).';

-- ------------------------------------------------------------ semantic building blocks

-- The site-local business date of as_of (semantic 3: today).
CREATE FUNCTION sem.business_date(p_site uuid, p_as_of timestamptz) RETURNS date
LANGUAGE sql STABLE SECURITY INVOKER SET search_path = sem, pg_temp
AS $$ SELECT (p_as_of AT TIME ZONE s.time_zone)::date FROM sem.site s WHERE s.site_id = p_site $$;

-- "Today" for "orders at risk today": the horizon in hours from as_of to just before the end of
-- the site-local day. The stockout rules count lines due at or before as_of + H; timestamps have
-- microsecond resolution, so ending the horizon 1 microsecond before midnight counts exactly the
-- lines due before midnight (an order due at 00:00 is tomorrow's), overdue ones included.
CREATE FUNCTION sem.today_horizon_hours(p_site uuid, p_as_of timestamptz) RETURNS numeric
LANGUAGE sql STABLE SECURITY INVOKER SET search_path = sem, pg_temp
AS $$
  SELECT (extract(epoch FROM
            (((p_as_of AT TIME ZONE s.time_zone)::date + 1)::timestamp AT TIME ZONE s.time_zone) - p_as_of)
          - 0.000001) / 3600
    FROM sem.site s WHERE s.site_id = p_site
$$;

-- Throughput (semantic 4.6): yesterday (the most recent operating day before today) against the
-- average of the 7 operating days before yesterday. Units picked are daily demand summed over
-- products; orders shipped are orders with a site-local shipped date of that day. The change is
-- its size in percent of the average with its direction (above, below or level), as a sentence
-- states it; null when the average is 0.
CREATE FUNCTION sem.throughput(p_site uuid, p_as_of timestamptz)
RETURNS TABLE (yesterday date, units_picked bigint, orders_shipped bigint, baseline_from date,
               baseline_to date, baseline_days integer, avg_units_picked numeric,
               avg_orders_shipped numeric, units_change_pct numeric, units_direction text,
               orders_change_pct numeric, orders_direction text)
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path = sem, pg_temp
AS $$
DECLARE
  v_region text;
  v_tz text;
  v_today date;
  v_yesterday date;
BEGIN
  PERFORM sem.assert_current(p_site, p_as_of);
  SELECT s.region_code, s.time_zone INTO v_region, v_tz FROM sem.site s WHERE s.site_id = p_site;
  v_today := (p_as_of AT TIME ZONE v_tz)::date;
  SELECT max(c.date) INTO v_yesterday FROM sem.calendar c
   WHERE c.region_code = v_region AND c.is_operating AND c.date < v_today;
  IF v_yesterday IS NULL THEN
    RETURN;
  END IF;
  RETURN QUERY
  WITH base AS (
    SELECT c.date FROM sem.calendar c
     WHERE c.region_code = v_region AND c.is_operating AND c.date < v_yesterday
     ORDER BY c.date DESC LIMIT 7
  ), days AS (
    SELECT v_yesterday AS date UNION ALL SELECT date FROM base
  ), units AS (
    SELECT d.date, coalesce(sum(e.qty), 0)::bigint AS n
      FROM days d LEFT JOIN sem.demand_event e
        ON e.site_id = p_site AND (e.occurred_at AT TIME ZONE v_tz)::date = d.date
       AND e.occurred_at <= p_as_of
     GROUP BY d.date
  ), shipped AS (
    SELECT d.date,
           (SELECT count(DISTINCT l.order_id) FROM sem.order_line l
             WHERE l.site_id = p_site AND l.shipped_at <= p_as_of
               AND (l.shipped_at AT TIME ZONE v_tz)::date = d.date)::bigint AS n
      FROM days d
  ), avgs AS (
    SELECT (SELECT min(date) FROM base) AS b_from, (SELECT max(date) FROM base) AS b_to,
           (SELECT count(*)::integer FROM base) AS b_days,
           (SELECT round(avg(u.n), 1) FROM units u WHERE u.date <> v_yesterday) AS a_units,
           (SELECT round(avg(s.n), 1) FROM shipped s WHERE s.date <> v_yesterday) AS a_orders
  )
  SELECT v_yesterday, y.units, y.orders, a.b_from, a.b_to, a.b_days, a.a_units, a.a_orders,
         CASE WHEN a.a_units > 0 THEN abs(round(100 * (y.units - a.a_units) / a.a_units, 1)) END,
         CASE WHEN a.a_units > 0 THEN CASE sign(round(100 * (y.units - a.a_units) / a.a_units, 1))
                                        WHEN 1 THEN 'above' WHEN -1 THEN 'below' ELSE 'level' END END,
         CASE WHEN a.a_orders > 0 THEN abs(round(100 * (y.orders - a.a_orders) / a.a_orders, 1)) END,
         CASE WHEN a.a_orders > 0 THEN CASE sign(round(100 * (y.orders - a.a_orders) / a.a_orders, 1))
                                         WHEN 1 THEN 'above' WHEN -1 THEN 'below' ELSE 'level' END END
    FROM avgs a,
         LATERAL (SELECT (SELECT u.n FROM units u WHERE u.date = v_yesterday) AS units,
                         (SELECT sh.n FROM shipped sh WHERE sh.date = v_yesterday) AS orders) y;
END $$;

-- Data freshness per stream (CWM 10.2): when each stream last synced and how many whole minutes
-- that is behind the site time. A stream never synced has neither.
CREATE FUNCTION sem.data_freshness(p_site uuid, p_as_of timestamptz)
RETURNS TABLE (stream text, data_as_of timestamptz, minutes_behind integer)
LANGUAGE sql STABLE SECURITY INVOKER SET search_path = sem, pg_temp
AS $$
  SELECT f.stream, f.data_as_of,
         CASE WHEN f.data_as_of IS NOT NULL
              THEN greatest(0, floor(extract(epoch FROM p_as_of - f.data_as_of) / 60))::integer END
    FROM sem.stream_freshness f WHERE f.site_id = p_site ORDER BY f.stream
$$;

-- ------------------------------------------------------------ grants

REVOKE ALL ON pw.daily_brief, pw.daily_brief_claim FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION sem.business_date(uuid, timestamptz), sem.today_horizon_hours(uuid, timestamptz),
  sem.throughput(uuid, timestamptz), sem.data_freshness(uuid, timestamptz) FROM PUBLIC;

-- pw_brief: the daily brief only (AGENTS.md rule 7: its own role for its own write path).
GRANT USAGE ON SCHEMA pw TO pw_brief;
GRANT SELECT, INSERT ON pw.daily_brief TO pw_brief;
GRANT SELECT, INSERT, UPDATE, DELETE ON pw.daily_brief_claim TO pw_brief;
GRANT EXECUTE ON FUNCTION pw.current_tenant() TO pw_brief;

-- The brief's facts are read as pw_agent_read, like every site read; the data watermark is the
-- evaluation key the exception engine uses.
GRANT EXECUTE ON FUNCTION sem.business_date(uuid, timestamptz), sem.today_horizon_hours(uuid, timestamptz),
  sem.throughput(uuid, timestamptz), sem.data_freshness(uuid, timestamptz) TO pw_agent_read;
GRANT SELECT ON sem.exception_state TO pw_agent_read;
