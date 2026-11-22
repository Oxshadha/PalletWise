-- User directory: the display name of each signed-in user, so the audit log can show a name
-- instead of an id. Runs as pw_owner.
--
-- One row per tenant and user, written only by pw.create_session (a sign-in refreshes the name);
-- no role has table privileges except the admin read role, which reads three columns of its own
-- tenant's rows (forced row-level security underneath). Nothing existing changes: the audit
-- table, the sessions and every other function stay as they were.

CREATE TABLE pw.user_directory (
  tenant_id     uuid NOT NULL REFERENCES pw.tenant (id) ON DELETE CASCADE,
  subject       text NOT NULL,
  display_name  text NOT NULL,
  updated_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, subject)
);

ALTER TABLE pw.user_directory ENABLE ROW LEVEL SECURITY;
ALTER TABLE pw.user_directory FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_functions_only ON pw.user_directory TO pw_owner USING (true) WITH CHECK (true);
CREATE POLICY admin_read ON pw.user_directory FOR SELECT TO pw_admin_read
  USING (tenant_id = (SELECT pw.current_tenant()));
GRANT SELECT (tenant_id, subject, display_name) ON pw.user_directory TO pw_admin_read;

-- Names of users who already have a session.
INSERT INTO pw.user_directory (tenant_id, subject, display_name)
SELECT DISTINCT ON (tenant_id, subject) tenant_id, subject, display_name
  FROM pw.web_session
 ORDER BY tenant_id, subject, last_seen_at DESC
ON CONFLICT DO NOTHING;

-- The same function as before, plus the name upsert after the session insert (a tenant that is
-- not provisioned still fails on the session's foreign key first, as it did).
CREATE OR REPLACE FUNCTION pw.create_session(p_id_hash bytea, p_subject text, p_role text,
                                             p_tenant uuid, p_sites uuid[], p_display_name text,
                                             p_refresh_token bytea, p_key_id text,
                                             p_absolute interval)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pw, pg_temp
AS $$
BEGIN
  DELETE FROM pw.web_session WHERE expires_at <= now();
  INSERT INTO pw.web_session (id_hash, subject, role, tenant_id, site_ids, display_name,
                              refresh_token, key_id, expires_at)
  VALUES (p_id_hash, p_subject, p_role, p_tenant, p_sites, p_display_name,
          p_refresh_token, p_key_id, now() + p_absolute);
  INSERT INTO pw.user_directory (tenant_id, subject, display_name)
  VALUES (p_tenant, p_subject, p_display_name)
  ON CONFLICT (tenant_id, subject)
  DO UPDATE SET display_name = EXCLUDED.display_name, updated_at = now();
END $$;
