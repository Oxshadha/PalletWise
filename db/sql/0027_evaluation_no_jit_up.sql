-- Exception evaluation without JIT compilation (Slice 3 step 7, measured at 180 days). Runs as
-- pw_owner.
--
-- The candidate query joins several rule functions; PostgreSQL estimates 1,000 rows for each
-- function it cannot look into, so the estimated cost (about 1.1 million) passes the JIT
-- threshold and every evaluation spent 1 to 3 seconds compiling for a query that runs in about
-- 150 ms (p95 2.1 s against the 2 s budget, measured on the reference machine). JIT is turned off
-- for these functions only; a function's SET applies to every statement it runs, nested ones
-- included, and is restored when it returns.
ALTER FUNCTION pw.evaluate_site_exceptions(uuid) SET jit = off;
ALTER FUNCTION sem.exception_candidates(uuid, timestamptz, uuid) SET jit = off;
