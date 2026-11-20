-- Reverses 0015: the Slice 1 states (normal and empty) without a settings version.
DROP FUNCTION IF EXISTS sem.bin_states(uuid, timestamptz, uuid);

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
