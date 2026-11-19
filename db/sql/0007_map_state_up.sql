-- PalletWise map state v0 (Slice 1): one state per active bin, from the semantic layer.
-- Specs: ux-screens.md 5, semantic-layer-and-query-dsl.md 4.1. Runs as pw_owner.
-- Slice 1 states: NORMAL and EMPTY. LOW_STOCK and OVER_CAPACITY arrive with their rules
-- (semantic spec 4.4 and 6) in Slice 2; the browser never derives a state itself.

CREATE FUNCTION sem.bin_states(p_site uuid, p_as_of timestamptz)
RETURNS TABLE (bin_id uuid, state text, on_hand integer)
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path = sem, pg_temp
AS $$
BEGIN
  PERFORM sem.assert_current(p_site, p_as_of);
  RETURN QUERY
  SELECT l.bin_id,
         CASE WHEN coalesce(sum(s.on_hand), 0) = 0 THEN 'EMPTY' ELSE 'NORMAL' END,
         coalesce(sum(s.on_hand), 0)::integer
    FROM sem.location l
    LEFT JOIN sem.stock s ON s.bin_id = l.bin_id
   WHERE l.site_id = p_site AND l.is_active
   GROUP BY l.bin_id;
END $$;

REVOKE EXECUTE ON FUNCTION sem.bin_states(uuid, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION sem.bin_states(uuid, timestamptz) TO pw_agent_read;
