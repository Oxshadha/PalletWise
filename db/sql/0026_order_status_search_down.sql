-- Reverses 0026_order_status_search_up.sql.
DROP FUNCTION sem.search(uuid, text);
DROP FUNCTION sem.order_locations(uuid, text);
DROP FUNCTION sem.order_status(uuid, timestamptz, uuid, text);
DROP VIEW sem.allocation;
