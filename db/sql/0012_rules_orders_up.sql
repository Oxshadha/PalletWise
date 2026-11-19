-- Order rules (Slice 2 step 1d). Runs as pw_owner.
-- Specs: semantic-layer-and-query-dsl.md 4.5 (orders), 6 (STOCKOUT_RISK, DELAYED_ORDER and the
-- replenishment-before-dispatch question), 8 (get_delayed_orders, get_order_stockout_risks).
--
-- Decision while implementing (recorded in the semantic spec change log): a delayed order is
-- either open past its deadline or shipped after it (4.5). Every open overdue order is returned;
-- shipped-late orders only when their deadline is at or after p_from, so a question about delays
-- is not buried under the whole history. The tool passes the start of today by default.

CREATE VIEW sem.order_line AS
SELECT o.site_id, o.id AS order_id, o.order_no, o.customer_id, c.code AS customer_code,
       c.brand AS customer_brand, o.status, o.dispatch_deadline, o.shipped_at, o.delivery_date,
       ol.id AS order_line_id, ol.line_no, ol.product_id, ol.qty_ordered, ol.qty_allocated,
       ol.qty_picked, ol.qty_shipped, ol.qty_ordered - ol.qty_allocated AS unallocated_qty,
       o.status NOT IN ('SHIPPED', 'CANCELLED') AS is_open
  FROM pw.outbound_order o
  JOIN pw.customer c ON c.id = o.customer_id AND c.tenant_id = o.tenant_id
  LEFT JOIN pw.order_line ol ON ol.order_id = o.id AND ol.tenant_id = o.tenant_id;

-- ------------------------------------------------------------ delayed orders (4.5)

CREATE FUNCTION sem.delayed_orders(p_site uuid, p_as_of timestamptz, p_settings uuid, p_from timestamptz)
RETURNS TABLE (order_id uuid, order_no text, customer_code text, customer_brand text, status text,
               dispatch_deadline timestamptz, shipped_at timestamptz, hours_late numeric,
               lines integer, units_ordered bigint)
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path = sem, pg_temp
AS $$
BEGIN
  RETURN QUERY
  SELECT l.order_id, l.order_no, l.customer_code, l.customer_brand, l.status, l.dispatch_deadline,
         l.shipped_at,
         round(extract(epoch FROM coalesce(l.shipped_at, p_as_of) - l.dispatch_deadline) / 3600, 1),
         count(l.order_line_id)::integer, coalesce(sum(l.qty_ordered), 0)::bigint
    FROM sem.order_line l
   WHERE l.site_id = p_site
     AND ((l.is_open AND p_as_of > l.dispatch_deadline)
          OR (l.status = 'SHIPPED' AND l.shipped_at > l.dispatch_deadline
              AND l.dispatch_deadline >= p_from AND l.shipped_at <= p_as_of))
   GROUP BY l.order_id, l.order_no, l.customer_code, l.customer_brand, l.status,
            l.dispatch_deadline, l.shipped_at
   ORDER BY l.status = 'SHIPPED', l.dispatch_deadline, l.order_no;
END $$;

-- ------------------------------------------------------------ STOCKOUT_RISK (6)

-- Per product: D = unallocated quantity on open lines due within H hours (overdue lines
-- included); P = pick-face available; S = storable available. At risk when D > P.
-- Fixable by replenishment when the reserve (S - P) covers the shortfall (D - P).
-- p_hours null means the settings horizon.
CREATE FUNCTION sem.stockout_risk(p_site uuid, p_as_of timestamptz, p_settings uuid, p_hours numeric)
RETURNS TABLE (product_id uuid, sku text, name text, demand_due bigint, pick_face_available bigint,
               storable_available bigint, shortfall bigint, reserve_available bigint,
               fixable boolean, true_shortage bigint, horizon_hours numeric, orders integer,
               lines integer, earliest_deadline timestamptz)
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path = sem, pg_temp
AS $$
DECLARE
  v_h numeric := coalesce(p_hours, sem.setting(p_settings, 'stockout_horizon_hours'));
BEGIN
  IF v_h <= 0 OR v_h > 168 THEN
    RAISE EXCEPTION 'horizon must be more than 0 and at most 168 hours' USING ERRCODE = 'invalid_parameter_value';
  END IF;
  RETURN QUERY
  WITH due AS (
    SELECT l.product_id, sum(l.unallocated_qty) AS d, count(DISTINCT l.order_id) AS orders,
           count(*) AS lines, min(l.dispatch_deadline) AS earliest
      FROM sem.order_line l
     WHERE l.site_id = p_site AND l.is_open AND l.unallocated_qty > 0
       AND l.dispatch_deadline <= p_as_of + make_interval(secs => v_h * 3600)
     GROUP BY l.product_id
  ), risk AS (
    SELECT due.product_id, due.d, due.orders, due.lines, due.earliest,
           coalesce(s.pick_face_available, 0) AS p, coalesce(s.storable_available, 0) AS s
      FROM due
      LEFT JOIN sem.storable_stock(p_site, p_as_of, p_settings) s ON s.product_id = due.product_id
     WHERE due.d > coalesce(s.pick_face_available, 0)
  )
  SELECT pr.product_id, pr.sku, pr.name, r.d::bigint, r.p::bigint, r.s::bigint,
         (r.d - r.p)::bigint, (r.s - r.p)::bigint, (r.s - r.p) >= (r.d - r.p),
         greatest((r.d - r.p) - (r.s - r.p), 0)::bigint, v_h, r.orders::integer, r.lines::integer,
         r.earliest
    FROM risk r
    JOIN sem.product pr ON pr.product_id = r.product_id
   ORDER BY (r.s - r.p) >= (r.d - r.p), r.earliest, (r.d - r.p) DESC, pr.sku;
END $$;

-- The orders behind the risks: "Which of today's orders are at risk?" One row per open order
-- with a line of an at-risk product due within the horizon.
CREATE FUNCTION sem.orders_at_risk(p_site uuid, p_as_of timestamptz, p_settings uuid, p_hours numeric)
RETURNS TABLE (order_id uuid, order_no text, customer_code text, customer_brand text,
               dispatch_deadline timestamptz, hours_until_due numeric, products_at_risk integer,
               units_short_of_pick_face bigint, any_true_shortage boolean)
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path = sem, pg_temp
AS $$
BEGIN
  RETURN QUERY
  WITH risk AS (SELECT * FROM sem.stockout_risk(p_site, p_as_of, p_settings, p_hours))
  SELECT l.order_id, l.order_no, l.customer_code, l.customer_brand, l.dispatch_deadline,
         round(extract(epoch FROM l.dispatch_deadline - p_as_of) / 3600, 1),
         count(DISTINCT l.product_id)::integer, sum(l.unallocated_qty)::bigint,
         bool_or(NOT r.fixable)
    FROM sem.order_line l
    JOIN risk r ON r.product_id = l.product_id
   WHERE l.site_id = p_site AND l.is_open AND l.unallocated_qty > 0
     AND l.dispatch_deadline <= p_as_of + make_interval(secs => r.horizon_hours * 3600)
   GROUP BY l.order_id, l.order_no, l.customer_code, l.customer_brand, l.dispatch_deadline
   ORDER BY l.dispatch_deadline, l.order_no;
END $$;

-- "Which items need replenishment before today's first dispatch window?" (spec 6): the same
-- rule with H set to the time until today's earliest open dispatch deadline still ahead,
-- keeping only products a replenishment can fix. No deadline left today: no rows.
CREATE FUNCTION sem.replenish_before_dispatch(p_site uuid, p_as_of timestamptz, p_settings uuid)
RETURNS TABLE (product_id uuid, sku text, name text, shortfall bigint, reserve_available bigint,
               pick_face_available bigint, demand_due bigint, first_deadline timestamptz,
               horizon_hours numeric)
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path = sem, pg_temp
AS $$
DECLARE
  v_tz text;
  v_first timestamptz;
BEGIN
  SELECT time_zone INTO v_tz FROM sem.site WHERE site_id = p_site;
  SELECT min(l.dispatch_deadline) INTO v_first
    FROM sem.order_line l
   WHERE l.site_id = p_site AND l.is_open AND l.dispatch_deadline > p_as_of
     AND (l.dispatch_deadline AT TIME ZONE v_tz)::date = (p_as_of AT TIME ZONE v_tz)::date;
  IF v_first IS NULL THEN
    RETURN;
  END IF;
  RETURN QUERY
  SELECT r.product_id, r.sku, r.name, r.shortfall, r.reserve_available, r.pick_face_available,
         r.demand_due, v_first, r.horizon_hours
    FROM sem.stockout_risk(p_site, p_as_of, p_settings,
                           extract(epoch FROM v_first - p_as_of) / 3600) r
   WHERE r.fixable
   ORDER BY r.shortfall DESC, r.sku;
END $$;

REVOKE ALL ON sem.order_line FROM PUBLIC;
GRANT SELECT ON sem.order_line TO pw_agent_read;
REVOKE EXECUTE ON FUNCTION
  sem.delayed_orders(uuid, timestamptz, uuid, timestamptz),
  sem.stockout_risk(uuid, timestamptz, uuid, numeric),
  sem.orders_at_risk(uuid, timestamptz, uuid, numeric),
  sem.replenish_before_dispatch(uuid, timestamptz, uuid)
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION
  sem.delayed_orders(uuid, timestamptz, uuid, timestamptz),
  sem.stockout_risk(uuid, timestamptz, uuid, numeric),
  sem.orders_at_risk(uuid, timestamptz, uuid, numeric),
  sem.replenish_before_dispatch(uuid, timestamptz, uuid)
TO pw_agent_read;
