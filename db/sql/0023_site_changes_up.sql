-- Site change sequence (Slice 3 step 2, agent spec 9.2, slice-3-acceptance 2.1). Runs as pw_owner.
--
-- Every change a screen shows advances the site's change sequence and records what kind of
-- data changed, in the same transaction as the change: triggers on the tables do it, so no
-- writer can forget and no writer role gains a privilege. One sequence number per site per
-- transaction (a batch of 5,000 records is one change). NOTIFY only wakes the API; PostgreSQL
-- delivers it only after commit. The sequence is the durable source: a client that missed a
-- notification still sees the gap and refetches.

CREATE TABLE pw.site_change_seq (
  site_id    uuid PRIMARY KEY,
  tenant_id  uuid NOT NULL,
  seq        bigint NOT NULL,
  FOREIGN KEY (tenant_id, site_id) REFERENCES pw.site (tenant_id, id)
);

CREATE TABLE pw.site_change (
  tenant_id   uuid NOT NULL,
  site_id     uuid NOT NULL,
  seq         bigint NOT NULL,
  kinds       text[] NOT NULL CHECK (kinds <@ ARRAY['inventory', 'exceptions', 'clock', 'data_status']),
  xact        xid8 NOT NULL,
  changed_at  timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (site_id, seq),
  FOREIGN KEY (tenant_id, site_id) REFERENCES pw.site (tenant_id, id)
);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['site_change_seq', 'site_change'] LOOP
    EXECUTE format('ALTER TABLE pw.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE pw.%I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('CREATE POLICY tenant_isolation ON pw.%I USING (tenant_id = (SELECT pw.current_tenant()))'
                   ' WITH CHECK (tenant_id = (SELECT pw.current_tenant()))', t);
  END LOOP;
END $$;

-- The history a reconnecting client can catch up from. Older gaps mean "refetch everything".
CREATE FUNCTION pw.change_history_kept() RETURNS integer
LANGUAGE sql IMMUTABLE SET search_path = pw, pg_temp AS $$ SELECT 1000 $$;

-- Records one kind of change for one site in the current transaction. The first call in a
-- transaction takes the next sequence number; later calls add their kind to the same row.
CREATE FUNCTION pw.note_site_change(p_tenant uuid, p_site uuid, p_kind text) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pw, pg_temp
AS $$
DECLARE
  v_flag text := 'pw.change_' || replace(p_site::text, '-', '') || '_' || p_kind;
  v_seq bigint;
  v_caller text := current_setting('app.tenant_id', true);
BEGIN
  -- Already noted in this transaction: nothing to do (a batch fires the trigger per record).
  IF current_setting(v_flag, true) = pg_current_xact_id()::text THEN
    RETURN;
  END IF;
  PERFORM set_config(v_flag, pg_current_xact_id()::text, true);
  -- Recorded under the changed row's tenant. A writer's tenant context is already that tenant
  -- (row-level security on the changed table); maintenance by the owner has none, and must not
  -- fail. The caller's context is restored below.
  PERFORM set_config('app.tenant_id', p_tenant::text, true);
  UPDATE pw.site_change SET kinds = array(SELECT DISTINCT unnest(kinds || p_kind) ORDER BY 1)
   WHERE site_id = p_site AND xact = pg_current_xact_id()
  RETURNING seq INTO v_seq;
  IF v_seq IS NULL THEN
    INSERT INTO pw.site_change_seq (site_id, tenant_id, seq) VALUES (p_site, p_tenant, 1)
    ON CONFLICT (site_id) DO UPDATE SET seq = pw.site_change_seq.seq + 1
    RETURNING seq INTO v_seq;
    INSERT INTO pw.site_change (tenant_id, site_id, seq, kinds, xact)
    VALUES (p_tenant, p_site, v_seq, ARRAY[p_kind], pg_current_xact_id());
    DELETE FROM pw.site_change WHERE site_id = p_site AND seq <= v_seq - pw.change_history_kept();
  END IF;
  PERFORM set_config('app.tenant_id', coalesce(v_caller, ''), true);
  PERFORM pg_notify('pw_site_changes', p_site::text);
END $$;

-- ------------------------------------------------------------ triggers

-- Row-level, with the cheapest possible repeat: after the first row of a site and kind in a
-- transaction, a row costs one setting lookup. (Statement-level triggers with transition tables
-- were measured first: they added about 65 percent to a bulk load, because every statement
-- captured its rows and ran a query over them.)
CREATE FUNCTION pw.site_change_trigger() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pw, pg_temp
AS $$
DECLARE
  v_tenant uuid;
  v_site uuid;
BEGIN
  IF TG_OP = 'DELETE' THEN
    v_tenant := OLD.tenant_id;
    v_site := OLD.site_id;
  ELSE
    v_tenant := NEW.tenant_id;
    v_site := NEW.site_id;
  END IF;
  IF current_setting('pw.change_' || replace(v_site::text, '-', '') || '_' || TG_ARGV[0], true)
     = pg_current_xact_id()::text THEN
    RETURN NULL;
  END IF;
  PERFORM pw.note_site_change(v_tenant, v_site, TG_ARGV[0]);
  RETURN NULL;
END $$;

-- pw.site has its id as the site; its trigger fires only when the data status changes.
CREATE FUNCTION pw.site_status_change_trigger() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pw, pg_temp
AS $$
BEGIN
  PERFORM pw.note_site_change(NEW.tenant_id, NEW.id, 'data_status');
  RETURN NULL;
END $$;

CREATE TRIGGER inventory_changed AFTER INSERT OR UPDATE OR DELETE ON pw.inventory_balance
  FOR EACH ROW EXECUTE FUNCTION pw.site_change_trigger('inventory');

-- Exceptions: a record that opens, is rescored, acknowledged or resolved. Only touching
-- last_evaluated_at is not a visible change.
CREATE TRIGGER exceptions_opened AFTER INSERT ON pw.exception_record
  FOR EACH ROW EXECUTE FUNCTION pw.site_change_trigger('exceptions');
CREATE TRIGGER exceptions_changed AFTER UPDATE ON pw.exception_record
  FOR EACH ROW WHEN ((OLD.status, OLD.score, OLD.score_components, OLD.critical)
                     IS DISTINCT FROM (NEW.status, NEW.score, NEW.score_components, NEW.critical))
  EXECUTE FUNCTION pw.site_change_trigger('exceptions');

CREATE TRIGGER clock_changed AFTER UPDATE ON pw.site_clock
  FOR EACH ROW WHEN (OLD.sim_time IS DISTINCT FROM NEW.sim_time)
  EXECUTE FUNCTION pw.site_change_trigger('clock');

CREATE TRIGGER data_status_changed AFTER UPDATE ON pw.site
  FOR EACH ROW WHEN (OLD.data_status IS DISTINCT FROM NEW.data_status)
  EXECUTE FUNCTION pw.site_status_change_trigger();

-- ------------------------------------------------------------ reading (the API, as pw_app)

-- The site's current sequence and the kinds changed after p_after. `complete` is false when
-- the history no longer reaches back that far: the client refetches everything.
CREATE FUNCTION pw.site_changes_since(p_site uuid, p_after bigint)
RETURNS TABLE (seq bigint, kinds text[], complete boolean)
LANGUAGE sql STABLE SECURITY INVOKER SET search_path = pw, pg_temp
AS $$
  SELECT coalesce(s.seq, 0),
         coalesce((SELECT array_agg(DISTINCT k ORDER BY k)
                     FROM pw.site_change c, unnest(c.kinds) k
                    WHERE c.site_id = p_site AND c.seq > p_after), ARRAY[]::text[]),
         -- A client ahead of the server (the database was rebuilt) refetches everything too.
         p_after = coalesce(s.seq, 0)
           OR (p_after < coalesce(s.seq, 0)
               AND EXISTS (SELECT 1 FROM pw.site_change c WHERE c.site_id = p_site AND c.seq = p_after + 1))
    FROM (SELECT 1) one
    LEFT JOIN pw.site_change_seq s ON s.site_id = p_site
$$;

REVOKE ALL ON pw.site_change_seq, pw.site_change FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION pw.note_site_change(uuid, uuid, text), pw.site_change_trigger(),
  pw.site_status_change_trigger(),
  pw.site_changes_since(uuid, bigint), pw.change_history_kept()
  FROM PUBLIC;
GRANT SELECT ON pw.site_change_seq, pw.site_change TO pw_app;
GRANT EXECUTE ON FUNCTION pw.site_changes_since(uuid, bigint), pw.change_history_kept() TO pw_app;
