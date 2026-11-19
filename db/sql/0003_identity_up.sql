-- PalletWise identity, Slice 1: login attempts and server-side sessions.
-- Spec: docs/specs/identity-and-access.md (revision 2), sections 2 and 3.
-- Runs as pw_owner.
--
-- A session is looked up by the hash of its id before any tenant is known, so these tables
-- cannot use the tenant policy. No role has table privileges; the API reaches them only
-- through the owner functions below (CWM 11.4). Row-level security is still forced, with a
-- policy that admits only pw_owner, so an accidental grant to another role exposes no rows.
--
-- Expiry uses database wall time, not the site clock: the site clock is simulated business
-- time, while session timeouts are a security property of real time.

CREATE TABLE pw.web_login (
  state_hash     bytea PRIMARY KEY CHECK (length(state_hash) = 32),
  nonce          text NOT NULL,
  code_verifier  text NOT NULL,
  return_to      text NOT NULL CHECK (return_to LIKE '/%'),
  created_at     timestamptz NOT NULL DEFAULT now(),
  expires_at     timestamptz NOT NULL,
  used_at        timestamptz
);
CREATE INDEX web_login_expires ON pw.web_login (expires_at);

CREATE TABLE pw.web_session (
  id_hash        bytea PRIMARY KEY CHECK (length(id_hash) = 32),
  subject        text NOT NULL,
  role           text NOT NULL CHECK (role IN ('warehouse_manager', 'admin')),
  tenant_id      uuid NOT NULL REFERENCES pw.tenant (id) ON DELETE CASCADE,
  site_ids       uuid[] NOT NULL,
  display_name   text NOT NULL,
  refresh_token  bytea NOT NULL,
  key_id         text NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT now(),
  last_seen_at   timestamptz NOT NULL DEFAULT now(),
  refreshed_at   timestamptz NOT NULL DEFAULT now(),
  expires_at     timestamptz NOT NULL
);
CREATE INDEX web_session_expires ON pw.web_session (expires_at);

ALTER TABLE pw.web_login ENABLE ROW LEVEL SECURITY;
ALTER TABLE pw.web_login FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_functions_only ON pw.web_login TO pw_owner USING (true) WITH CHECK (true);
ALTER TABLE pw.web_session ENABLE ROW LEVEL SECURITY;
ALTER TABLE pw.web_session FORCE ROW LEVEL SECURITY;
CREATE POLICY owner_functions_only ON pw.web_session TO pw_owner USING (true) WITH CHECK (true);

-- ------------------------------------------------------------ login attempts (spec 2)

CREATE FUNCTION pw.create_login(p_state_hash bytea, p_nonce text, p_verifier text,
                                p_return_to text, p_ttl interval)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pw, pg_temp
AS $$
BEGIN
  DELETE FROM pw.web_login WHERE expires_at <= now();
  INSERT INTO pw.web_login (state_hash, nonce, code_verifier, return_to, expires_at)
  VALUES (p_state_hash, p_nonce, p_verifier, p_return_to, now() + p_ttl);
END $$;

-- Marks the attempt used and returns it, only if it is unexpired and unused. A second call
-- with the same state returns nothing, so a replayed callback is rejected.
CREATE FUNCTION pw.consume_login(p_state_hash bytea)
RETURNS TABLE (nonce text, code_verifier text, return_to text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pw, pg_temp
AS $$
BEGIN
  RETURN QUERY
  UPDATE pw.web_login l SET used_at = now()
   WHERE l.state_hash = p_state_hash AND l.used_at IS NULL AND l.expires_at > now()
  RETURNING l.nonce, l.code_verifier, l.return_to;
END $$;

-- ------------------------------------------------------------ sessions (spec 3)

CREATE FUNCTION pw.create_session(p_id_hash bytea, p_subject text, p_role text, p_tenant uuid,
                                  p_sites uuid[], p_display_name text, p_refresh_token bytea,
                                  p_key_id text, p_absolute interval)
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

-- Called on every authenticated request. Enforces the idle and absolute timeouts here, not in
-- a sweep: an expired session is deleted and nothing is returned. `refresh_due` says the
-- identity must be revalidated with the identity provider (spec 3, revalidation).
CREATE FUNCTION pw.touch_session(p_id_hash bytea, p_idle interval, p_revalidate interval)
RETURNS TABLE (subject text, role text, tenant_id uuid, site_ids uuid[], display_name text,
               refresh_due boolean)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pw, pg_temp
AS $$
BEGIN
  DELETE FROM pw.web_session s
   WHERE s.id_hash = p_id_hash AND (s.expires_at <= now() OR s.last_seen_at + p_idle <= now());
  RETURN QUERY
  UPDATE pw.web_session s SET last_seen_at = now()
   WHERE s.id_hash = p_id_hash
  RETURNING s.subject, s.role, s.tenant_id, s.site_ids, s.display_name,
            s.refreshed_at + p_revalidate <= now();
END $$;

-- Locks the session row for the rest of the caller's transaction, so refreshes of one session
-- are serialized (spec 3, token refresh). `refresh_due` is re-read under the lock: a request
-- that waited for another one's refresh sees it as done.
CREATE FUNCTION pw.lock_session(p_id_hash bytea, p_revalidate interval)
RETURNS TABLE (subject text, tenant_id uuid, refresh_token bytea, key_id text,
               refresh_due boolean)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pw, pg_temp
AS $$
BEGIN
  RETURN QUERY
  SELECT s.subject, s.tenant_id, s.refresh_token, s.key_id,
         s.refreshed_at + p_revalidate <= clock_timestamp()
    FROM pw.web_session s
   WHERE s.id_hash = p_id_hash AND s.expires_at > now()
     FOR UPDATE;
END $$;

-- Stores the result of a successful refresh after revalidation. A changed subject or tenant
-- ends the session (spec 3, revalidation); returns false in that case.
CREATE FUNCTION pw.refresh_session(p_id_hash bytea, p_subject text, p_role text, p_tenant uuid,
                                   p_sites uuid[], p_display_name text, p_refresh_token bytea,
                                   p_key_id text)
RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pw, pg_temp
AS $$
BEGIN
  UPDATE pw.web_session s
     SET role = p_role, site_ids = p_sites, display_name = p_display_name,
         refresh_token = p_refresh_token, key_id = p_key_id, refreshed_at = clock_timestamp()
   WHERE s.id_hash = p_id_hash AND s.subject = p_subject AND s.tenant_id = p_tenant;
  IF FOUND THEN
    RETURN true;
  END IF;
  DELETE FROM pw.web_session s WHERE s.id_hash = p_id_hash;
  RETURN false;
END $$;

-- Ends a session and returns its encrypted refresh token, so the caller can end the session
-- at the identity provider too.
CREATE FUNCTION pw.end_session(p_id_hash bytea)
RETURNS TABLE (refresh_token bytea, key_id text)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pw, pg_temp
AS $$
BEGIN
  RETURN QUERY DELETE FROM pw.web_session s WHERE s.id_hash = p_id_hash
               RETURNING s.refresh_token, s.key_id;
END $$;

-- ------------------------------------------------------------ grants

REVOKE ALL ON pw.web_login, pw.web_session FROM PUBLIC;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA pw FROM PUBLIC;
GRANT EXECUTE ON FUNCTION
  pw.create_login(bytea, text, text, text, interval),
  pw.consume_login(bytea),
  pw.create_session(bytea, text, text, uuid, uuid[], text, bytea, text, interval),
  pw.touch_session(bytea, interval, interval),
  pw.lock_session(bytea, interval),
  pw.refresh_session(bytea, text, text, uuid, uuid[], text, bytea, text),
  pw.end_session(bytea)
TO pw_app;
