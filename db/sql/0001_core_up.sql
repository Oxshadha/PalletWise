-- PalletWise canonical model, Slice 0 core.
-- Spec: docs/specs/canonical-warehouse-model.md (revision 3).
-- Runs as pw_owner. Later slices add settings, exceptions, proposals and tasks.

CREATE SCHEMA pw AUTHORIZATION pw_owner;
CREATE SCHEMA sem AUTHORIZATION pw_owner;
REVOKE ALL ON SCHEMA pw, sem FROM PUBLIC;

-- Functions are never executable by PUBLIC (CWM 11.4). Schema-level default privileges can
-- only add privileges, so the global PUBLIC default is revoked at role level; 0002 also
-- revokes explicitly on every function, and a catalog test verifies it.
ALTER DEFAULT PRIVILEGES FOR ROLE pw_owner REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

-- ------------------------------------------------------------ helpers

-- Tenant context (CWM 11.1). Fails closed with an explicit error when the context is missing,
-- and also when it is empty: after a transaction-local set_config, PostgreSQL keeps the
-- setting defined as '' for the rest of the session.
CREATE FUNCTION pw.current_tenant() RETURNS uuid
LANGUAGE plpgsql STABLE SET search_path = pw, pg_temp
AS $$
DECLARE v text := current_setting('app.tenant_id', true);
BEGIN
  IF v IS NULL OR v = '' THEN
    RAISE EXCEPTION 'no tenant context' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN v::uuid;
END $$;

-- UUIDv7 for transactional rows (CWM 2). Uses clock time only to order IDs.
CREATE FUNCTION pw.uuidv7() RETURNS uuid
LANGUAGE sql VOLATILE SET search_path = pw, pg_temp
AS $$
  SELECT encode(
    set_bit(set_bit(
      overlay(uuid_send(gen_random_uuid())
              PLACING substring(int8send(floor(extract(epoch FROM clock_timestamp()) * 1000)::bigint) FROM 3)
              FROM 1 FOR 6),
      52, 1), 53, 1),
    'hex')::uuid
$$;

-- Deterministic UUIDv5 for connector-sourced master data (CWM 2, revision 3).
-- p_site is NULL for tenant-owned entities.
CREATE FUNCTION pw.master_id(p_tenant uuid, p_site uuid, p_source text, p_entity text, p_external text)
RETURNS uuid
LANGUAGE sql IMMUTABLE SET search_path = pw, pg_temp
AS $$
  SELECT public.uuid_generate_v5(
    p_tenant,
    coalesce(p_site::text, '') || '|' || p_source || '|' || p_entity || '|' || p_external)
$$;

-- ------------------------------------------------------------ global reference (no tenant, no RLS)

CREATE TABLE pw.calendar_region (
  region_code     text PRIMARY KEY,
  name            text NOT NULL,
  coverage_start  date NOT NULL,
  coverage_end    date NOT NULL,
  CHECK (coverage_start <= coverage_end)
);

CREATE TABLE pw.business_calendar (
  region_code    text NOT NULL REFERENCES pw.calendar_region (region_code),
  date           date NOT NULL,
  dow            smallint NOT NULL CHECK (dow BETWEEN 0 AND 6),
  is_weekend     boolean NOT NULL,
  iso_year       smallint NOT NULL,
  iso_week       smallint NOT NULL,
  is_payday      boolean NOT NULL,
  festival       text,
  festival_ramp  numeric(3,2) NOT NULL CHECK (festival_ramp BETWEEN 0 AND 1),
  is_holiday     boolean NOT NULL,
  is_monsoon     boolean NOT NULL,
  is_operating   boolean NOT NULL,
  PRIMARY KEY (region_code, date)
);

-- ------------------------------------------------------------ tenant-owned

CREATE TABLE pw.tenant (
  id         uuid PRIMARY KEY,
  code       text NOT NULL UNIQUE,
  name       text NOT NULL,
  currency   char(3) NOT NULL,
  is_active  boolean NOT NULL DEFAULT true
);

CREATE TABLE pw.site (
  id                  uuid PRIMARY KEY,
  tenant_id           uuid NOT NULL REFERENCES pw.tenant (id),
  code                text NOT NULL,
  name                text NOT NULL,
  region_code         text NOT NULL REFERENCES pw.calendar_region (region_code),
  time_zone           text NOT NULL,
  width_m             numeric(8,2) NOT NULL CHECK (width_m > 0),
  depth_m             numeric(8,2) NOT NULL CHECK (depth_m > 0),
  data_status         text NOT NULL DEFAULT 'LOADING'
                      CHECK (data_status IN ('LOADING', 'READY', 'REBUILDING', 'FAILED')),
  data_status_detail  text,
  is_active           boolean NOT NULL DEFAULT true,
  UNIQUE (tenant_id, id),
  UNIQUE (tenant_id, code)
);

CREATE TABLE pw.category (
  id               uuid PRIMARY KEY,
  tenant_id        uuid NOT NULL REFERENCES pw.tenant (id),
  code             text NOT NULL,
  name             text NOT NULL,
  source_system    text NOT NULL,
  external_id      text NOT NULL,
  dataset_version  text,
  is_synthetic     boolean NOT NULL DEFAULT false,
  UNIQUE (tenant_id, id),
  UNIQUE (tenant_id, code)
);

CREATE TABLE pw.product (
  id                 uuid PRIMARY KEY,
  tenant_id          uuid NOT NULL,
  sku                text NOT NULL,
  name               text NOT NULL,
  category_id        uuid NOT NULL,
  brand              text NOT NULL,
  uom                text NOT NULL CHECK (uom IN ('EA', 'CASE')),
  unit_volume_m3     numeric(10,6) NOT NULL CHECK (unit_volume_m3 > 0),
  unit_weight_kg     numeric(10,3) NOT NULL CHECK (unit_weight_kg > 0),
  unit_value         numeric(14,2) NOT NULL CHECK (unit_value >= 0),
  temperature_class  text NOT NULL CHECK (temperature_class IN ('AMBIENT', 'CHILLED', 'FROZEN')),
  is_active          boolean NOT NULL DEFAULT true,
  source_system      text NOT NULL,
  external_id        text NOT NULL,
  dataset_version    text,
  is_synthetic       boolean NOT NULL DEFAULT false,
  UNIQUE (tenant_id, id),
  UNIQUE (tenant_id, sku),
  FOREIGN KEY (tenant_id, category_id) REFERENCES pw.category (tenant_id, id)
);

CREATE TABLE pw.customer (
  id                  uuid PRIMARY KEY,
  tenant_id           uuid NOT NULL REFERENCES pw.tenant (id),
  code                text NOT NULL,
  brand               text NOT NULL,
  district            text NOT NULL,
  dock_type           text NOT NULL,
  parking_constraint  text,
  window_open         time NOT NULL,
  window_close        time NOT NULL,
  source_system       text NOT NULL,
  external_id         text NOT NULL,
  dataset_version     text,
  is_synthetic        boolean NOT NULL DEFAULT false,
  UNIQUE (tenant_id, id),
  UNIQUE (tenant_id, code)
);

-- ------------------------------------------------------------ site-scoped: clock, runs, connectors

CREATE TABLE pw.site_clock (
  site_id      uuid PRIMARY KEY,
  tenant_id    uuid NOT NULL,
  mode         text NOT NULL CHECK (mode IN ('SIMULATED', 'REAL')),
  sim_time     timestamptz,
  speed        int NOT NULL DEFAULT 1 CHECK (speed IN (1, 10, 60)),
  state        text NOT NULL DEFAULT 'PAUSED' CHECK (state IN ('RUNNING', 'PAUSED')),
  row_version  int NOT NULL DEFAULT 0,
  CHECK (mode = 'REAL' OR sim_time IS NOT NULL),
  FOREIGN KEY (tenant_id, site_id) REFERENCES pw.site (tenant_id, id)
);

CREATE TABLE pw.simulation_run (
  id                 uuid PRIMARY KEY,
  tenant_id          uuid NOT NULL,
  site_id            uuid NOT NULL,
  seed               bigint NOT NULL,
  dataset_version    text NOT NULL,
  history_start      timestamptz NOT NULL,
  clock_start        timestamptz NOT NULL,
  status             text NOT NULL CHECK (status IN ('BUILDING', 'ACTIVE', 'ENDED', 'FAILED')),
  started_at         timestamptz NOT NULL,
  ended_at           timestamptz,
  started_by         text NOT NULL,
  scenario_log       jsonb NOT NULL DEFAULT '[]'::jsonb,
  history_loaded_at  timestamptz,
  dataset_hash       text,
  status_detail      text,
  UNIQUE (tenant_id, site_id, id),
  FOREIGN KEY (tenant_id, site_id) REFERENCES pw.site (tenant_id, id)
);
CREATE UNIQUE INDEX simulation_run_one_current
  ON pw.simulation_run (site_id) WHERE status IN ('BUILDING', 'ACTIVE');

CREATE TABLE pw.connector (
  id              uuid PRIMARY KEY,
  tenant_id       uuid NOT NULL,
  site_id         uuid NOT NULL,
  connector_type  text NOT NULL CHECK (connector_type IN ('SIMULATOR')),
  status          text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'PAUSED', 'ERROR')),
  config_ref      text,
  UNIQUE (tenant_id, site_id, id),
  FOREIGN KEY (tenant_id, site_id) REFERENCES pw.site (tenant_id, id)
);

CREATE TABLE pw.sync_state (
  connector_id     uuid NOT NULL,
  stream           text NOT NULL CHECK (stream IN ('MASTER', 'MOVEMENTS', 'ORDERS', 'ALLOCATIONS')),
  tenant_id        uuid NOT NULL,
  site_id          uuid NOT NULL,
  cursor           text,
  last_success_at  timestamptz,
  data_as_of       timestamptz,
  PRIMARY KEY (connector_id, stream),
  FOREIGN KEY (tenant_id, site_id, connector_id) REFERENCES pw.connector (tenant_id, site_id, id)
);

-- ------------------------------------------------------------ site-scoped: structure

CREATE TABLE pw.zone (
  id                 uuid PRIMARY KEY,
  tenant_id          uuid NOT NULL,
  site_id            uuid NOT NULL,
  code               text NOT NULL,
  name               text NOT NULL,
  zone_type          text NOT NULL
                     CHECK (zone_type IN ('FAST_PICK', 'RESERVE', 'BULK', 'COLD', 'RETURNS', 'STAGING')),
  temperature_class  text NOT NULL CHECK (temperature_class IN ('AMBIENT', 'CHILLED', 'FROZEN')),
  outline            jsonb NOT NULL,
  is_prime           boolean NOT NULL DEFAULT false,
  is_active          boolean NOT NULL DEFAULT true,
  source_system      text NOT NULL,
  external_id        text NOT NULL,
  dataset_version    text,
  is_synthetic       boolean NOT NULL DEFAULT false,
  UNIQUE (tenant_id, site_id, id),
  UNIQUE (site_id, code),
  FOREIGN KEY (tenant_id, site_id) REFERENCES pw.site (tenant_id, id)
);

CREATE TABLE pw.rack (
  id               uuid PRIMARY KEY,
  tenant_id        uuid NOT NULL,
  site_id          uuid NOT NULL,
  zone_id          uuid NOT NULL,
  code             text NOT NULL,
  origin_x         numeric(8,2) NOT NULL,
  origin_y         numeric(8,2) NOT NULL,
  rotation_deg     numeric(5,1) NOT NULL CHECK (rotation_deg IN (0, 90, 180, 270)),
  bay_count        int NOT NULL CHECK (bay_count > 0),
  level_count      int NOT NULL CHECK (level_count > 0),
  bay_width_m      numeric(6,2) NOT NULL CHECK (bay_width_m > 0),
  depth_m          numeric(6,2) NOT NULL CHECK (depth_m > 0),
  level_height_m   numeric(6,2) NOT NULL CHECK (level_height_m > 0),
  is_active        boolean NOT NULL DEFAULT true,
  source_system    text NOT NULL,
  external_id      text NOT NULL,
  dataset_version  text,
  is_synthetic     boolean NOT NULL DEFAULT false,
  UNIQUE (tenant_id, site_id, zone_id, id),
  UNIQUE (site_id, code),
  FOREIGN KEY (tenant_id, site_id, zone_id) REFERENCES pw.zone (tenant_id, site_id, id)
);

CREATE TABLE pw.bin (
  id                  uuid PRIMARY KEY,
  tenant_id           uuid NOT NULL,
  site_id             uuid NOT NULL,
  zone_id             uuid NOT NULL,
  rack_id             uuid,
  code                text NOT NULL,
  bay                 int,
  level               int,
  bin_role            text NOT NULL
                      CHECK (bin_role IN ('PICK', 'RESERVE', 'BULK', 'STAGING', 'RETURNS', 'RECEIVING')),
  x                   numeric(8,2) NOT NULL,
  y                   numeric(8,2) NOT NULL,
  z                   numeric(8,2) NOT NULL,
  width_m             numeric(6,2) NOT NULL CHECK (width_m > 0),
  depth_m             numeric(6,2) NOT NULL CHECK (depth_m > 0),
  height_m            numeric(6,2) NOT NULL CHECK (height_m > 0),
  capacity_volume_m3  numeric(10,3) NOT NULL CHECK (capacity_volume_m3 > 0),
  capacity_weight_kg  numeric(10,1) NOT NULL CHECK (capacity_weight_kg > 0),
  single_sku          boolean NOT NULL DEFAULT false,
  is_active           boolean NOT NULL DEFAULT true,
  source_system       text NOT NULL,
  external_id         text NOT NULL,
  dataset_version     text,
  is_synthetic        boolean NOT NULL DEFAULT false,
  CHECK ((rack_id IS NULL) = (bay IS NULL AND level IS NULL)),
  UNIQUE (tenant_id, site_id, id),
  UNIQUE (site_id, code),
  FOREIGN KEY (tenant_id, site_id, zone_id) REFERENCES pw.zone (tenant_id, site_id, id),
  FOREIGN KEY (tenant_id, site_id, zone_id, rack_id) REFERENCES pw.rack (tenant_id, site_id, zone_id, id)
);

-- Bay and level must fall inside the rack (CWM 5.5, revision 3).
CREATE FUNCTION pw.check_bin_bounds() RETURNS trigger
LANGUAGE plpgsql SET search_path = pw, pg_temp
AS $$
DECLARE r record;
BEGIN
  IF NEW.rack_id IS NULL THEN RETURN NEW; END IF;
  SELECT bay_count, level_count INTO r FROM pw.rack WHERE id = NEW.rack_id;
  IF NEW.bay NOT BETWEEN 1 AND r.bay_count OR NEW.level NOT BETWEEN 1 AND r.level_count THEN
    RAISE EXCEPTION 'bin % is outside its rack (bay %, level %)', NEW.code, NEW.bay, NEW.level
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER bin_bounds BEFORE INSERT OR UPDATE ON pw.bin
  FOR EACH ROW EXECUTE FUNCTION pw.check_bin_bounds();

CREATE TABLE pw.site_feature (
  id               uuid PRIMARY KEY,
  tenant_id        uuid NOT NULL,
  site_id          uuid NOT NULL,
  feature_type     text NOT NULL
                   CHECK (feature_type IN ('RECEIVING_DOCK', 'SHIPPING_DOCK', 'AISLE', 'OFFICE', 'WALL')),
  label            text NOT NULL,
  outline          jsonb NOT NULL,
  source_system    text NOT NULL,
  external_id      text NOT NULL,
  dataset_version  text,
  is_synthetic     boolean NOT NULL DEFAULT false,
  FOREIGN KEY (tenant_id, site_id) REFERENCES pw.site (tenant_id, id)
);

-- ------------------------------------------------------------ site-scoped: orders and allocation

CREATE TABLE pw.outbound_order (
  id                 uuid PRIMARY KEY,
  tenant_id          uuid NOT NULL,
  site_id            uuid NOT NULL,
  order_no           text NOT NULL,
  customer_id        uuid NOT NULL,
  delivery_date      date NOT NULL,
  dispatch_deadline  timestamptz NOT NULL,
  status             text NOT NULL DEFAULT 'OPEN'
                     CHECK (status IN ('OPEN', 'ALLOCATED', 'PICKING', 'PICKED', 'SHIPPED', 'CANCELLED')),
  shipped_at         timestamptz,
  source_system      text NOT NULL,
  external_id        text NOT NULL,
  dataset_version    text,
  is_synthetic       boolean NOT NULL DEFAULT false,
  UNIQUE (tenant_id, site_id, id),
  UNIQUE (site_id, order_no),
  UNIQUE (tenant_id, site_id, source_system, external_id),
  FOREIGN KEY (tenant_id, site_id) REFERENCES pw.site (tenant_id, id),
  FOREIGN KEY (tenant_id, customer_id) REFERENCES pw.customer (tenant_id, id)
);
CREATE INDEX outbound_order_due ON pw.outbound_order (site_id, status, dispatch_deadline);

CREATE TABLE pw.order_line (
  id               uuid PRIMARY KEY,
  tenant_id        uuid NOT NULL,
  site_id          uuid NOT NULL,
  order_id         uuid NOT NULL,
  line_no          int NOT NULL,
  product_id       uuid NOT NULL,
  qty_ordered      int NOT NULL CHECK (qty_ordered > 0),
  qty_allocated    int NOT NULL DEFAULT 0,
  qty_picked       int NOT NULL DEFAULT 0,
  qty_shipped      int NOT NULL DEFAULT 0,
  source_system    text NOT NULL,
  external_id      text NOT NULL,
  dataset_version  text,
  is_synthetic     boolean NOT NULL DEFAULT false,
  CHECK (0 <= qty_shipped AND qty_shipped <= qty_picked
         AND qty_picked <= qty_allocated AND qty_allocated <= qty_ordered),
  UNIQUE (tenant_id, site_id, id),
  UNIQUE (tenant_id, site_id, id, product_id),
  UNIQUE (order_id, line_no),
  UNIQUE (tenant_id, site_id, source_system, external_id),
  FOREIGN KEY (tenant_id, site_id, order_id) REFERENCES pw.outbound_order (tenant_id, site_id, id),
  FOREIGN KEY (tenant_id, product_id) REFERENCES pw.product (tenant_id, id)
);

CREATE TABLE pw.allocation (
  id               uuid PRIMARY KEY,
  tenant_id        uuid NOT NULL,
  site_id          uuid NOT NULL,
  order_line_id    uuid NOT NULL,
  product_id       uuid NOT NULL,
  bin_id           uuid NOT NULL,
  qty              int NOT NULL CHECK (qty > 0),
  qty_consumed     int NOT NULL DEFAULT 0 CHECK (qty_consumed >= 0),
  qty_released     int NOT NULL DEFAULT 0 CHECK (qty_released >= 0),
  status           text GENERATED ALWAYS AS (
                     CASE WHEN qty - qty_consumed - qty_released > 0 THEN 'ACTIVE'
                          WHEN qty_consumed > 0 THEN 'CONSUMED'
                          ELSE 'RELEASED' END) STORED,
  created_at       timestamptz NOT NULL,
  closed_at        timestamptz,
  row_version      int NOT NULL DEFAULT 0,
  source_system    text NOT NULL,
  external_id      text NOT NULL,
  dataset_version  text,
  is_synthetic     boolean NOT NULL DEFAULT false,
  CHECK (qty_consumed + qty_released <= qty),
  UNIQUE (tenant_id, site_id, source_system, external_id),
  FOREIGN KEY (tenant_id, site_id, order_line_id, product_id)
    REFERENCES pw.order_line (tenant_id, site_id, id, product_id),
  FOREIGN KEY (tenant_id, site_id, bin_id) REFERENCES pw.bin (tenant_id, site_id, id)
);
CREATE INDEX allocation_line_bin ON pw.allocation (order_line_id, bin_id);

-- ------------------------------------------------------------ site-scoped: inventory

CREATE TABLE pw.movement (
  id               uuid PRIMARY KEY,
  tenant_id        uuid NOT NULL,
  site_id          uuid NOT NULL,
  movement_type    text NOT NULL
                   CHECK (movement_type IN ('RECEIPT', 'PUTAWAY', 'REPLENISH', 'TRANSFER', 'PICK', 'SHIP', 'ADJUSTMENT')),
  product_id       uuid NOT NULL,
  qty              int NOT NULL CHECK (qty > 0),
  from_bin_id      uuid,
  to_bin_id        uuid,
  occurred_at      timestamptz NOT NULL,
  recorded_at      timestamptz NOT NULL DEFAULT clock_timestamp(),
  order_line_id    uuid,
  reason_code      text,
  source_system    text NOT NULL,
  external_id      text NOT NULL,
  dataset_version  text,
  is_synthetic     boolean NOT NULL DEFAULT false,
  CHECK (from_bin_id IS DISTINCT FROM to_bin_id),
  CHECK (CASE movement_type
           WHEN 'RECEIPT'    THEN from_bin_id IS NULL AND to_bin_id IS NOT NULL
           WHEN 'SHIP'       THEN from_bin_id IS NOT NULL AND to_bin_id IS NULL AND order_line_id IS NOT NULL
           WHEN 'PICK'       THEN from_bin_id IS NOT NULL AND to_bin_id IS NOT NULL AND order_line_id IS NOT NULL
           WHEN 'ADJUSTMENT' THEN (from_bin_id IS NULL) <> (to_bin_id IS NULL) AND reason_code IS NOT NULL
           ELSE from_bin_id IS NOT NULL AND to_bin_id IS NOT NULL
         END),
  UNIQUE (tenant_id, site_id, source_system, external_id),
  FOREIGN KEY (tenant_id, site_id) REFERENCES pw.site (tenant_id, id),
  FOREIGN KEY (tenant_id, product_id) REFERENCES pw.product (tenant_id, id),
  FOREIGN KEY (tenant_id, site_id, from_bin_id) REFERENCES pw.bin (tenant_id, site_id, id),
  FOREIGN KEY (tenant_id, site_id, to_bin_id) REFERENCES pw.bin (tenant_id, site_id, id),
  FOREIGN KEY (tenant_id, site_id, order_line_id) REFERENCES pw.order_line (tenant_id, site_id, id)
);
CREATE INDEX movement_product_time ON pw.movement (site_id, product_id, occurred_at);
CREATE INDEX movement_from_time    ON pw.movement (site_id, from_bin_id, occurred_at);
CREATE INDEX movement_to_time      ON pw.movement (site_id, to_bin_id, occurred_at);
CREATE INDEX movement_type_time    ON pw.movement (site_id, movement_type, occurred_at);
CREATE INDEX movement_order_line   ON pw.movement (site_id, order_line_id);

CREATE TABLE pw.inventory_balance (
  tenant_id         uuid NOT NULL,
  site_id           uuid NOT NULL,
  bin_id            uuid NOT NULL,
  product_id        uuid NOT NULL,
  on_hand           int NOT NULL CHECK (on_hand >= 0),
  allocated         int NOT NULL DEFAULT 0 CHECK (allocated >= 0),
  available         int GENERATED ALWAYS AS (on_hand - allocated) STORED,
  last_movement_at  timestamptz NOT NULL,
  row_version       int NOT NULL DEFAULT 0,
  CHECK (allocated <= on_hand),
  PRIMARY KEY (tenant_id, site_id, bin_id, product_id),
  FOREIGN KEY (tenant_id, site_id, bin_id) REFERENCES pw.bin (tenant_id, site_id, id),
  FOREIGN KEY (tenant_id, product_id) REFERENCES pw.product (tenant_id, id)
);
CREATE INDEX inventory_balance_product ON pw.inventory_balance (site_id, product_id);

CREATE TABLE pw.replenishment_policy (
  id               uuid PRIMARY KEY,
  tenant_id        uuid NOT NULL,
  site_id          uuid NOT NULL,
  product_id       uuid NOT NULL,
  pick_bin_id      uuid NOT NULL,
  min_qty          int NOT NULL,
  max_qty          int NOT NULL,
  source_system    text NOT NULL,
  external_id      text NOT NULL,
  dataset_version  text,
  is_synthetic     boolean NOT NULL DEFAULT false,
  CHECK (0 <= min_qty AND min_qty < max_qty),
  UNIQUE (site_id, product_id, pick_bin_id),
  FOREIGN KEY (tenant_id, product_id) REFERENCES pw.product (tenant_id, id),
  FOREIGN KEY (tenant_id, site_id, pick_bin_id) REFERENCES pw.bin (tenant_id, site_id, id)
);

-- ------------------------------------------------------------ row-level security (CWM 11.1)

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'site', 'category', 'product', 'customer', 'site_clock', 'simulation_run', 'connector',
    'sync_state', 'zone', 'rack', 'bin', 'site_feature', 'outbound_order', 'order_line',
    'allocation', 'movement', 'inventory_balance', 'replenishment_policy']
  LOOP
    EXECUTE format('ALTER TABLE pw.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE pw.%I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format(
      'CREATE POLICY tenant_isolation ON pw.%I USING (tenant_id = (SELECT pw.current_tenant())) '
      'WITH CHECK (tenant_id = (SELECT pw.current_tenant()))', t);
  END LOOP;
END $$;

ALTER TABLE pw.tenant ENABLE ROW LEVEL SECURITY;
ALTER TABLE pw.tenant FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON pw.tenant
  USING (id = (SELECT pw.current_tenant())) WITH CHECK (id = (SELECT pw.current_tenant()));
