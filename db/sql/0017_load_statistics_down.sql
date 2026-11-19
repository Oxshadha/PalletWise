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
  UPDATE pw.site SET data_status = 'READY', data_status_detail = NULL
   WHERE id = p_site AND tenant_id = p_tenant;
END $$;
