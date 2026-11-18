-- Invariant-keeping write paths (CWM 6.6, 8.2, 11.3, 11.4) and site lifecycle (CWM 5.2, 7.4).
-- Every SECURITY DEFINER function: fixed search_path, tenant check first, no dynamic SQL.

CREATE FUNCTION pw.assert_tenant(p_tenant uuid) RETURNS void
LANGUAGE plpgsql STABLE SET search_path = pw, pg_temp
AS $$
BEGIN
  IF p_tenant IS DISTINCT FROM pw.current_tenant() THEN
    RAISE EXCEPTION 'tenant context mismatch' USING ERRCODE = 'insufficient_privilege';
  END IF;
END $$;

-- ------------------------------------------------------------ balances and allocations

-- Adds (positive) or removes (negative) physical stock in a bin.
CREATE FUNCTION pw._change_on_hand(p_tenant uuid, p_site uuid, p_bin uuid, p_product uuid,
                                   p_delta int, p_release_alloc int, p_at timestamptz)
RETURNS void
LANGUAGE plpgsql SET search_path = pw, pg_temp
AS $$
DECLARE v_single boolean;
BEGIN
  IF p_delta > 0 THEN
    SELECT single_sku INTO v_single FROM pw.bin WHERE id = p_bin;
    IF v_single AND EXISTS (
      SELECT 1 FROM pw.inventory_balance
      WHERE tenant_id = p_tenant AND site_id = p_site AND bin_id = p_bin AND product_id <> p_product
    ) THEN
      RAISE EXCEPTION 'bin % holds another product and is single-SKU', p_bin USING ERRCODE = 'check_violation';
    END IF;
    INSERT INTO pw.inventory_balance AS b
      (tenant_id, site_id, bin_id, product_id, on_hand, allocated, last_movement_at)
    VALUES (p_tenant, p_site, p_bin, p_product, p_delta, -p_release_alloc, p_at)
    ON CONFLICT (tenant_id, site_id, bin_id, product_id) DO UPDATE
      SET on_hand = b.on_hand + EXCLUDED.on_hand,
          allocated = b.allocated - p_release_alloc,
          last_movement_at = greatest(b.last_movement_at, EXCLUDED.last_movement_at),
          row_version = b.row_version + 1;
  ELSE
    UPDATE pw.inventory_balance
       SET on_hand = on_hand + p_delta,
           allocated = allocated - p_release_alloc,
           last_movement_at = greatest(last_movement_at, p_at),
           row_version = row_version + 1
     WHERE tenant_id = p_tenant AND site_id = p_site AND bin_id = p_bin AND product_id = p_product;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'no stock of product % in bin %', p_product, p_bin USING ERRCODE = 'check_violation';
    END IF;
    -- Presence of a row means stock is present (CWM 8.2).
    DELETE FROM pw.inventory_balance
     WHERE tenant_id = p_tenant AND site_id = p_site AND bin_id = p_bin
       AND product_id = p_product AND on_hand = 0;
  END IF;
END $$;

CREATE FUNCTION pw._apply_movement(p_tenant uuid, p_site uuid, p_source text, p_at timestamptz,
                                   p_dataset text, p_synthetic boolean, r jsonb)
RETURNS void
LANGUAGE plpgsql SET search_path = pw, pg_temp
AS $$
DECLARE
  v_type    text := r->>'movement_type';
  v_qty     int  := (r->>'qty')::int;
  v_reason  text := r->>'reason_code';
  v_product uuid := pw.master_id(p_tenant, NULL, p_source, 'PRODUCT', r->>'sku');
  v_from    uuid;
  v_to      uuid;
  v_line    uuid;
  v_alloc   uuid;
  v_rows    int;
BEGIN
  IF r->>'from_bin' IS NOT NULL THEN
    v_from := pw.master_id(p_tenant, p_site, p_source, 'BIN', r->>'from_bin');
  END IF;
  IF r->>'to_bin' IS NOT NULL THEN
    v_to := pw.master_id(p_tenant, p_site, p_source, 'BIN', r->>'to_bin');
  END IF;
  IF r->>'order_line' IS NOT NULL THEN
    SELECT id INTO v_line FROM pw.order_line
     WHERE tenant_id = p_tenant AND site_id = p_site
       AND source_system = p_source AND external_id = r->>'order_line';
    IF NOT FOUND THEN
      RAISE EXCEPTION 'unknown order line %', r->>'order_line' USING ERRCODE = 'foreign_key_violation';
    END IF;
  END IF;

  INSERT INTO pw.movement (id, tenant_id, site_id, movement_type, product_id, qty, from_bin_id,
                           to_bin_id, occurred_at, order_line_id, reason_code, source_system,
                           external_id, dataset_version, is_synthetic)
  VALUES (pw.uuidv7(), p_tenant, p_site, v_type, v_product, v_qty, v_from, v_to, p_at, v_line,
          v_reason, p_source, r->>'external_id', p_dataset, p_synthetic)
  ON CONFLICT (tenant_id, site_id, source_system, external_id) DO NOTHING;
  GET DIAGNOSTICS v_rows = ROW_COUNT;
  IF v_rows = 0 THEN
    RETURN;  -- replay of an already applied movement: idempotent
  END IF;

  IF v_type = 'PICK' THEN
    -- A pick consumes the line's active allocation in the same bin (CWM 6.6).
    SELECT id INTO v_alloc FROM pw.allocation
     WHERE tenant_id = p_tenant AND site_id = p_site AND order_line_id = v_line AND bin_id = v_from
       AND qty - qty_consumed - qty_released >= v_qty
     ORDER BY created_at, id LIMIT 1;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'pick without a sufficient active allocation (line %, bin %)', v_line, v_from
        USING ERRCODE = 'check_violation';
    END IF;
    UPDATE pw.allocation SET qty_consumed = qty_consumed + v_qty, row_version = row_version + 1,
           closed_at = CASE WHEN qty - qty_consumed - v_qty - qty_released = 0 THEN p_at END
     WHERE id = v_alloc;
    PERFORM pw._change_on_hand(p_tenant, p_site, v_from, v_product, -v_qty, v_qty, p_at);
    PERFORM pw._change_on_hand(p_tenant, p_site, v_to, v_product, v_qty, 0, p_at);
    UPDATE pw.order_line SET qty_picked = qty_picked + v_qty WHERE id = v_line;

  ELSIF v_type = 'TRANSFER' AND v_reason = 'PICK_REVERSAL' THEN
    -- Must return to the bin of the allocation it reverses (CWM 6.6).
    SELECT id INTO v_alloc FROM pw.allocation
     WHERE tenant_id = p_tenant AND site_id = p_site AND order_line_id = v_line AND bin_id = v_to
       AND qty_consumed >= v_qty
     ORDER BY created_at DESC, id DESC LIMIT 1;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'pick reversal must return to the allocation bin and not exceed picked quantity'
        USING ERRCODE = 'check_violation';
    END IF;
    UPDATE pw.allocation SET qty_consumed = qty_consumed - v_qty, closed_at = NULL,
           row_version = row_version + 1
     WHERE id = v_alloc;
    PERFORM pw._change_on_hand(p_tenant, p_site, v_from, v_product, -v_qty, 0, p_at);
    PERFORM pw._change_on_hand(p_tenant, p_site, v_to, v_product, v_qty, -v_qty, p_at);
    UPDATE pw.order_line SET qty_picked = qty_picked - v_qty WHERE id = v_line;

  ELSE
    IF v_from IS NOT NULL THEN
      PERFORM pw._change_on_hand(p_tenant, p_site, v_from, v_product, -v_qty, 0, p_at);
    END IF;
    IF v_to IS NOT NULL THEN
      PERFORM pw._change_on_hand(p_tenant, p_site, v_to, v_product, v_qty, 0, p_at);
    END IF;
    IF v_type = 'SHIP' THEN
      UPDATE pw.order_line SET qty_shipped = qty_shipped + v_qty WHERE id = v_line;
    END IF;
  END IF;
END $$;

CREATE FUNCTION pw._apply_allocation(p_tenant uuid, p_site uuid, p_source text, p_at timestamptz,
                                     p_dataset text, p_synthetic boolean, r jsonb)
RETURNS void
LANGUAGE plpgsql SET search_path = pw, pg_temp
AS $$
DECLARE
  v_line    uuid;
  v_product uuid := pw.master_id(p_tenant, NULL, p_source, 'PRODUCT', r->>'sku');
  v_bin     uuid := pw.master_id(p_tenant, p_site, p_source, 'BIN', r->>'bin');
  v_qty     int  := (r->>'qty')::int;
  v_rows    int;
BEGIN
  SELECT id INTO v_line FROM pw.order_line
   WHERE tenant_id = p_tenant AND site_id = p_site
     AND source_system = p_source AND external_id = r->>'order_line';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'unknown order line %', r->>'order_line' USING ERRCODE = 'foreign_key_violation';
  END IF;

  INSERT INTO pw.allocation (id, tenant_id, site_id, order_line_id, product_id, bin_id, qty,
                             created_at, source_system, external_id, dataset_version, is_synthetic)
  VALUES (pw.uuidv7(), p_tenant, p_site, v_line, v_product, v_bin, v_qty, p_at, p_source,
          r->>'external_id', p_dataset, p_synthetic)
  ON CONFLICT (tenant_id, site_id, source_system, external_id) DO NOTHING;
  GET DIAGNOSTICS v_rows = ROW_COUNT;
  IF v_rows = 0 THEN
    RETURN;
  END IF;

  UPDATE pw.inventory_balance
     SET allocated = allocated + v_qty, row_version = row_version + 1
   WHERE tenant_id = p_tenant AND site_id = p_site AND bin_id = v_bin AND product_id = v_product;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'allocation in bin % without stock', v_bin USING ERRCODE = 'check_violation';
  END IF;
  UPDATE pw.order_line SET qty_allocated = qty_allocated + v_qty WHERE id = v_line;
END $$;

-- Release sets the absolute released quantity, so replays are idempotent.
-- Only the unpicked remainder can be released (CWM 6.6).
CREATE FUNCTION pw._apply_allocation_release(p_tenant uuid, p_site uuid, p_source text,
                                             p_at timestamptz, r jsonb)
RETURNS void
LANGUAGE plpgsql SET search_path = pw, pg_temp
AS $$
DECLARE
  a        record;
  v_target int := (r->>'qty_released')::int;
  v_delta  int;
BEGIN
  SELECT * INTO a FROM pw.allocation
   WHERE tenant_id = p_tenant AND site_id = p_site
     AND source_system = p_source AND external_id = r->>'external_id'
   FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'unknown allocation %', r->>'external_id' USING ERRCODE = 'foreign_key_violation';
  END IF;
  v_delta := v_target - a.qty_released;
  IF v_delta <= 0 THEN
    RETURN;
  END IF;
  IF v_delta > a.qty - a.qty_consumed - a.qty_released THEN
    RAISE EXCEPTION 'release exceeds the unpicked remainder' USING ERRCODE = 'check_violation';
  END IF;
  UPDATE pw.allocation SET qty_released = v_target, row_version = row_version + 1,
         closed_at = CASE WHEN qty - qty_consumed - v_target = 0 THEN p_at END
   WHERE id = a.id;
  UPDATE pw.inventory_balance SET allocated = allocated - v_delta, row_version = row_version + 1
   WHERE tenant_id = p_tenant AND site_id = p_site AND bin_id = a.bin_id AND product_id = a.product_id;
  UPDATE pw.order_line SET qty_allocated = qty_allocated - v_delta WHERE id = a.order_line_id;
END $$;

-- ------------------------------------------------------------ the ingestion entry point

-- Applies one source record. The only write path for connector data (CWM 11.3, pw_ingest).
CREATE FUNCTION pw.apply_record(p_tenant uuid, p_site uuid, p_connector uuid, p_run uuid,
                                p_type text, p_at timestamptz, p_dataset text,
                                p_synthetic boolean, r jsonb)
RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pw, pg_temp
AS $$
DECLARE
  v_source   text;
  v_category uuid;
BEGIN
  PERFORM pw.assert_tenant(p_tenant);
  SELECT connector_type INTO v_source FROM pw.connector
   WHERE id = p_connector AND site_id = p_site AND tenant_id = p_tenant;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'unknown connector' USING ERRCODE = 'insufficient_privilege';
  END IF;

  -- Records of a run that is not the site's current run are skipped (simulator spec 2.1).
  IF p_run IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM pw.simulation_run
     WHERE id = p_run AND site_id = p_site AND status IN ('BUILDING', 'ACTIVE')
  ) THEN
    RETURN 'SKIPPED_OLD_RUN';
  END IF;

  CASE p_type
  WHEN 'RUN_STARTED' THEN
    NULL;  -- the run row is created by the simulation admin service

  WHEN 'ZONE' THEN
    INSERT INTO pw.zone AS z (id, tenant_id, site_id, code, name, zone_type, temperature_class,
                              outline, is_prime, source_system, external_id, dataset_version, is_synthetic)
    VALUES (pw.master_id(p_tenant, p_site, v_source, 'ZONE', r->>'code'), p_tenant, p_site,
            r->>'code', r->>'name', r->>'zone_type', r->>'temperature_class', r->'outline',
            (r->>'is_prime')::boolean, v_source, r->>'code', p_dataset, p_synthetic)
    ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, zone_type = EXCLUDED.zone_type,
      temperature_class = EXCLUDED.temperature_class, outline = EXCLUDED.outline,
      is_prime = EXCLUDED.is_prime, dataset_version = EXCLUDED.dataset_version;

  WHEN 'RACK' THEN
    INSERT INTO pw.rack AS k (id, tenant_id, site_id, zone_id, code, origin_x, origin_y, rotation_deg,
                              bay_count, level_count, bay_width_m, depth_m, level_height_m,
                              source_system, external_id, dataset_version, is_synthetic)
    VALUES (pw.master_id(p_tenant, p_site, v_source, 'RACK', r->>'code'), p_tenant, p_site,
            pw.master_id(p_tenant, p_site, v_source, 'ZONE', r->>'zone'), r->>'code',
            (r->>'origin_x')::numeric, (r->>'origin_y')::numeric, (r->>'rotation_deg')::numeric,
            (r->>'bay_count')::int, (r->>'level_count')::int, (r->>'bay_width_m')::numeric,
            (r->>'depth_m')::numeric, (r->>'level_height_m')::numeric,
            v_source, r->>'code', p_dataset, p_synthetic)
    ON CONFLICT (id) DO UPDATE SET origin_x = EXCLUDED.origin_x, origin_y = EXCLUDED.origin_y,
      rotation_deg = EXCLUDED.rotation_deg, bay_count = EXCLUDED.bay_count,
      level_count = EXCLUDED.level_count, dataset_version = EXCLUDED.dataset_version;

  WHEN 'BIN' THEN
    INSERT INTO pw.bin AS b (id, tenant_id, site_id, zone_id, rack_id, code, bay, level, bin_role,
                             x, y, z, width_m, depth_m, height_m, capacity_volume_m3,
                             capacity_weight_kg, single_sku, source_system, external_id,
                             dataset_version, is_synthetic)
    VALUES (pw.master_id(p_tenant, p_site, v_source, 'BIN', r->>'code'), p_tenant, p_site,
            pw.master_id(p_tenant, p_site, v_source, 'ZONE', r->>'zone'),
            CASE WHEN r->>'rack' IS NULL THEN NULL
                 ELSE pw.master_id(p_tenant, p_site, v_source, 'RACK', r->>'rack') END,
            r->>'code', (r->>'bay')::int, (r->>'level')::int, r->>'bin_role',
            (r->>'x')::numeric, (r->>'y')::numeric, (r->>'z')::numeric,
            (r->>'width_m')::numeric, (r->>'depth_m')::numeric, (r->>'height_m')::numeric,
            (r->>'capacity_volume_m3')::numeric, (r->>'capacity_weight_kg')::numeric,
            (r->>'single_sku')::boolean, v_source, r->>'code', p_dataset, p_synthetic)
    ON CONFLICT (id) DO UPDATE SET bin_role = EXCLUDED.bin_role, x = EXCLUDED.x, y = EXCLUDED.y,
      z = EXCLUDED.z, capacity_volume_m3 = EXCLUDED.capacity_volume_m3,
      capacity_weight_kg = EXCLUDED.capacity_weight_kg, single_sku = EXCLUDED.single_sku,
      dataset_version = EXCLUDED.dataset_version;

  WHEN 'SITE_FEATURE' THEN
    INSERT INTO pw.site_feature (id, tenant_id, site_id, feature_type, label, outline,
                                 source_system, external_id, dataset_version, is_synthetic)
    VALUES (pw.master_id(p_tenant, p_site, v_source, 'SITE_FEATURE', r->>'code'), p_tenant, p_site,
            r->>'feature_type', r->>'label', r->'outline', v_source, r->>'code', p_dataset, p_synthetic)
    ON CONFLICT (id) DO UPDATE SET label = EXCLUDED.label, outline = EXCLUDED.outline;

  WHEN 'CATEGORY' THEN
    INSERT INTO pw.category (id, tenant_id, code, name, source_system, external_id,
                             dataset_version, is_synthetic)
    VALUES (pw.master_id(p_tenant, NULL, v_source, 'CATEGORY', r->>'code'), p_tenant,
            r->>'code', r->>'name', v_source, r->>'code', p_dataset, p_synthetic)
    ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name;

  WHEN 'PRODUCT' THEN
    v_category := pw.master_id(p_tenant, NULL, v_source, 'CATEGORY', r->>'category');
    INSERT INTO pw.product (id, tenant_id, sku, name, category_id, brand, uom, unit_volume_m3,
                            unit_weight_kg, unit_value, temperature_class, source_system,
                            external_id, dataset_version, is_synthetic)
    VALUES (pw.master_id(p_tenant, NULL, v_source, 'PRODUCT', r->>'sku'), p_tenant, r->>'sku',
            r->>'name', v_category, r->>'brand', r->>'uom', (r->>'unit_volume_m3')::numeric,
            (r->>'unit_weight_kg')::numeric, (r->>'unit_value')::numeric,
            r->>'temperature_class', v_source, r->>'sku', p_dataset, p_synthetic)
    ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, category_id = EXCLUDED.category_id,
      unit_value = EXCLUDED.unit_value, dataset_version = EXCLUDED.dataset_version;

  WHEN 'CUSTOMER' THEN
    INSERT INTO pw.customer (id, tenant_id, code, brand, district, dock_type, parking_constraint,
                             window_open, window_close, source_system, external_id,
                             dataset_version, is_synthetic)
    VALUES (pw.master_id(p_tenant, NULL, v_source, 'CUSTOMER', r->>'code'), p_tenant, r->>'code',
            r->>'brand', r->>'district', r->>'dock_type', r->>'parking_constraint',
            (r->>'window_open')::time, (r->>'window_close')::time, v_source, r->>'code',
            p_dataset, p_synthetic)
    ON CONFLICT (id) DO UPDATE SET window_open = EXCLUDED.window_open,
      window_close = EXCLUDED.window_close;

  WHEN 'REPLENISHMENT_POLICY' THEN
    INSERT INTO pw.replenishment_policy (id, tenant_id, site_id, product_id, pick_bin_id, min_qty,
                                         max_qty, source_system, external_id, dataset_version,
                                         is_synthetic)
    VALUES (pw.master_id(p_tenant, p_site, v_source, 'REPLENISHMENT_POLICY', r->>'sku'), p_tenant,
            p_site, pw.master_id(p_tenant, NULL, v_source, 'PRODUCT', r->>'sku'),
            pw.master_id(p_tenant, p_site, v_source, 'BIN', r->>'pick_bin'),
            (r->>'min_qty')::int, (r->>'max_qty')::int, v_source, r->>'sku', p_dataset, p_synthetic)
    ON CONFLICT (id) DO UPDATE SET min_qty = EXCLUDED.min_qty, max_qty = EXCLUDED.max_qty;

  WHEN 'ORDER' THEN
    INSERT INTO pw.outbound_order (id, tenant_id, site_id, order_no, customer_id, delivery_date,
                                   dispatch_deadline, source_system, external_id, dataset_version,
                                   is_synthetic)
    VALUES (pw.uuidv7(), p_tenant, p_site, r->>'order_no',
            pw.master_id(p_tenant, NULL, v_source, 'CUSTOMER', r->>'customer'),
            (r->>'delivery_date')::date, (r->>'dispatch_deadline')::timestamptz, v_source,
            r->>'order_no', p_dataset, p_synthetic)
    ON CONFLICT (tenant_id, site_id, source_system, external_id) DO NOTHING;

  WHEN 'ORDER_STATUS' THEN
    UPDATE pw.outbound_order SET status = r->>'status',
           shipped_at = coalesce((r->>'shipped_at')::timestamptz, shipped_at)
     WHERE tenant_id = p_tenant AND site_id = p_site
       AND source_system = v_source AND external_id = r->>'order_no';
    IF NOT FOUND THEN
      RAISE EXCEPTION 'unknown order %', r->>'order_no' USING ERRCODE = 'foreign_key_violation';
    END IF;

  WHEN 'ORDER_LINE' THEN
    INSERT INTO pw.order_line (id, tenant_id, site_id, order_id, line_no, product_id, qty_ordered,
                               source_system, external_id, dataset_version, is_synthetic)
    SELECT pw.uuidv7(), p_tenant, p_site, o.id, (r->>'line_no')::int,
           pw.master_id(p_tenant, NULL, v_source, 'PRODUCT', r->>'sku'),
           (r->>'qty_ordered')::int, v_source, r->>'external_id', p_dataset, p_synthetic
      FROM pw.outbound_order o
     WHERE o.tenant_id = p_tenant AND o.site_id = p_site
       AND o.source_system = v_source AND o.external_id = r->>'order_no'
    ON CONFLICT (tenant_id, site_id, source_system, external_id) DO NOTHING;

  WHEN 'ALLOCATION' THEN
    PERFORM pw._apply_allocation(p_tenant, p_site, v_source, p_at, p_dataset, p_synthetic, r);

  WHEN 'ALLOCATION_RELEASE' THEN
    PERFORM pw._apply_allocation_release(p_tenant, p_site, v_source, p_at, r);

  WHEN 'MOVEMENT' THEN
    PERFORM pw._apply_movement(p_tenant, p_site, v_source, p_at, p_dataset, p_synthetic, r);

  WHEN 'TICK' THEN
    -- Everything up to p_at is applied: only now may the clock move (simulator spec 2.1).
    UPDATE pw.site_clock SET sim_time = p_at, row_version = row_version + 1
     WHERE site_id = p_site AND tenant_id = p_tenant AND sim_time < p_at;
    UPDATE pw.sync_state SET data_as_of = p_at
     WHERE connector_id = p_connector AND (data_as_of IS NULL OR data_as_of < p_at);

  WHEN 'LOAD_COMPLETE' THEN
    UPDATE pw.simulation_run SET history_loaded_at = clock_timestamp()
     WHERE id = p_run AND site_id = p_site;

  ELSE
    RAISE EXCEPTION 'unknown record type %', p_type USING ERRCODE = 'invalid_parameter_value';
  END CASE;

  RETURN 'APPLIED';
END $$;

-- Advances the connector cursor at the end of a batch, in the batch transaction.
CREATE FUNCTION pw.set_cursor(p_tenant uuid, p_connector uuid, p_cursor text)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pw, pg_temp
AS $$
BEGIN
  PERFORM pw.assert_tenant(p_tenant);
  UPDATE pw.sync_state SET cursor = p_cursor, last_success_at = clock_timestamp()
   WHERE connector_id = p_connector AND tenant_id = p_tenant;
END $$;

-- ------------------------------------------------------------ simulated site lifecycle (pw_sim_admin)

-- Creates the tenant, site, clock, connector, sync state and a BUILDING run. Idempotent for the
-- tenant, site and connector; refuses to start a run while another one is current.
CREATE FUNCTION pw.bootstrap_simulated_site(
  p_tenant uuid, p_tenant_code text, p_tenant_name text,
  p_site uuid, p_site_code text, p_site_name text, p_region text, p_time_zone text,
  p_width numeric, p_depth numeric, p_connector uuid,
  p_run uuid, p_seed bigint, p_dataset text, p_history_start timestamptz,
  p_clock_start timestamptz, p_started_by text)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pw, pg_temp
AS $$
DECLARE s text;
BEGIN
  PERFORM pw.assert_tenant(p_tenant);
  INSERT INTO pw.tenant (id, code, name, currency) VALUES (p_tenant, p_tenant_code, p_tenant_name, 'LKR')
  ON CONFLICT (id) DO NOTHING;
  INSERT INTO pw.site (id, tenant_id, code, name, region_code, time_zone, width_m, depth_m, data_status)
  VALUES (p_site, p_tenant, p_site_code, p_site_name, p_region, p_time_zone, p_width, p_depth, 'LOADING')
  ON CONFLICT (id) DO NOTHING;
  INSERT INTO pw.site_clock (site_id, tenant_id, mode, sim_time, state)
  VALUES (p_site, p_tenant, 'SIMULATED', p_history_start, 'PAUSED')
  ON CONFLICT (site_id) DO NOTHING;
  INSERT INTO pw.connector (id, tenant_id, site_id, connector_type)
  VALUES (p_connector, p_tenant, p_site, 'SIMULATOR')
  ON CONFLICT (id) DO NOTHING;
  FOREACH s IN ARRAY ARRAY['MASTER', 'MOVEMENTS', 'ORDERS', 'ALLOCATIONS'] LOOP
    INSERT INTO pw.sync_state (connector_id, stream, tenant_id, site_id)
    VALUES (p_connector, s, p_tenant, p_site)
    ON CONFLICT (connector_id, stream) DO NOTHING;
  END LOOP;

  IF EXISTS (SELECT 1 FROM pw.simulation_run WHERE id = p_run) THEN
    RETURN;
  END IF;
  INSERT INTO pw.simulation_run (id, tenant_id, site_id, seed, dataset_version, history_start,
                                 clock_start, status, started_at, started_by)
  VALUES (p_run, p_tenant, p_site, p_seed, p_dataset, p_history_start, p_clock_start, 'BUILDING',
          clock_timestamp(), p_started_by);
END $$;

-- Completes a load: run ACTIVE and site READY in one transaction (CWM 7.4 step 3).
CREATE FUNCTION pw.complete_load(p_tenant uuid, p_site uuid, p_run uuid, p_hash text)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pw, pg_temp
AS $$
BEGIN
  PERFORM pw.assert_tenant(p_tenant);
  UPDATE pw.simulation_run SET status = 'ACTIVE', dataset_hash = p_hash
   WHERE id = p_run AND site_id = p_site AND tenant_id = p_tenant
     AND status = 'BUILDING' AND history_loaded_at IS NOT NULL;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'run is not a fully loaded BUILDING run' USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
  UPDATE pw.site SET data_status = 'READY', data_status_detail = NULL
   WHERE id = p_site AND tenant_id = p_tenant;
END $$;

-- Marks a load as failed: partial data never becomes visible as ready.
CREATE FUNCTION pw.fail_load(p_tenant uuid, p_site uuid, p_run uuid, p_detail text)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pw, pg_temp
AS $$
BEGIN
  PERFORM pw.assert_tenant(p_tenant);
  UPDATE pw.simulation_run SET status = 'FAILED', status_detail = p_detail, ended_at = clock_timestamp()
   WHERE id = p_run AND site_id = p_site AND tenant_id = p_tenant AND status = 'BUILDING';
  UPDATE pw.site SET data_status = 'FAILED', data_status_detail = p_detail
   WHERE id = p_site AND tenant_id = p_tenant;
END $$;

-- ------------------------------------------------------------ grants (CWM 11.3)

REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA pw FROM PUBLIC;

GRANT USAGE ON SCHEMA pw TO pw_app, pw_ingest, pw_sim_admin;
GRANT USAGE ON SCHEMA sem TO pw_agent_read, pw_evaluator;

-- pw_app: read for the API (writes arrive in later slices through their own functions).
GRANT SELECT ON ALL TABLES IN SCHEMA pw TO pw_app;

-- pw_ingest: the ingestion function and the cursor; reads only what the worker needs.
GRANT EXECUTE ON FUNCTION pw.apply_record(uuid, uuid, uuid, uuid, text, timestamptz, text, boolean, jsonb) TO pw_ingest;
GRANT EXECUTE ON FUNCTION pw.set_cursor(uuid, uuid, text) TO pw_ingest;
GRANT SELECT ON pw.connector, pw.sync_state, pw.simulation_run TO pw_ingest;

-- pw_sim_admin: site lifecycle, plus read access to compute the dataset hash.
GRANT EXECUTE ON FUNCTION pw.bootstrap_simulated_site(uuid, text, text, uuid, text, text, text, text,
  numeric, numeric, uuid, uuid, bigint, text, timestamptz, timestamptz, text) TO pw_sim_admin;
GRANT EXECUTE ON FUNCTION pw.complete_load(uuid, uuid, uuid, text) TO pw_sim_admin;
GRANT EXECUTE ON FUNCTION pw.fail_load(uuid, uuid, uuid, text) TO pw_sim_admin;
GRANT SELECT ON ALL TABLES IN SCHEMA pw TO pw_sim_admin;

-- Helpers used inside policies must be callable by every role that reads tables.
GRANT EXECUTE ON FUNCTION pw.current_tenant() TO pw_app, pw_ingest, pw_sim_admin;
