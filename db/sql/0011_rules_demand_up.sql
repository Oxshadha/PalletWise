-- Demand and movement rules (Slice 2 step 1c). Runs as pw_owner.
-- Specs: semantic-layer-and-query-dsl.md 3 (time), 4.2 (demand), 4.3 (cover), 6 (DEAD_STOCK,
-- SLOW_IN_PRIME), 8 (get_top_movers, get_dead_stock, get_slow_in_prime); CWM 8 (demand semantics).
--
-- Decisions while implementing (recorded in the semantic spec change log):
-- * A PICK_REVERSAL subtracts only when its order is not CANCELLED: it offsets a pick of the same
--   order line (CWM 8), and picks of cancelled orders are not demand in the first place.
-- * Days since a pick are whole days, floor((as_of - picked_at) / 1 day), in rules and on cards
--   alike, so "more than 30 days" never flags a product the card shows as 30 days.
-- * DEAD_STOCK's "first receipt more than D days ago" protects new items. A product with stock
--   and no receipt in the data is not new, so it is not protected.

-- ------------------------------------------------------------ views

CREATE VIEW sem.site AS
SELECT id AS site_id, region_code, time_zone FROM pw.site;

-- The business calendar has no tenant (one per region), so it has no row-level security.
CREATE VIEW sem.calendar AS
SELECT region_code, date, is_operating FROM pw.business_calendar;

-- Movements with the status of the order they belong to, if any.
CREATE VIEW sem.movement AS
SELECT m.site_id, m.movement_type, m.product_id, m.qty, m.from_bin_id, m.to_bin_id,
       m.occurred_at, m.reason_code, m.order_line_id,
       coalesce(o.status = 'CANCELLED', false) AS order_cancelled
  FROM pw.movement m
  LEFT JOIN pw.order_line ol ON ol.id = m.order_line_id AND ol.tenant_id = m.tenant_id
  LEFT JOIN pw.outbound_order o ON o.id = ol.order_id AND o.tenant_id = ol.tenant_id;

-- Demand events (spec 4.2): a PICK of an order that is not cancelled adds its quantity; a
-- PICK_REVERSAL of such an order subtracts it. SHIP is never demand.
CREATE VIEW sem.demand_event AS
SELECT site_id, product_id, occurred_at,
       CASE WHEN movement_type = 'PICK' THEN qty ELSE -qty END AS qty
  FROM sem.movement
 WHERE NOT order_cancelled
   AND (movement_type = 'PICK' OR (movement_type = 'TRANSFER' AND reason_code = 'PICK_REVERSAL'));

-- ------------------------------------------------------------ time

-- The trailing window (spec 3): the last W operating days before today, today excluded.
-- Demand is summed over every date from its first to its last day and divided by W (4.2).
CREATE FUNCTION sem.trailing_window(p_site uuid, p_as_of timestamptz, p_settings uuid)
RETURNS TABLE (window_from date, window_to date, operating_days integer)
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path = sem, pg_temp
AS $$
DECLARE
  v_w integer := sem.setting(p_settings, 'trailing_window_operating_days')::integer;
  v_region text;
  v_today date;
BEGIN
  SELECT s.region_code, (p_as_of AT TIME ZONE s.time_zone)::date INTO v_region, v_today
    FROM sem.site s WHERE s.site_id = p_site;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'unknown site' USING ERRCODE = 'no_data_found';
  END IF;
  IF (SELECT count(*) FROM sem.calendar c
       WHERE c.region_code = v_region AND c.is_operating AND c.date < v_today) < v_w THEN
    RAISE EXCEPTION 'the business calendar does not cover the trailing window of % operating days', v_w
      USING ERRCODE = 'no_data_found';
  END IF;
  RETURN QUERY
  WITH days AS (
    SELECT c.date FROM sem.calendar c
     WHERE c.region_code = v_region AND c.is_operating AND c.date < v_today
     ORDER BY c.date DESC LIMIT v_w
  )
  SELECT min(d.date), max(d.date), count(*)::integer FROM days d;
END $$;

-- ------------------------------------------------------------ per product building blocks

-- Storable stock per product (spec 4.1): PICK, RESERVE and BULK bins that are active.
CREATE FUNCTION sem.storable_stock(p_site uuid, p_as_of timestamptz, p_settings uuid)
RETURNS TABLE (product_id uuid, storable_on_hand bigint, storable_available bigint,
               pick_face_on_hand bigint, pick_face_available bigint)
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path = sem, pg_temp
AS $$
BEGIN
  PERFORM sem.assert_current(p_site, p_as_of);
  RETURN QUERY
  SELECT s.product_id, sum(s.on_hand)::bigint, sum(s.available)::bigint,
         coalesce(sum(s.on_hand) FILTER (WHERE l.bin_role = 'PICK'), 0)::bigint,
         coalesce(sum(s.available) FILTER (WHERE l.bin_role = 'PICK'), 0)::bigint
    FROM sem.stock s
    JOIN sem.location l ON l.bin_id = s.bin_id
   WHERE s.site_id = p_site AND l.storable
   GROUP BY s.product_id;
END $$;

-- Latest pick per product (spec 4.2): PICK movements of orders that are not cancelled;
-- reversals are ignored. Whole days between the pick and as_of.
CREATE FUNCTION sem.last_pick(p_site uuid, p_as_of timestamptz)
RETURNS TABLE (product_id uuid, last_pick_at timestamptz, days_since_last_pick integer)
LANGUAGE sql STABLE SECURITY INVOKER SET search_path = sem, pg_temp
AS $$
  SELECT m.product_id, max(m.occurred_at),
         floor(extract(epoch FROM p_as_of - max(m.occurred_at)) / 86400)::integer
    FROM sem.movement m
   WHERE m.site_id = p_site AND m.movement_type = 'PICK' AND NOT m.order_cancelled
     AND m.occurred_at <= p_as_of
   GROUP BY m.product_id
$$;

-- Demand over the trailing window and its average per operating day (spec 4.2).
CREATE FUNCTION sem.window_demand(p_site uuid, p_as_of timestamptz, p_settings uuid)
RETURNS TABLE (product_id uuid, window_demand bigint, avg_daily_demand numeric,
               window_from date, window_to date, operating_days integer)
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path = sem, pg_temp
AS $$
DECLARE
  w record;
  v_tz text;
BEGIN
  SELECT * INTO w FROM sem.trailing_window(p_site, p_as_of, p_settings);
  SELECT time_zone INTO v_tz FROM sem.site WHERE site_id = p_site;
  RETURN QUERY
  SELECT d.product_id, sum(d.qty)::bigint, sum(d.qty)::numeric / w.operating_days,
         w.window_from, w.window_to, w.operating_days
    FROM sem.demand_event d
   WHERE d.site_id = p_site
     AND (d.occurred_at AT TIME ZONE v_tz)::date BETWEEN w.window_from AND w.window_to
   GROUP BY d.product_id;
END $$;

-- ------------------------------------------------------------ rules and tools

-- Days of cover (spec 4.3): storable on_hand over average daily demand, in operating days.
-- Null when there is no recent demand. One row per product with storable stock or demand.
CREATE FUNCTION sem.days_of_cover(p_site uuid, p_as_of timestamptz, p_settings uuid)
RETURNS TABLE (product_id uuid, sku text, name text, category_name text, storable_on_hand bigint,
               window_demand bigint, avg_daily_demand numeric, days_of_cover numeric,
               window_from date, window_to date, operating_days integer)
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path = sem, pg_temp
AS $$
DECLARE
  w record;
BEGIN
  SELECT * INTO w FROM sem.trailing_window(p_site, p_as_of, p_settings);
  RETURN QUERY
  SELECT p.product_id, p.sku, p.name, p.category_name, coalesce(s.storable_on_hand, 0),
         coalesce(d.window_demand, 0), coalesce(d.avg_daily_demand, 0),
         CASE WHEN coalesce(d.avg_daily_demand, 0) <= 0 THEN NULL
              ELSE round(coalesce(s.storable_on_hand, 0) / d.avg_daily_demand, 1) END,
         w.window_from, w.window_to, w.operating_days
    FROM sem.product p
    LEFT JOIN sem.storable_stock(p_site, p_as_of, p_settings) s ON s.product_id = p.product_id
    LEFT JOIN sem.window_demand(p_site, p_as_of, p_settings) d ON d.product_id = p.product_id
   WHERE s.product_id IS NOT NULL OR d.product_id IS NOT NULL
   ORDER BY p.sku;
END $$;

-- get_top_movers (spec 8): demand_units over the last N calendar days ending at as_of (spec 3).
CREATE FUNCTION sem.top_movers(p_site uuid, p_as_of timestamptz, p_settings uuid, p_days integer, p_limit integer)
RETURNS TABLE (product_id uuid, sku text, name text, category_name text, demand_units bigint)
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path = sem, pg_temp
AS $$
BEGIN
  IF p_days NOT BETWEEN 1 AND 365 OR p_limit NOT BETWEEN 1 AND 100 THEN
    RAISE EXCEPTION 'days must be 1 to 365 and limit 1 to 100' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  RETURN QUERY
  SELECT p.product_id, p.sku, p.name, p.category_name, sum(d.qty)::bigint AS units
    FROM sem.demand_event d
    JOIN sem.product p ON p.product_id = d.product_id
   WHERE d.site_id = p_site
     AND d.occurred_at > p_as_of - make_interval(days => p_days) AND d.occurred_at <= p_as_of
   GROUP BY p.product_id, p.sku, p.name, p.category_name
  HAVING sum(d.qty) > 0
   ORDER BY units DESC, p.sku
   LIMIT p_limit;
END $$;

-- DEAD_STOCK (spec 6): storable stock, not picked for more than D days (or never), and first
-- received more than D days ago (or never received), so new items are not flagged.
CREATE FUNCTION sem.dead_stock(p_site uuid, p_as_of timestamptz, p_settings uuid)
RETURNS TABLE (product_id uuid, sku text, name text, category_name text, storable_on_hand bigint,
               stock_value numeric, last_pick_at timestamptz, days_since_last_pick integer,
               first_receipt_at timestamptz, period_days integer)
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path = sem, pg_temp
AS $$
DECLARE
  v_d integer := sem.setting(p_settings, 'dead_stock_days')::integer;
BEGIN
  RETURN QUERY
  WITH receipts AS (
    SELECT m.product_id, min(m.occurred_at) AS first_receipt_at
      FROM sem.movement m
     WHERE m.site_id = p_site AND m.movement_type = 'RECEIPT' AND m.occurred_at <= p_as_of
     GROUP BY m.product_id
  )
  SELECT p.product_id, p.sku, p.name, p.category_name, s.storable_on_hand,
         s.storable_on_hand * p.unit_value, lp.last_pick_at, lp.days_since_last_pick,
         r.first_receipt_at, v_d
    FROM sem.storable_stock(p_site, p_as_of, p_settings) s
    JOIN sem.product p ON p.product_id = s.product_id
    LEFT JOIN sem.last_pick(p_site, p_as_of) lp ON lp.product_id = s.product_id
    LEFT JOIN receipts r ON r.product_id = s.product_id
   WHERE s.storable_on_hand > 0
     AND (lp.days_since_last_pick IS NULL OR lp.days_since_last_pick > v_d)
     AND (r.first_receipt_at IS NULL OR r.first_receipt_at < p_as_of - make_interval(days => v_d))
   ORDER BY s.storable_on_hand * p.unit_value DESC, p.sku;
END $$;

-- SLOW_IN_PRIME (spec 6, Slice 2 D1): a product not picked for more than P days (product level,
-- or never) with stock in at least one bin of a prime zone. One row per such prime bin.
CREATE FUNCTION sem.slow_in_prime(p_site uuid, p_as_of timestamptz, p_settings uuid)
RETURNS TABLE (product_id uuid, sku text, name text, bin_id uuid, bin_code text, zone_id uuid,
               zone_code text, on_hand integer, last_pick_at timestamptz,
               days_since_last_pick integer, period_days integer)
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path = sem, pg_temp
AS $$
DECLARE
  v_p integer := sem.setting(p_settings, 'slow_in_prime_days')::integer;
BEGIN
  PERFORM sem.assert_current(p_site, p_as_of);
  RETURN QUERY
  SELECT p.product_id, p.sku, p.name, l.bin_id, l.bin_code, l.zone_id, l.zone_code, s.on_hand,
         lp.last_pick_at, lp.days_since_last_pick, v_p
    FROM sem.stock s
    JOIN sem.location l ON l.bin_id = s.bin_id
    JOIN sem.zone z ON z.zone_id = l.zone_id
    JOIN sem.product p ON p.product_id = s.product_id
    LEFT JOIN sem.last_pick(p_site, p_as_of) lp ON lp.product_id = s.product_id
   WHERE s.site_id = p_site AND s.on_hand > 0 AND l.is_active AND z.is_prime AND z.is_active
     AND (lp.days_since_last_pick IS NULL OR lp.days_since_last_pick > v_p)
   ORDER BY lp.days_since_last_pick DESC NULLS FIRST, p.sku, l.bin_code;
END $$;

-- ------------------------------------------------------------ grants

REVOKE ALL ON sem.site, sem.calendar, sem.movement, sem.demand_event FROM PUBLIC;
GRANT SELECT ON sem.site, sem.calendar, sem.movement, sem.demand_event TO pw_agent_read;
REVOKE EXECUTE ON FUNCTION
  sem.trailing_window(uuid, timestamptz, uuid), sem.storable_stock(uuid, timestamptz, uuid),
  sem.last_pick(uuid, timestamptz), sem.window_demand(uuid, timestamptz, uuid),
  sem.days_of_cover(uuid, timestamptz, uuid), sem.top_movers(uuid, timestamptz, uuid, integer, integer),
  sem.dead_stock(uuid, timestamptz, uuid), sem.slow_in_prime(uuid, timestamptz, uuid)
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION
  sem.trailing_window(uuid, timestamptz, uuid), sem.storable_stock(uuid, timestamptz, uuid),
  sem.last_pick(uuid, timestamptz), sem.window_demand(uuid, timestamptz, uuid),
  sem.days_of_cover(uuid, timestamptz, uuid), sem.top_movers(uuid, timestamptz, uuid, integer, integer),
  sem.dead_stock(uuid, timestamptz, uuid), sem.slow_in_prime(uuid, timestamptz, uuid)
TO pw_agent_read;
