-- Restores pw.create_session as it was (0003) and drops the user directory.
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
END $$;

DROP TABLE pw.user_directory;
