-- Map activity and site figures (product owner, 2026-11-21: activity heatmap and the figures
-- strip of the assignment's wireframe). Runs as pw_owner. Calculations stay in the semantic
-- layer (AGENTS.md rule 3); the screen only colours and prints them.

-- Units picked from each location in the days up to the site time (pick movements leave the
-- location they come from). Locations with no picks are absent.
CREATE FUNCTION sem.bin_pick_activity(p_site uuid, p_as_of timestamptz, p_days integer)
RETURNS TABLE (bin_id uuid, picked_units bigint)
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path = sem, pg_temp
AS $$
BEGIN
  PERFORM sem.assert_current(p_site, p_as_of);
  RETURN QUERY
  SELECT m.from_bin_id, sum(m.qty)::bigint
    FROM sem.movement m
   WHERE m.site_id = p_site AND m.movement_type = 'PICK' AND m.from_bin_id IS NOT NULL
     AND m.occurred_at > p_as_of - make_interval(days => p_days) AND m.occurred_at <= p_as_of
   GROUP BY m.from_bin_id;
END $$;

-- The site at a glance: units on hand, locations holding stock, zones holding stock, and open
-- (not resolved) exceptions.
CREATE FUNCTION sem.site_summary(p_site uuid, p_as_of timestamptz)
RETURNS TABLE (total_units bigint, stocked_locations bigint, active_zones bigint, open_exceptions bigint)
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path = sem, pg_temp
AS $$
BEGIN
  PERFORM sem.assert_current(p_site, p_as_of);
  RETURN QUERY
  SELECT coalesce(sum(s.on_hand), 0)::bigint,
         count(DISTINCT s.bin_id) FILTER (WHERE s.on_hand > 0),
         count(DISTINCT l.zone_id) FILTER (WHERE s.on_hand > 0),
         (SELECT count(*) FROM sem.exception x WHERE x.site_id = p_site AND x.status <> 'RESOLVED')
    FROM sem.stock s JOIN sem.location l ON l.bin_id = s.bin_id
   WHERE s.site_id = p_site;
END $$;

REVOKE EXECUTE ON FUNCTION sem.bin_pick_activity(uuid, timestamptz, integer), sem.site_summary(uuid, timestamptz) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION sem.bin_pick_activity(uuid, timestamptz, integer), sem.site_summary(uuid, timestamptz) TO pw_agent_read;
