-- Reverses 0003. Ends every session and pending login.
DROP FUNCTION IF EXISTS pw.create_login(bytea, text, text, text, interval);
DROP FUNCTION IF EXISTS pw.consume_login(bytea);
DROP FUNCTION IF EXISTS pw.create_session(bytea, text, text, uuid, uuid[], text, bytea, text, interval);
DROP FUNCTION IF EXISTS pw.touch_session(bytea, interval, interval);
DROP FUNCTION IF EXISTS pw.lock_session(bytea, interval);
DROP FUNCTION IF EXISTS pw.refresh_session(bytea, text, text, uuid, uuid[], text, bytea, text);
DROP FUNCTION IF EXISTS pw.end_session(bytea);
DROP TABLE IF EXISTS pw.web_session;
DROP TABLE IF EXISTS pw.web_login;
