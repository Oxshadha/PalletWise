-- Building blocks for the query language (Slice 2 step 3b). Runs as pw_owner.
-- Spec: semantic-layer-and-query-dsl.md 4.2 (bin-level last pick, last movement) and 7.2
-- (demand_units by customer). Same access model as 0005.

-- Demand events keep their order line, so demand can be grouped by the ordering outlet.
-- Appended column only: the meaning of each row is unchanged (0011).
CREATE OR REPLACE VIEW sem.demand_event AS
SELECT site_id, product_id, occurred_at,
       CASE WHEN movement_type = 'PICK' THEN qty ELSE -qty END AS qty,
       order_line_id
  FROM sem.movement
 WHERE NOT order_cancelled
   AND (movement_type = 'PICK' OR (movement_type = 'TRANSFER' AND reason_code = 'PICK_REVERSAL'));

-- days_since_last_pick(product, bin) (spec 4.2): the same as the product definition, restricted to
-- picks from one bin. A separate definition: it describes a bin, not a product. Whole days.
CREATE FUNCTION sem.last_pick_by_bin(p_site uuid, p_as_of timestamptz)
RETURNS TABLE (product_id uuid, bin_id uuid, last_pick_at timestamptz, days_since_last_pick integer)
LANGUAGE sql STABLE SECURITY INVOKER SET search_path = sem, pg_temp
AS $$
  SELECT m.product_id, m.from_bin_id, max(m.occurred_at),
         floor(extract(epoch FROM p_as_of - max(m.occurred_at)) / 86400)::integer
    FROM sem.movement m
   WHERE m.site_id = p_site AND m.movement_type = 'PICK' AND NOT m.order_cancelled
     AND m.occurred_at <= p_as_of
   GROUP BY m.product_id, m.from_bin_id
$$;

-- days_since_last_move(product) (spec 4.2): the latest movement of any type. Whole days.
CREATE FUNCTION sem.last_move(p_site uuid, p_as_of timestamptz)
RETURNS TABLE (product_id uuid, last_move_at timestamptz, days_since_last_move integer)
LANGUAGE sql STABLE SECURITY INVOKER SET search_path = sem, pg_temp
AS $$
  SELECT m.product_id, max(m.occurred_at),
         floor(extract(epoch FROM p_as_of - max(m.occurred_at)) / 86400)::integer
    FROM sem.movement m
   WHERE m.site_id = p_site AND m.occurred_at <= p_as_of
   GROUP BY m.product_id
$$;

REVOKE EXECUTE ON FUNCTION sem.last_pick_by_bin(uuid, timestamptz), sem.last_move(uuid, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION sem.last_pick_by_bin(uuid, timestamptz), sem.last_move(uuid, timestamptz) TO pw_agent_read;
