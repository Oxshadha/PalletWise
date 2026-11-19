-- Reverses 0004. Destroys the audit log: development and CI round trip only.
DROP FUNCTION IF EXISTS pw.verify_audit_chain(uuid);
DROP FUNCTION IF EXISTS pw.append_audit(uuid, jsonb);
DROP FUNCTION IF EXISTS pw.audit_hash(pw.audit_event);
DROP FUNCTION IF EXISTS pw.canonical_json(jsonb);
DROP TABLE IF EXISTS pw.audit_event;
DROP TABLE IF EXISTS pw.audit_chain_head;
DROP FUNCTION IF EXISTS pw.audit_is_append_only();
DROP TRIGGER IF EXISTS seed_default_agent ON pw.tenant;
DROP FUNCTION IF EXISTS pw.seed_default_agent();
DROP TABLE IF EXISTS pw.agent;
