-- One order's status and site search (Slice 3 step 6, D8; product owner decision Q1 a). Runs as
-- pw_owner. Read only; everything is computed from canonical data and the existing rules.

-- Open allocations: which bin each order line's reserved units are in (CWM 7.3).
CREATE VIEW sem.allocation AS
SELECT a.site_id, a.order_line_id, a.product_id, a.bin_id, a.qty - a.qty_consumed - a.qty_released AS open_qty
  FROM pw.allocation a
 WHERE a.status = 'ACTIVE';

-- get_order_status (semantic 4.5 and 6): one order and its lines at as_of.
--   delayed       semantic 4.5: open and past its deadline, or shipped after it
--   hours_until_due  (deadline - as_of) in hours, negative when overdue (4.5)
--   hours_late    how far past its deadline an open order is (positive), as a sentence states it
--   ordered, allocated, picked, shipped are the line's own quantities, never merged
--   at_risk       the line is open, not fully allocated, due within the stockout horizon, and its
--                 product is in sem.stockout_risk (the same rule as get_order_stockout_risks)
--   in_window     whether the line is due within that horizon at all: a line beyond it is not
--                 assessed, which is not the same as safe
-- The order number is matched exactly within the caller's site (row-level security adds the
-- tenant), so another site's or tenant's order is simply not found.
CREATE FUNCTION sem.order_status(p_site uuid, p_as_of timestamptz, p_settings uuid, p_order_no text)
RETURNS TABLE (order_id uuid, order_no text, customer_code text, status text,
               dispatch_deadline timestamptz, shipped_at timestamptz, hours_until_due numeric,
               hours_late numeric, delayed boolean, line_no integer, product_id uuid, sku text, name text,
               qty_ordered integer, qty_allocated integer, qty_picked integer, qty_shipped integer,
               in_window boolean, at_risk boolean, true_shortage boolean)
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path = sem, pg_temp
AS $$
DECLARE
  v_h numeric := sem.setting(p_settings, 'stockout_horizon_hours');
BEGIN
  PERFORM sem.assert_current(p_site, p_as_of);
  RETURN QUERY
  WITH risk AS (SELECT * FROM sem.stockout_risk(p_site, p_as_of, p_settings, NULL))
  SELECT l.order_id, l.order_no, l.customer_code, l.status, l.dispatch_deadline, l.shipped_at,
         round(extract(epoch FROM l.dispatch_deadline - p_as_of) / 3600, 1),
         CASE WHEN l.is_open AND p_as_of > l.dispatch_deadline
              THEN round(extract(epoch FROM p_as_of - l.dispatch_deadline) / 3600, 1) END,
         (l.is_open AND p_as_of > l.dispatch_deadline)
           OR (l.status = 'SHIPPED' AND l.shipped_at > l.dispatch_deadline),
         l.line_no, l.product_id, p.sku, p.name, l.qty_ordered, l.qty_allocated, l.qty_picked,
         l.qty_shipped,
         l.dispatch_deadline <= p_as_of + make_interval(secs => v_h * 3600),
         coalesce(l.is_open AND l.unallocated_qty > 0
                  AND l.dispatch_deadline <= p_as_of + make_interval(secs => v_h * 3600)
                  AND r.product_id IS NOT NULL, false),
         coalesce(l.is_open AND l.unallocated_qty > 0
                  AND l.dispatch_deadline <= p_as_of + make_interval(secs => v_h * 3600)
                  AND NOT r.fixable, false)
    FROM sem.order_line l
    LEFT JOIN sem.product p ON p.product_id = l.product_id
    LEFT JOIN risk r ON r.product_id = l.product_id
   WHERE l.site_id = p_site AND l.order_no = p_order_no
   ORDER BY l.line_no NULLS FIRST;
END $$;

-- The locations that matter for one order's open lines (product owner review: never every bin
-- holding the same products): the bins its lines are allocated in, and for quantity not yet
-- allocated, its product's pick locations. A shipped or cancelled order has none.
CREATE FUNCTION sem.order_locations(p_site uuid, p_order_no text)
RETURNS TABLE (bin_id uuid, product_id uuid, source text)
LANGUAGE sql STABLE SECURITY INVOKER SET search_path = sem, pg_temp
AS $$
  SELECT DISTINCT a.bin_id, a.product_id, 'allocated'
    FROM sem.order_line l
    JOIN sem.allocation a ON a.order_line_id = l.order_line_id AND a.site_id = p_site
   WHERE l.site_id = p_site AND l.order_no = p_order_no AND l.is_open
  UNION
  SELECT pick.bin_id, pick.product_id, 'pick_face'
    FROM sem.order_line l
    JOIN (SELECT s.product_id, s.bin_id FROM sem.stock s JOIN sem.location loc ON loc.bin_id = s.bin_id
           WHERE s.site_id = p_site AND loc.bin_role = 'PICK' AND loc.is_active
          UNION
          SELECT rp.product_id, rp.pick_bin_id FROM sem.replenishment_policy rp WHERE rp.site_id = p_site
         ) pick ON pick.product_id = l.product_id
   WHERE l.site_id = p_site AND l.order_no = p_order_no AND l.is_open AND l.unallocated_qty > 0
$$;

-- Site search (D8): SKUs, bin codes and order numbers. Exact identifiers rank first, then prefix
-- matches, then product-name matches; at most 8 results. Case-insensitive; the query is matched
-- as text (LIKE wildcards in it are escaped), never as a pattern. Products are master data of the
-- tenant (CWM 4), as for the copilot's product lookup; bins and orders are the site's own.
CREATE FUNCTION sem.search(p_site uuid, p_query text)
RETURNS TABLE (kind text, id uuid, code text, label text, match text)
LANGUAGE sql STABLE SECURITY INVOKER SET search_path = sem, pg_temp
AS $$
  WITH q AS (
    SELECT lower(btrim(p_query)) AS text,
           replace(replace(replace(lower(btrim(p_query)), '\', '\\'), '%', '\%'), '_', '\_') AS esc
  ), found AS (
    SELECT 'product' AS kind, p.product_id AS id, p.sku AS code, p.name AS label,
           CASE WHEN lower(p.sku) = q.text THEN 0 WHEN lower(p.sku) LIKE q.esc || '%' THEN 1 ELSE 2 END AS rank
      FROM sem.product p, q
     WHERE p.is_active AND q.text <> ''
       AND (lower(p.sku) LIKE q.esc || '%' OR (length(q.text) >= 2 AND lower(p.name) LIKE '%' || q.esc || '%'))
    UNION ALL
    SELECT 'bin', l.bin_id, l.bin_code, l.zone_name,
           CASE WHEN lower(l.bin_code) = q.text THEN 0 ELSE 1 END
      FROM sem.location l, q
     WHERE l.site_id = p_site AND l.is_active AND q.text <> '' AND lower(l.bin_code) LIKE q.esc || '%'
    UNION ALL
    SELECT DISTINCT 'order', o.order_id, o.order_no, o.customer_code,
           CASE WHEN lower(o.order_no) = q.text THEN 0 ELSE 1 END
      FROM sem.order_line o, q
     WHERE o.site_id = p_site AND q.text <> '' AND lower(o.order_no) LIKE q.esc || '%'
  )
  SELECT f.kind, f.id, f.code, f.label, (ARRAY['exact', 'prefix', 'name'])[f.rank + 1]
    FROM found f
   ORDER BY f.rank, length(f.code), f.code, f.kind
   LIMIT 8
$$;

REVOKE ALL ON sem.allocation FROM PUBLIC;
GRANT SELECT ON sem.allocation TO pw_agent_read;
REVOKE EXECUTE ON FUNCTION sem.order_status(uuid, timestamptz, uuid, text),
  sem.order_locations(uuid, text), sem.search(uuid, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION sem.order_status(uuid, timestamptz, uuid, text),
  sem.order_locations(uuid, text), sem.search(uuid, text) TO pw_agent_read;
