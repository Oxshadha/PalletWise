-- Reverses 0012.
DROP FUNCTION IF EXISTS sem.replenish_before_dispatch(uuid, timestamptz, uuid);
DROP FUNCTION IF EXISTS sem.orders_at_risk(uuid, timestamptz, uuid, numeric);
DROP FUNCTION IF EXISTS sem.stockout_risk(uuid, timestamptz, uuid, numeric);
DROP FUNCTION IF EXISTS sem.delayed_orders(uuid, timestamptz, uuid, timestamptz);
DROP VIEW IF EXISTS sem.order_line;
