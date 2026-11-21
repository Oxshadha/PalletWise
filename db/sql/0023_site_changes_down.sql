-- Reverses 0023_site_changes_up.sql.
DROP TRIGGER data_status_changed ON pw.site;
DROP TRIGGER clock_changed ON pw.site_clock;
DROP TRIGGER exceptions_changed ON pw.exception_record;
DROP TRIGGER exceptions_opened ON pw.exception_record;
DROP TRIGGER inventory_changed ON pw.inventory_balance;
DROP FUNCTION pw.site_changes_since(uuid, bigint);
DROP FUNCTION pw.site_change_trigger();
DROP FUNCTION pw.site_status_change_trigger();
DROP FUNCTION pw.note_site_change(uuid, uuid, text);
DROP FUNCTION pw.change_history_kept();
DROP TABLE pw.site_change;
DROP TABLE pw.site_change_seq;
