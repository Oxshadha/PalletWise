DROP FUNCTION pw.reset_simulated_site(uuid, uuid, text, text);
DROP INDEX pw.simulation_run_reset_key;
ALTER TABLE pw.simulation_run DROP COLUMN reset_key;
