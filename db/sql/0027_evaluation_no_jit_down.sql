-- Reverses 0027_evaluation_no_jit_up.sql.
ALTER FUNCTION sem.exception_candidates(uuid, timestamptz, uuid) RESET jit;
ALTER FUNCTION pw.evaluate_site_exceptions(uuid) RESET jit;
