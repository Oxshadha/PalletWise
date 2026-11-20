ALTER FUNCTION sem.top_movers(uuid, timestamptz, uuid, integer, integer) RESET plan_cache_mode;
ALTER FUNCTION sem.dead_stock(uuid, timestamptz, uuid) RESET plan_cache_mode;
