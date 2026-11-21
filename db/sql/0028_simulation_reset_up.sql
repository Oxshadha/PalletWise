-- Simulation reset, PalletWise side (Slice 4 step 4, CWM 7.4 step 1, simulator spec 7.1).
-- Runs as pw_owner.
--
-- pw_app gains one function, reset_simulated_site(), and no table privilege. The API calls it
-- for a holder of simulation.control (slice-4-acceptance D3) after the simulator has ended the
-- old run, and appends the audit entry in the same transaction. The simulation admin service
-- then loads the new BUILDING run exactly as it loads the first one.

-- The same Idempotency-Key never resets twice (D9): the new run records the key that made it.
ALTER TABLE pw.simulation_run ADD COLUMN reset_key text;
CREATE UNIQUE INDEX simulation_run_reset_key ON pw.simulation_run (site_id, reset_key)
  WHERE reset_key IS NOT NULL;

-- Outcomes: 'reset' (old_run ended, new_run BUILDING), 'repeated' (this key already reset the site:
-- the run it made, nothing changed), 'not_found', 'not_simulated', 'loading' (a load or reset is
-- still running), 'busy' (another write holds the site's clock for longer than the wait).
CREATE FUNCTION pw.reset_simulated_site(p_site uuid, p_new_run uuid, p_subject text, p_key text)
RETURNS TABLE (outcome text, old_run uuid, new_run uuid, clock_start timestamptz)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pw, pg_temp SET lock_timeout = '5s'
AS $$
DECLARE
  c   pw.site_clock;
  s   pw.site;
  r   pw.simulation_run;
  v_now timestamptz;
BEGIN
  IF coalesce(p_subject, '') = '' OR coalesce(p_key, '') = '' THEN
    RAISE EXCEPTION 'a reset needs the user and a key' USING ERRCODE = '22023';
  END IF;
  SELECT * INTO r FROM pw.simulation_run WHERE site_id = p_site AND reset_key = p_key;
  IF FOUND THEN
    RETURN QUERY SELECT 'repeated', NULL::uuid, r.id, r.clock_start;
    RETURN;
  END IF;
  BEGIN
    -- The clock row serializes the reset with ingestion (each TICK updates it) and with a
    -- second reset; one waits briefly, then is refused as busy.
    SELECT * INTO c FROM pw.site_clock WHERE site_id = p_site FOR UPDATE;
  EXCEPTION WHEN lock_not_available THEN
    RETURN QUERY SELECT 'busy', NULL::uuid, NULL::uuid, NULL::timestamptz;
    RETURN;
  END;
  IF NOT FOUND THEN
    RETURN QUERY SELECT 'not_found', NULL::uuid, NULL::uuid, NULL::timestamptz;
    RETURN;
  END IF;
  IF c.mode <> 'SIMULATED' THEN
    RETURN QUERY SELECT 'not_simulated', NULL::uuid, NULL::uuid, NULL::timestamptz;
    RETURN;
  END IF;
  SELECT * INTO STRICT s FROM pw.site WHERE id = p_site;
  IF s.data_status NOT IN ('READY', 'FAILED') THEN
    RETURN QUERY SELECT 'loading', NULL::uuid, NULL::uuid, NULL::timestamptz;
    RETURN;
  END IF;
  SELECT * INTO r FROM pw.simulation_run
   WHERE site_id = p_site ORDER BY started_at DESC LIMIT 1;
  v_now := c.sim_time;

  -- 1. The data readiness gate applies from now (CWM 5.2).
  UPDATE pw.site SET data_status = 'REBUILDING', data_status_detail = NULL WHERE id = p_site;

  -- 2. Proposals and tasks arrive in Slice 6; their expiry joins this function then.

  -- 3. Open exceptions are resolved, with an event each (2.4 rule 5).
  WITH gone AS (
    UPDATE pw.exception_record x
       SET status = 'RESOLVED', resolved_at = v_now, last_evaluated_at = v_now,
           resolution_reason = 'SIMULATION_RESET', row_version = x.row_version + 1
     WHERE x.site_id = p_site AND x.status <> 'RESOLVED'
    RETURNING x.*
  )
  INSERT INTO pw.exception_evaluation (tenant_id, site_id, exception_id, evaluated_at, event, status,
                                       score, score_components, settings_version_id)
  SELECT g.tenant_id, g.site_id, g.id, v_now, 'RESOLVED', 'RESOLVED', g.score, g.score_components,
         g.settings_version_id
    FROM gone g;
  DELETE FROM pw.exception_engine_state WHERE site_id = p_site;

  -- 4. The connector-sourced transactional data (CWM 2, the documented exception to no deletes).
  --    Exception order links keep their order_no snapshot (ON DELETE SET NULL).
  DELETE FROM pw.movement WHERE site_id = p_site;
  DELETE FROM pw.allocation WHERE site_id = p_site;
  DELETE FROM pw.inventory_balance WHERE site_id = p_site;
  DELETE FROM pw.order_line WHERE site_id = p_site;
  DELETE FROM pw.outbound_order WHERE site_id = p_site;
  UPDATE pw.sync_state SET cursor = NULL, data_as_of = NULL, last_success_at = NULL
   WHERE site_id = p_site;

  -- 5. Master data stays: its IDs are deterministic, so the new run upserts the same rows.

  -- 6. The old run ends; the new one is BUILDING; the clock goes back to the history start, where
  --    the new run's tick markers begin (as at the first load).
  UPDATE pw.simulation_run SET status = 'ENDED', ended_at = clock_timestamp()
   WHERE site_id = p_site AND status IN ('BUILDING', 'ACTIVE');
  INSERT INTO pw.simulation_run (id, tenant_id, site_id, seed, dataset_version, history_start,
                                 clock_start, status, started_at, started_by, reset_key)
  VALUES (p_new_run, s.tenant_id, p_site, r.seed, r.dataset_version, r.history_start, r.clock_start,
          'BUILDING', clock_timestamp(), p_subject, p_key);
  UPDATE pw.site_clock SET sim_time = r.history_start, state = 'PAUSED', row_version = row_version + 1
   WHERE site_id = p_site;

  -- 7. The audit entry is appended by the caller in this transaction.
  RETURN QUERY SELECT 'reset', r.id, p_new_run, r.clock_start;
END $$;

REVOKE EXECUTE ON FUNCTION pw.reset_simulated_site(uuid, uuid, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION pw.reset_simulated_site(uuid, uuid, text, text) TO pw_app;
