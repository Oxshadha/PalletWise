-- Reverses 0011.
DROP FUNCTION IF EXISTS sem.slow_in_prime(uuid, timestamptz, uuid);
DROP FUNCTION IF EXISTS sem.dead_stock(uuid, timestamptz, uuid);
DROP FUNCTION IF EXISTS sem.top_movers(uuid, timestamptz, uuid, integer, integer);
DROP FUNCTION IF EXISTS sem.days_of_cover(uuid, timestamptz, uuid);
DROP FUNCTION IF EXISTS sem.window_demand(uuid, timestamptz, uuid);
DROP FUNCTION IF EXISTS sem.last_pick(uuid, timestamptz);
DROP FUNCTION IF EXISTS sem.storable_stock(uuid, timestamptz, uuid);
DROP FUNCTION IF EXISTS sem.trailing_window(uuid, timestamptz, uuid);
DROP VIEW IF EXISTS sem.demand_event;
DROP VIEW IF EXISTS sem.movement;
DROP VIEW IF EXISTS sem.calendar;
DROP VIEW IF EXISTS sem.site;
