-- Rule functions with a date range plan with the real values (Slice 2 step 7d). PL/pgSQL caches a
-- generic plan after a few calls in a session, and the generic plan ignores the range: at 180 days
-- of history, top movers over 365 days went from about 100 ms to over 1 s at p95 and hit the 3 s
-- statement timeout. Snapshot rules (zones, capacity) keep the default: planning them on every call
-- costs more than it saves (measured, docs/quality/performance).

ALTER FUNCTION sem.top_movers(uuid, timestamptz, uuid, integer, integer) SET plan_cache_mode = force_custom_plan;
ALTER FUNCTION sem.dead_stock(uuid, timestamptz, uuid) SET plan_cache_mode = force_custom_plan;
