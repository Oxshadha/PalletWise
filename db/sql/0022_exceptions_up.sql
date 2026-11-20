-- Exception engine (Slice 3 step 1). Runs as pw_owner.
-- Specs: semantic-layer-and-query-dsl.md 6 (rules) and 6.1 (impact score); canonical warehouse
-- model 9.2 (tables) and 11.3 (pw_evaluator); slice-3-acceptance.md 2.3 (scheduling) and 2.4
-- (lifecycle).
--
-- The candidates and their impact scores are computed here, in the semantic layer, from the
-- Slice 2 rule functions (one implementation per rule). The worker only calls
-- pw.evaluate_site_exceptions(), which applies them in one transaction.

-- ------------------------------------------------------------ settings schema version 2

-- Schema version 2 adds the impact-score caps and weights (semantic 6.1). Version 1 rows stay
-- valid; new sites and the version appended below use version 2.
CREATE OR REPLACE FUNCTION pw.settings_valid(p_schema_version int, p_values jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path = pw, pg_temp
AS $$
  -- coalesce: a NULL (an unknown schema version) must refuse, and a CHECK passes on NULL.
  SELECT coalesce(p_schema_version IN (1, 2)
     AND jsonb_typeof(p_values) = 'object'
     AND (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(p_values) AS k)
         = CASE p_schema_version
             WHEN 1 THEN ARRAY['capacity_threshold_pct', 'dead_stock_days', 'slow_in_prime_days',
                               'stockout_horizon_hours', 'trailing_window_operating_days']
             WHEN 2 THEN ARRAY['capacity_threshold_pct', 'dead_stock_days', 'impact_hours_cap',
                               'impact_lines_cap', 'impact_value_cap', 'impact_weight_lines',
                               'impact_weight_urgency', 'impact_weight_value', 'slow_in_prime_days',
                               'stockout_horizon_hours', 'trailing_window_operating_days']
           END
     AND jsonb_typeof(p_values->'dead_stock_days') = 'number'
     AND (p_values->>'dead_stock_days')::numeric BETWEEN 1 AND 365
     AND (p_values->>'dead_stock_days')::numeric = trunc((p_values->>'dead_stock_days')::numeric)
     AND jsonb_typeof(p_values->'slow_in_prime_days') = 'number'
     AND (p_values->>'slow_in_prime_days')::numeric BETWEEN 1 AND 365
     AND (p_values->>'slow_in_prime_days')::numeric = trunc((p_values->>'slow_in_prime_days')::numeric)
     AND jsonb_typeof(p_values->'capacity_threshold_pct') = 'number'
     AND (p_values->>'capacity_threshold_pct')::numeric > 0
     AND (p_values->>'capacity_threshold_pct')::numeric <= 100
     AND jsonb_typeof(p_values->'stockout_horizon_hours') = 'number'
     AND (p_values->>'stockout_horizon_hours')::numeric BETWEEN 1 AND 168
     AND jsonb_typeof(p_values->'trailing_window_operating_days') = 'number'
     AND (p_values->>'trailing_window_operating_days')::numeric BETWEEN 1 AND 365
     AND (p_values->>'trailing_window_operating_days')::numeric
         = trunc((p_values->>'trailing_window_operating_days')::numeric)
     AND (p_schema_version = 1 OR (
           -- Caps greater than 0, weights 0 or greater, the sum of weights greater than 0 (6.1).
           (SELECT bool_and(jsonb_typeof(p_values->k) = 'number' AND (p_values->>k)::numeric > 0)
              FROM unnest(ARRAY['impact_lines_cap', 'impact_hours_cap', 'impact_value_cap']) AS k)
       AND (SELECT bool_and(jsonb_typeof(p_values->k) = 'number' AND (p_values->>k)::numeric >= 0)
              FROM unnest(ARRAY['impact_weight_lines', 'impact_weight_urgency',
                                'impact_weight_value']) AS k)
       AND (p_values->>'impact_weight_lines')::numeric + (p_values->>'impact_weight_urgency')::numeric
           + (p_values->>'impact_weight_value')::numeric > 0)), false)
$$;

-- Defaults of semantic 6.1: L_cap 20 lines, H_cap 48 hours, V_cap 1,000,000, all weights 1.
CREATE OR REPLACE FUNCTION pw.default_settings() RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path = pw, pg_temp
AS $$
  SELECT jsonb_build_object(
    'dead_stock_days', 30,
    'slow_in_prime_days', 14,
    'capacity_threshold_pct', 90,
    'stockout_horizon_hours', 24,
    'trailing_window_operating_days', 28,
    'impact_lines_cap', 20,
    'impact_hours_cap', 48,
    'impact_value_cap', 1000000,
    'impact_weight_lines', 1,
    'impact_weight_urgency', 1,
    'impact_weight_value', 1)
$$;

CREATE OR REPLACE FUNCTION pw.seed_default_settings() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pw, pg_temp
AS $$
BEGIN
  INSERT INTO pw.settings_version (tenant_id, site_id, version_no, schema_version, "values", created_by, note)
  VALUES (NEW.tenant_id, NEW.id, 1, 2, pw.default_settings(), 'system', 'defaults')
  ON CONFLICT DO NOTHING;
  RETURN NEW;
END $$;

-- Existing sites get a new version: their current values plus the 6.1 defaults. Versions are
-- append-only, so nothing earlier changes.
ALTER TABLE pw.settings_version NO FORCE ROW LEVEL SECURITY;
INSERT INTO pw.settings_version (tenant_id, site_id, version_no, schema_version, "values", created_by, note)
SELECT DISTINCT ON (site_id) tenant_id, site_id, version_no + 1, 2,
       pw.default_settings() || "values", 'system', 'impact score defaults (Slice 3)'
  FROM pw.settings_version
 WHERE schema_version = 1
 ORDER BY site_id, version_no DESC;
ALTER TABLE pw.settings_version FORCE ROW LEVEL SECURITY;

-- ------------------------------------------------------------ tables (CWM 9.2)

CREATE TABLE pw.exception_record (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id            uuid NOT NULL,
  site_id              uuid NOT NULL,
  simulation_run_id    uuid,
  exception_type       text NOT NULL CHECK (exception_type IN ('STOCKOUT_RISK', 'DELAYED_ORDER',
                         'LOW_STOCK', 'DEAD_STOCK', 'SLOW_IN_PRIME', 'CAPACITY_HOTSPOT',
                         'FESTIVAL_SHORTAGE')),
  subject_key          text NOT NULL,
  product_id           uuid,
  zone_id              uuid,
  order_id             uuid,
  status               text NOT NULL CHECK (status IN ('OPEN', 'ACKNOWLEDGED', 'RESOLVED')),
  resolution_reason    text CHECK (resolution_reason IN ('CONDITION_CLEARED', 'SIMULATION_RESET')),
  critical             boolean NOT NULL,
  score                numeric(5,1) NOT NULL CHECK (score BETWEEN 0 AND 100),
  score_components     jsonb NOT NULL,
  settings_version_id  uuid NOT NULL,
  first_detected_at    timestamptz NOT NULL,
  last_evaluated_at    timestamptz NOT NULL,
  resolved_at          timestamptz,
  acknowledged_by      text,
  acknowledged_at      timestamptz,
  row_version          int NOT NULL DEFAULT 0,
  CHECK ((status = 'RESOLVED') = (resolution_reason IS NOT NULL AND resolved_at IS NOT NULL)),
  CHECK ((status = 'ACKNOWLEDGED') <= (acknowledged_by IS NOT NULL AND acknowledged_at IS NOT NULL)),
  UNIQUE (tenant_id, site_id, id),
  FOREIGN KEY (tenant_id, site_id) REFERENCES pw.site (tenant_id, id),
  FOREIGN KEY (tenant_id, site_id, simulation_run_id) REFERENCES pw.simulation_run (tenant_id, site_id, id),
  FOREIGN KEY (tenant_id, product_id) REFERENCES pw.product (tenant_id, id),
  FOREIGN KEY (tenant_id, site_id, settings_version_id) REFERENCES pw.settings_version (tenant_id, site_id, id)
);
-- One non-resolved record per site, type and subject (2.4 rule 5).
CREATE UNIQUE INDEX exception_record_current
  ON pw.exception_record (site_id, exception_type, subject_key) WHERE status <> 'RESOLVED';
CREATE INDEX exception_record_ranked ON pw.exception_record (tenant_id, site_id, score DESC)
  WHERE status <> 'RESOLVED';

CREATE TABLE pw.exception_bin (
  tenant_id     uuid NOT NULL,
  site_id       uuid NOT NULL,
  exception_id  uuid NOT NULL,
  bin_id        uuid NOT NULL,
  PRIMARY KEY (exception_id, bin_id),
  FOREIGN KEY (tenant_id, site_id, exception_id) REFERENCES pw.exception_record (tenant_id, site_id, id)
    ON DELETE CASCADE,
  FOREIGN KEY (tenant_id, site_id, bin_id) REFERENCES pw.bin (tenant_id, site_id, id)
);

CREATE TABLE pw.exception_order (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id     uuid NOT NULL,
  site_id       uuid NOT NULL,
  exception_id  uuid NOT NULL,
  order_id      uuid,
  order_no      text NOT NULL,
  UNIQUE (exception_id, order_no),
  FOREIGN KEY (tenant_id, site_id, exception_id) REFERENCES pw.exception_record (tenant_id, site_id, id)
    ON DELETE CASCADE,
  -- A simulation reset replaces orders; the link stays readable through order_no.
  FOREIGN KEY (tenant_id, site_id, order_id) REFERENCES pw.outbound_order (tenant_id, site_id, id)
    ON DELETE SET NULL (order_id)
);

-- Every change of an exception (2.4 rule 6): when it opened, was rescored, acknowledged, resolved.
CREATE TABLE pw.exception_evaluation (
  id                   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant_id            uuid NOT NULL,
  site_id              uuid NOT NULL,
  exception_id         uuid NOT NULL,
  evaluated_at         timestamptz NOT NULL,
  event                text NOT NULL CHECK (event IN ('OPENED', 'RESCORED', 'ACKNOWLEDGED', 'RESOLVED')),
  status               text NOT NULL CHECK (status IN ('OPEN', 'ACKNOWLEDGED', 'RESOLVED')),
  score                numeric(5,1) NOT NULL,
  score_components     jsonb NOT NULL,
  settings_version_id  uuid NOT NULL,
  FOREIGN KEY (tenant_id, site_id, exception_id) REFERENCES pw.exception_record (tenant_id, site_id, id)
);
CREATE INDEX exception_evaluation_by_exception ON pw.exception_evaluation (exception_id, id);

CREATE FUNCTION pw.exception_evaluation_immutable() RETURNS trigger
LANGUAGE plpgsql SET search_path = pw, pg_temp
AS $$
BEGIN
  RAISE EXCEPTION 'exception evaluations are append-only' USING ERRCODE = 'insufficient_privilege';
END $$;
CREATE TRIGGER exception_evaluation_append_only BEFORE UPDATE OR DELETE ON pw.exception_evaluation
  FOR EACH ROW EXECUTE FUNCTION pw.exception_evaluation_immutable();

-- The last evaluated state per site (2.3): the same key is never evaluated twice.
CREATE TABLE pw.exception_engine_state (
  site_id           uuid PRIMARY KEY,
  tenant_id         uuid NOT NULL,
  evaluation_key    text NOT NULL,
  evaluated_at      timestamptz NOT NULL,
  FOREIGN KEY (tenant_id, site_id) REFERENCES pw.site (tenant_id, id)
);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['exception_record', 'exception_bin', 'exception_order',
                           'exception_evaluation', 'exception_engine_state'] LOOP
    EXECUTE format('ALTER TABLE pw.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE pw.%I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON pw.%I USING (tenant_id = (SELECT pw.current_tenant()))'
                   ' WITH CHECK (tenant_id = (SELECT pw.current_tenant()))', t);
  END LOOP;
END $$;

-- ------------------------------------------------------------ semantic layer

-- What identifies the state an evaluation sees (2.3): the simulation run, the data watermark
-- (every stream's cursor), the settings version and the clock floored to 15 minutes. The clock
-- is part of it because time-based rules change as the clock moves with no new data.
CREATE VIEW sem.exception_state AS
SELECT s.id AS site_id, s.data_status, c.sim_time AS as_of, cs.settings_version_id,
       r.id AS simulation_run_id,
       concat_ws('|', coalesce(r.id::text, '-'),
                 (SELECT string_agg(ss.stream || ':' || coalesce(ss.cursor, '') || ':'
                                    || coalesce(ss.data_as_of::text, ''), ',' ORDER BY ss.stream, ss.connector_id)
                    FROM pw.sync_state ss WHERE ss.site_id = s.id),
                 cs.settings_version_id::text,
                 date_bin('15 minutes', c.sim_time, TIMESTAMPTZ '2000-01-01 00:00:00+00')::text) AS evaluation_key
  FROM pw.site s
  JOIN pw.site_clock c ON c.site_id = s.id AND c.tenant_id = s.tenant_id
  JOIN sem.current_settings cs ON cs.site_id = s.id
  LEFT JOIN pw.simulation_run r ON r.site_id = s.id AND r.tenant_id = s.tenant_id AND r.status = 'ACTIVE';

-- Impact score (6.1): three components, each normalized to 0..1, weighted. U is 1 when the
-- earliest affected deadline has passed and 0 when no order is affected.
CREATE FUNCTION sem.impact_score(p_settings uuid, p_as_of timestamptz, p_lines integer,
                                 p_earliest timestamptz, p_value numeric)
RETURNS TABLE (score numeric, components jsonb)
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path = sem, pg_temp
AS $$
DECLARE
  l_cap numeric := sem.setting(p_settings, 'impact_lines_cap');
  h_cap numeric := sem.setting(p_settings, 'impact_hours_cap');
  v_cap numeric := sem.setting(p_settings, 'impact_value_cap');
  w_l numeric := sem.setting(p_settings, 'impact_weight_lines');
  w_u numeric := sem.setting(p_settings, 'impact_weight_urgency');
  w_v numeric := sem.setting(p_settings, 'impact_weight_value');
  hours numeric := CASE WHEN p_earliest IS NULL THEN NULL
                        ELSE extract(epoch FROM p_earliest - p_as_of) / 3600 END;
  l_n numeric := least(coalesce(p_lines, 0) / l_cap, 1);
  u_n numeric := CASE WHEN hours IS NULL THEN 0 WHEN hours <= 0 THEN 1
                      ELSE greatest(0, 1 - hours / h_cap) END;
  v_n numeric := least(log(1 + greatest(coalesce(p_value, 0), 0)) / log(1 + v_cap), 1);
BEGIN
  score := round(100 * (w_l * l_n + w_u * u_n + w_v * v_n) / (w_l + w_u + w_v), 1);
  components := jsonb_build_object(
    'lines', coalesce(p_lines, 0), 'hours_until_due', round(hours, 1),
    'value_at_risk', round(coalesce(p_value, 0), 2),
    'lines_normalized', round(l_n, 4), 'urgency_normalized', round(u_n, 4),
    'value_normalized', round(v_n, 4),
    'lines_cap', l_cap, 'hours_cap', h_cap, 'value_cap', v_cap,
    'weight_lines', w_l, 'weight_urgency', w_u, 'weight_value', w_v);
  RETURN NEXT;
END $$;

-- Every exception that holds now (semantic 6), with what the inbox and the map need. One row
-- per subject. FESTIVAL_SHORTAGE arrives in Slice 5.
--
-- Raw impact per type (6.1; the spec gives value at risk for shortage, dead, slow and capacity
-- types, the rest is recorded in the semantic spec change log):
--   STOCKOUT_RISK   lines and earliest deadline of the open lines due within H; shortfall x unit value
--   DELAYED_ORDER   its lines, overdue; its unshipped quantity x unit value
--   LOW_STOCK       open lines of the product due within H; (min - available) x unit value
--   DEAD_STOCK      no order; storable stock value
--   SLOW_IN_PRIME   no order; stock value in its prime bins
--   CAPACITY_HOTSPOT no order; stock value in the zone
CREATE FUNCTION sem.exception_candidates(p_site uuid, p_as_of timestamptz, p_settings uuid)
RETURNS TABLE (exception_type text, subject_key text, product_id uuid, zone_id uuid,
               order_id uuid, critical boolean, lines integer, earliest_deadline timestamptz,
               value_at_risk numeric, bin_ids uuid[], order_ids uuid[], order_nos text[])
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path = sem, pg_temp
AS $$
DECLARE
  v_h numeric := sem.setting(p_settings, 'stockout_horizon_hours');
BEGIN
  PERFORM sem.assert_current(p_site, p_as_of);
  RETURN QUERY
  WITH due_lines AS (
    -- Open lines still waiting for stock, due within the horizon (overdue included).
    SELECT l.product_id, l.order_id, l.order_no, l.dispatch_deadline
      FROM sem.order_line l
     WHERE l.site_id = p_site AND l.is_open AND l.unallocated_qty > 0
       AND l.dispatch_deadline <= p_as_of + make_interval(secs => v_h * 3600)
  ), risk AS (
    SELECT * FROM sem.stockout_risk(p_site, p_as_of, p_settings, NULL)
  ), pick_bins AS (
    -- The pick locations of a product, as the stockout tool highlights them.
    SELECT s.product_id, s.bin_id FROM sem.stock s JOIN sem.location l ON l.bin_id = s.bin_id
     WHERE s.site_id = p_site AND l.bin_role = 'PICK' AND l.is_active
     UNION
    SELECT rp.product_id, rp.pick_bin_id FROM sem.replenishment_policy rp WHERE rp.site_id = p_site
  ), low AS (
    SELECT * FROM sem.low_stock(p_site, p_as_of, p_settings)
  ), slow AS (
    SELECT * FROM sem.slow_in_prime(p_site, p_as_of, p_settings)
  ), hot AS (
    SELECT * FROM sem.capacity_hotspots(p_site, p_as_of, p_settings)
  ), late AS (
    -- Open overdue orders only: from = now leaves out orders already shipped late (history).
    SELECT * FROM sem.delayed_orders(p_site, p_as_of, p_settings, p_as_of)
  )
  SELECT 'STOCKOUT_RISK', 'product:' || r.product_id, r.product_id, NULL::uuid, NULL::uuid,
         (NOT r.fixable OR r.earliest_deadline <= p_as_of + interval '4 hours'),
         r.lines, r.earliest_deadline, r.shortfall * p.unit_value,
         ARRAY(SELECT b.bin_id FROM pick_bins b WHERE b.product_id = r.product_id ORDER BY b.bin_id),
         ARRAY(SELECT DISTINCT d.order_id FROM due_lines d WHERE d.product_id = r.product_id),
         ARRAY(SELECT DISTINCT d.order_no FROM due_lines d WHERE d.product_id = r.product_id ORDER BY 1)
    FROM risk r JOIN sem.product p ON p.product_id = r.product_id
  UNION ALL
  SELECT 'DELAYED_ORDER', 'order:' || o.order_id, NULL, NULL, o.order_id, true, o.lines,
         o.dispatch_deadline,
         (SELECT coalesce(sum(greatest(l.qty_ordered - l.qty_shipped, 0) * p.unit_value), 0)
            FROM sem.order_line l JOIN sem.product p ON p.product_id = l.product_id
           WHERE l.order_id = o.order_id),
         ARRAY[]::uuid[], ARRAY[o.order_id], ARRAY[o.order_no]
    FROM late o
  UNION ALL
  SELECT 'LOW_STOCK', 'product:' || lo.product_id || ':bin:' || lo.bin_id, lo.product_id, NULL, NULL,
         false,
         (SELECT count(*)::integer FROM due_lines d WHERE d.product_id = lo.product_id),
         (SELECT min(d.dispatch_deadline) FROM due_lines d WHERE d.product_id = lo.product_id),
         greatest(lo.min_qty - lo.available, 0) * p.unit_value,
         ARRAY[lo.bin_id],
         ARRAY(SELECT DISTINCT d.order_id FROM due_lines d WHERE d.product_id = lo.product_id),
         ARRAY(SELECT DISTINCT d.order_no FROM due_lines d WHERE d.product_id = lo.product_id ORDER BY 1)
    FROM low lo JOIN sem.product p ON p.product_id = lo.product_id
  UNION ALL
  SELECT 'DEAD_STOCK', 'product:' || ds.product_id, ds.product_id, NULL, NULL, false, 0, NULL,
         ds.stock_value,
         ARRAY(SELECT l.bin_id FROM sem.stock s JOIN sem.location l ON l.bin_id = s.bin_id
                WHERE s.site_id = p_site AND l.storable AND s.on_hand > 0
                  AND s.product_id = ds.product_id ORDER BY l.bin_id),
         ARRAY[]::uuid[], ARRAY[]::text[]
    FROM sem.dead_stock(p_site, p_as_of, p_settings) ds
  UNION ALL
  SELECT 'SLOW_IN_PRIME', 'product:' || sl.product_id, sl.product_id, NULL, NULL, false, 0, NULL,
         sum(sl.on_hand) * min(p.unit_value), array_agg(sl.bin_id ORDER BY sl.bin_id),
         ARRAY[]::uuid[], ARRAY[]::text[]
    FROM slow sl JOIN sem.product p ON p.product_id = sl.product_id
   GROUP BY sl.product_id
  UNION ALL
  SELECT 'CAPACITY_HOTSPOT', 'zone:' || h.zone_id, NULL, h.zone_id, NULL, h.over_capacity_bins > 0,
         0, NULL,
         (SELECT coalesce(sum(s.on_hand * p.unit_value), 0)
            FROM sem.stock s JOIN sem.location l ON l.bin_id = s.bin_id
            JOIN sem.product p ON p.product_id = s.product_id
           WHERE s.site_id = p_site AND l.zone_id = h.zone_id),
         ARRAY(SELECT b.bin_id FROM sem.bin_utilization(p_site, p_as_of, p_settings) b
                WHERE b.zone_id = h.zone_id AND b.over_capacity ORDER BY b.bin_id),
         ARRAY[]::uuid[], ARRAY[]::text[]
    FROM hot h;
END $$;

-- ------------------------------------------------------------ applying an evaluation

-- One candidate with its score and the record it maps to (an existing current record, or a
-- new id). The evaluation builds the set once and every statement reads it, with no temporary
-- table (the evaluator role has no TEMP privilege).
CREATE TYPE pw.exception_candidate AS (
  id uuid, is_new boolean, exception_type text, subject_key text, product_id uuid, zone_id uuid,
  order_id uuid, critical boolean, bin_ids uuid[], order_nos text[], score numeric, components jsonb
);

-- One evaluation of one site in one transaction (2.3): lock, compare the key, apply the
-- candidates, record the key. Readers never see a partial result. Lifecycle as 2.4:
-- acknowledged records keep their status, cleared records resolve, a returning condition
-- opens a new occurrence, records of an earlier simulation run resolve as SIMULATION_RESET.
-- Returns what changed; 'unchanged' when the key was already evaluated, 'not_ready' when the
-- site's data is not READY.
CREATE FUNCTION pw.evaluate_site_exceptions(p_site uuid)
RETURNS TABLE (outcome text, opened integer, rescored integer, resolved integer)
LANGUAGE plpgsql SECURITY INVOKER SET search_path = pw, pg_temp
AS $$
DECLARE
  st record;
  v_tenant uuid := pw.current_tenant();
  v_cand pw.exception_candidate[];
BEGIN
  -- One evaluation per site at a time; a second trigger waits, then sees the key evaluated.
  PERFORM pg_advisory_xact_lock(hashtextextended('pw.exceptions:' || p_site::text, 0));
  SELECT * INTO st FROM sem.exception_state WHERE site_id = p_site;
  IF NOT FOUND OR st.data_status <> 'READY' THEN
    RETURN QUERY SELECT 'not_ready'::text, 0, 0, 0;
    RETURN;
  END IF;
  IF EXISTS (SELECT 1 FROM pw.exception_engine_state e
              WHERE e.site_id = p_site AND e.evaluation_key = st.evaluation_key) THEN
    RETURN QUERY SELECT 'unchanged'::text, 0, 0, 0;
    RETURN;
  END IF;

  SELECT coalesce(array_agg(ROW(coalesce(x.id, gen_random_uuid()), x.id IS NULL, c.exception_type,
                                c.subject_key, c.product_id, c.zone_id, c.order_id, c.critical,
                                c.bin_ids, c.order_nos, i.score, i.components)::pw.exception_candidate),
                  ARRAY[]::pw.exception_candidate[])
    INTO v_cand
    FROM sem.exception_candidates(p_site, st.as_of, st.settings_version_id) c
    CROSS JOIN LATERAL sem.impact_score(st.settings_version_id, st.as_of, c.lines,
                                        c.earliest_deadline, c.value_at_risk) i
    LEFT JOIN pw.exception_record x
      ON x.site_id = p_site AND x.exception_type = c.exception_type
     AND x.subject_key = c.subject_key AND x.status <> 'RESOLVED'
     AND x.simulation_run_id IS NOT DISTINCT FROM st.simulation_run_id;

  -- Resolve: records of an earlier run, then records whose condition no longer holds.
  WITH gone AS (
    UPDATE pw.exception_record x
       SET status = 'RESOLVED', resolved_at = st.as_of, last_evaluated_at = st.as_of,
           resolution_reason = CASE WHEN x.simulation_run_id IS DISTINCT FROM st.simulation_run_id
                                    THEN 'SIMULATION_RESET' ELSE 'CONDITION_CLEARED' END,
           row_version = x.row_version + 1
     WHERE x.site_id = p_site AND x.status <> 'RESOLVED'
       AND NOT EXISTS (SELECT 1 FROM unnest(v_cand) c WHERE c.id = x.id)
    RETURNING x.*
  ), logged AS (
    INSERT INTO pw.exception_evaluation (tenant_id, site_id, exception_id, evaluated_at, event, status,
                                         score, score_components, settings_version_id)
    SELECT g.tenant_id, g.site_id, g.id, st.as_of, 'RESOLVED', 'RESOLVED', g.score,
           g.score_components, g.settings_version_id
      FROM gone g
    RETURNING 1
  )
  SELECT count(*) INTO resolved FROM logged;

  -- Rescore: an existing record whose score or components changed. Status is kept, so an
  -- acknowledged exception stays acknowledged.
  WITH changed AS (
    UPDATE pw.exception_record x
       SET score = c.score, score_components = c.components, critical = c.critical,
           settings_version_id = st.settings_version_id, last_evaluated_at = st.as_of,
           row_version = x.row_version + 1
      FROM unnest(v_cand) c
     WHERE c.id = x.id AND NOT c.is_new
       AND (x.score, x.score_components, x.critical, x.settings_version_id)
           IS DISTINCT FROM (c.score, c.components, c.critical, st.settings_version_id)
    RETURNING x.*
  ), logged AS (
    INSERT INTO pw.exception_evaluation (tenant_id, site_id, exception_id, evaluated_at, event, status,
                                         score, score_components, settings_version_id)
    SELECT ch.tenant_id, ch.site_id, ch.id, st.as_of, 'RESCORED', ch.status, ch.score,
           ch.score_components, ch.settings_version_id
      FROM changed ch
    RETURNING 1
  )
  SELECT count(*) INTO rescored FROM logged;
  UPDATE pw.exception_record x SET last_evaluated_at = st.as_of
    FROM unnest(v_cand) c WHERE c.id = x.id AND NOT c.is_new AND x.last_evaluated_at <> st.as_of;

  -- Open: a subject with no current record, including one whose earlier occurrence resolved.
  WITH fresh AS (
    INSERT INTO pw.exception_record (id, tenant_id, site_id, simulation_run_id, exception_type,
                                     subject_key, product_id, zone_id, order_id, status, critical,
                                     score, score_components, settings_version_id,
                                     first_detected_at, last_evaluated_at)
    SELECT c.id, v_tenant, p_site, st.simulation_run_id, c.exception_type, c.subject_key,
           c.product_id, c.zone_id, c.order_id, 'OPEN', c.critical, c.score, c.components,
           st.settings_version_id, st.as_of, st.as_of
      FROM unnest(v_cand) c
     WHERE c.is_new
    RETURNING *
  ), logged AS (
    INSERT INTO pw.exception_evaluation (tenant_id, site_id, exception_id, evaluated_at, event, status,
                                         score, score_components, settings_version_id)
    SELECT f.tenant_id, f.site_id, f.id, st.as_of, 'OPENED', 'OPEN', f.score, f.score_components,
           f.settings_version_id
      FROM fresh f
    RETURNING 1
  )
  SELECT count(*) INTO opened FROM logged;

  -- Links: the bins and orders each current exception concerns, replaced as they change.
  DELETE FROM pw.exception_bin b USING unnest(v_cand) c
   WHERE b.exception_id = c.id AND NOT b.bin_id = ANY (c.bin_ids);
  INSERT INTO pw.exception_bin (tenant_id, site_id, exception_id, bin_id)
  SELECT v_tenant, p_site, c.id, unnest(c.bin_ids) FROM unnest(v_cand) c
  ON CONFLICT DO NOTHING;
  DELETE FROM pw.exception_order o USING unnest(v_cand) c
   WHERE o.exception_id = c.id AND NOT o.order_no = ANY (c.order_nos);
  INSERT INTO pw.exception_order (tenant_id, site_id, exception_id, order_id, order_no)
  SELECT v_tenant, p_site, c.id, l.order_id, l.order_no
    FROM unnest(v_cand) c
    CROSS JOIN LATERAL (SELECT DISTINCT o.order_id, o.order_no
                          FROM sem.order_line o
                         WHERE o.site_id = p_site AND o.order_no = ANY (c.order_nos)) l
  ON CONFLICT (exception_id, order_no) DO NOTHING;

  INSERT INTO pw.exception_engine_state (site_id, tenant_id, evaluation_key, evaluated_at)
  VALUES (p_site, v_tenant, st.evaluation_key, st.as_of)
  ON CONFLICT (site_id) DO UPDATE SET evaluation_key = EXCLUDED.evaluation_key,
                                      evaluated_at = EXCLUDED.evaluated_at;
  outcome := 'evaluated';
  RETURN NEXT;
END $$;

-- ------------------------------------------------------------ grants

REVOKE ALL ON pw.exception_record, pw.exception_bin, pw.exception_order, pw.exception_evaluation,
              pw.exception_engine_state FROM PUBLIC;
REVOKE ALL ON sem.exception_state FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION sem.impact_score(uuid, timestamptz, integer, timestamptz, numeric),
  sem.exception_candidates(uuid, timestamptz, uuid), pw.evaluate_site_exceptions(uuid) FROM PUBLIC;

-- pw_evaluator (CWM 11.3): reads through the semantic layer, writes the exception tables only.
GRANT USAGE ON SCHEMA pw TO pw_evaluator;
GRANT EXECUTE ON FUNCTION pw.current_tenant() TO pw_evaluator;
GRANT SELECT ON ALL TABLES IN SCHEMA sem TO pw_evaluator;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA sem TO pw_evaluator;
GRANT SELECT, INSERT, UPDATE ON pw.exception_record TO pw_evaluator;
GRANT SELECT, INSERT, DELETE ON pw.exception_bin, pw.exception_order TO pw_evaluator;
GRANT SELECT, INSERT ON pw.exception_evaluation TO pw_evaluator;
GRANT SELECT, INSERT, UPDATE ON pw.exception_engine_state TO pw_evaluator;
GRANT EXECUTE ON FUNCTION pw.evaluate_site_exceptions(uuid) TO pw_evaluator;
