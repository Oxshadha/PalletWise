-- Reverses 0024_exception_ack_up.sql.
DROP FUNCTION pw.acknowledge_exception(uuid, uuid, text);
DROP VIEW sem.exception_order;
DROP VIEW sem.exception_bin;
DROP VIEW sem.exception;
