-- Planner statistics are current when a site becomes READY (CWM 7.4 step 3).
--
-- Found by the Slice 2 evaluation: right after a bulk load the tables have no statistics, the
-- planner estimates one row where there are thousands, and a rule such as top movers ran 3.7 s
-- under the agent role (cancelled by the 3 s statement timeout) instead of 0.01 s. Autovacuum
-- would analyze later; until it does, the first questions after a load or a simulation reset
-- fail. Analyzing the loaded tables before READY closes that window. The function already runs
-- as the tables' owner, so no role gains a privilege.

CREATE OR REPLACE FUNCTION pw.complete_load(p_tenant uuid, p_site uuid, p_run uuid, p_hash text)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pw, pg_temp
AS $$
BEGIN
  PERFORM pw.assert_tenant(p_tenant);
  UPDATE pw.simulation_run SET status = 'ACTIVE', dataset_hash = p_hash
   WHERE id = p_run AND site_id = p_site AND tenant_id = p_tenant
     AND status = 'BUILDING' AND history_loaded_at IS NOT NULL;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'run is not a fully loaded BUILDING run' USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
  ANALYZE pw.category, pw.product, pw.customer, pw.zone, pw.rack, pw.bin, pw.site_feature,
          pw.outbound_order, pw.order_line, pw.allocation, pw.movement, pw.inventory_balance,
          pw.replenishment_policy;
  UPDATE pw.site SET data_status = 'READY', data_status_detail = NULL
   WHERE id = p_site AND tenant_id = p_tenant;
END $$;
