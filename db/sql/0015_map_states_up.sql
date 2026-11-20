-- Map states from the Slice 2 rules (Slice 2 step 6). Runs as pw_owner.
-- Specs: ux-screens.md 5 (one state per bin: normal, empty, low stock, over capacity);
-- semantic-layer-and-query-dsl.md 4.4 and 6. The states come from the same functions the tools
-- use (sem.bin_utilization, sem.low_stock), so the map and an answer can never disagree.
-- Priority when several apply: over capacity, then low stock, then empty, then normal.

DROP FUNCTION sem.bin_states(uuid, timestamptz);

CREATE FUNCTION sem.bin_states(p_site uuid, p_as_of timestamptz, p_settings uuid)
RETURNS TABLE (bin_id uuid, state text, on_hand integer)
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path = sem, pg_temp
AS $$
BEGIN
  PERFORM sem.assert_current(p_site, p_as_of);
  RETURN QUERY
  WITH held AS (
    SELECT l.bin_id, coalesce(sum(s.on_hand), 0)::integer AS on_hand
      FROM sem.location l
      LEFT JOIN sem.stock s ON s.bin_id = l.bin_id
     WHERE l.site_id = p_site AND l.is_active
     GROUP BY l.bin_id
  ), over AS (
    SELECT u.bin_id FROM sem.bin_utilization(p_site, p_as_of, p_settings) u WHERE u.over_capacity
  ), low AS (
    SELECT DISTINCT ls.bin_id FROM sem.low_stock(p_site, p_as_of, p_settings) ls
  )
  SELECT h.bin_id,
         CASE WHEN h.bin_id IN (SELECT o.bin_id FROM over o) THEN 'OVER_CAPACITY'
              WHEN h.bin_id IN (SELECT w.bin_id FROM low w) THEN 'LOW_STOCK'
              WHEN h.on_hand = 0 THEN 'EMPTY'
              ELSE 'NORMAL' END,
         h.on_hand
    FROM held h;
END $$;

REVOKE EXECUTE ON FUNCTION sem.bin_states(uuid, timestamptz, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION sem.bin_states(uuid, timestamptz, uuid) TO pw_agent_read;
