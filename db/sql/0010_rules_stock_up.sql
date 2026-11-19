-- Stock and capacity rules (Slice 2 step 1b). Runs as pw_owner.
-- Specs: semantic-layer-and-query-dsl.md 4.1 (stock), 4.4 (capacity), 6 (LOW_STOCK,
-- CAPACITY_HOTSPOT), 8 (get_low_stock, get_zone_summary, get_capacity_hotspots).
--
-- Same access model as 0005: views owned by pw_owner read forced-RLS pw tables, functions are
-- SECURITY INVOKER over sem views only. Every rule function takes site, as_of and the settings
-- version (semantic spec 2), even when its definition uses no setting, so all callers pass the
-- same three inputs. Stock describes current balances, so each function asserts as_of is now.

-- ------------------------------------------------------------ views

-- Appended columns only (CREATE OR REPLACE VIEW keeps existing ones in place).
CREATE OR REPLACE VIEW sem.product AS
SELECT p.id AS product_id, p.sku, p.name, p.brand, c.code AS category_code,
       c.name AS category_name, p.uom, p.unit_value, p.is_active,
       p.unit_volume_m3, p.unit_weight_kg, c.id AS category_id
  FROM pw.product p
  JOIN pw.category c ON c.id = p.category_id AND c.tenant_id = p.tenant_id;

CREATE OR REPLACE VIEW sem.location AS
SELECT b.id AS bin_id, b.site_id, b.code AS bin_code, b.bin_role,
       (b.is_active AND b.bin_role IN ('PICK', 'RESERVE', 'BULK')) AS storable,
       b.is_active, z.id AS zone_id, z.code AS zone_code, z.name AS zone_name, z.zone_type,
       r.id AS rack_id, r.code AS rack_code, b.bay, b.level,
       z.is_prime, b.capacity_volume_m3, b.capacity_weight_kg
  FROM pw.bin b
  JOIN pw.zone z ON z.id = b.zone_id AND z.tenant_id = b.tenant_id
  LEFT JOIN pw.rack r ON r.id = b.rack_id AND r.tenant_id = b.tenant_id;

-- Zones on their own, so a zone with no active bins still appears (with null utilization).
CREATE VIEW sem.zone AS
SELECT id AS zone_id, site_id, code AS zone_code, name AS zone_name, zone_type, is_prime, is_active
  FROM pw.zone;

CREATE VIEW sem.rack AS
SELECT r.id AS rack_id, r.site_id, r.code AS rack_code, r.zone_id
  FROM pw.rack r;

CREATE VIEW sem.replenishment_policy AS
SELECT site_id, product_id, pick_bin_id, min_qty, max_qty
  FROM pw.replenishment_policy;

-- ------------------------------------------------------------ capacity (spec 4.4)

-- Per active bin: volume and weight used by its stock, and utilization against capacity.
-- Both capacities are greater than 0 (CWM), so no division by zero for a bin.
CREATE FUNCTION sem.bin_utilization(p_site uuid, p_as_of timestamptz, p_settings uuid)
RETURNS TABLE (bin_id uuid, bin_code text, zone_id uuid, rack_id uuid,
               volume_used numeric, weight_used numeric,
               capacity_volume_m3 numeric, capacity_weight_kg numeric,
               utilization_pct numeric, over_capacity boolean)
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path = sem, pg_temp
AS $$
BEGIN
  PERFORM sem.assert_current(p_site, p_as_of);
  RETURN QUERY
  WITH used AS (
    SELECT l.bin_id, l.bin_code, l.zone_id, l.rack_id, l.capacity_volume_m3, l.capacity_weight_kg,
           coalesce(sum(s.on_hand * p.unit_volume_m3), 0) AS volume_used,
           coalesce(sum(s.on_hand * p.unit_weight_kg), 0) AS weight_used
      FROM sem.location l
      LEFT JOIN sem.stock s ON s.bin_id = l.bin_id
      LEFT JOIN sem.product p ON p.product_id = s.product_id
     WHERE l.site_id = p_site AND l.is_active
     GROUP BY l.bin_id, l.bin_code, l.zone_id, l.rack_id, l.capacity_volume_m3, l.capacity_weight_kg
  )
  SELECT u.bin_id, u.bin_code, u.zone_id, u.rack_id, u.volume_used, u.weight_used,
         u.capacity_volume_m3, u.capacity_weight_kg,
         round(100 * greatest(u.volume_used / u.capacity_volume_m3, u.weight_used / u.capacity_weight_kg), 1),
         100 * greatest(u.volume_used / u.capacity_volume_m3, u.weight_used / u.capacity_weight_kg) > 100
    FROM used u;
END $$;

-- Zone and rack: the same principle over their active bins, ratio of sums (spec 4.4).
-- A zone with no active bins has null utilization ("no active storage"), never 0 percent.
CREATE FUNCTION sem.zone_utilization(p_site uuid, p_as_of timestamptz, p_settings uuid)
RETURNS TABLE (zone_id uuid, utilization_pct numeric, active_bins integer, over_capacity_bins integer)
LANGUAGE sql STABLE SECURITY INVOKER SET search_path = sem, pg_temp
AS $$
  SELECT z.zone_id,
         CASE WHEN count(b.bin_id) = 0 THEN NULL ELSE
           round(100 * greatest(sum(b.volume_used) / sum(b.capacity_volume_m3),
                                sum(b.weight_used) / sum(b.capacity_weight_kg)), 1) END,
         count(b.bin_id)::integer,
         count(b.bin_id) FILTER (WHERE b.over_capacity)::integer
    FROM sem.zone z
    LEFT JOIN sem.bin_utilization(p_site, p_as_of, p_settings) b ON b.zone_id = z.zone_id
   WHERE z.site_id = p_site AND z.is_active
   GROUP BY z.zone_id
$$;

CREATE FUNCTION sem.rack_utilization(p_site uuid, p_as_of timestamptz, p_settings uuid)
RETURNS TABLE (rack_id uuid, utilization_pct numeric, active_bins integer, over_capacity_bins integer)
LANGUAGE sql STABLE SECURITY INVOKER SET search_path = sem, pg_temp
AS $$
  SELECT r.rack_id,
         CASE WHEN count(b.bin_id) = 0 THEN NULL ELSE
           round(100 * greatest(sum(b.volume_used) / sum(b.capacity_volume_m3),
                                sum(b.weight_used) / sum(b.capacity_weight_kg)), 1) END,
         count(b.bin_id)::integer,
         count(b.bin_id) FILTER (WHERE b.over_capacity)::integer
    FROM sem.rack r
    LEFT JOIN sem.bin_utilization(p_site, p_as_of, p_settings) b ON b.rack_id = r.rack_id
   WHERE r.site_id = p_site
   GROUP BY r.rack_id
$$;

-- CAPACITY_HOTSPOT (spec 6): a zone at or above the threshold T, or with any bin over capacity.
CREATE FUNCTION sem.capacity_hotspots(p_site uuid, p_as_of timestamptz, p_settings uuid)
RETURNS TABLE (zone_id uuid, zone_code text, zone_name text, utilization_pct numeric,
               over_capacity_bins integer, threshold_pct numeric)
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path = sem, pg_temp
AS $$
DECLARE
  v_threshold numeric := sem.setting(p_settings, 'capacity_threshold_pct');
BEGIN
  RETURN QUERY
  SELECT z.zone_id, z.zone_code, z.zone_name, u.utilization_pct, u.over_capacity_bins, v_threshold
    FROM sem.zone_utilization(p_site, p_as_of, p_settings) u
    JOIN sem.zone z ON z.zone_id = u.zone_id
   WHERE u.utilization_pct >= v_threshold OR u.over_capacity_bins > 0
   ORDER BY u.utilization_pct DESC NULLS LAST, z.zone_code;
END $$;

-- ------------------------------------------------------------ stock rules

-- LOW_STOCK (spec 6, Slice 2 D2): the product has a replenishment policy and available in its
-- pick bin is at or below min_qty. A policy bin with no balance row holds nothing: available 0.
CREATE FUNCTION sem.low_stock(p_site uuid, p_as_of timestamptz, p_settings uuid)
RETURNS TABLE (product_id uuid, sku text, name text, bin_id uuid, bin_code text, zone_id uuid,
               zone_code text, on_hand integer, allocated integer, available integer,
               min_qty integer, max_qty integer)
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path = sem, pg_temp
AS $$
BEGIN
  PERFORM sem.assert_current(p_site, p_as_of);
  RETURN QUERY
  SELECT p.product_id, p.sku, p.name, l.bin_id, l.bin_code, l.zone_id, l.zone_code,
         coalesce(s.on_hand, 0), coalesce(s.allocated, 0), coalesce(s.available, 0),
         rp.min_qty, rp.max_qty
    FROM sem.replenishment_policy rp
    JOIN sem.product p ON p.product_id = rp.product_id
    JOIN sem.location l ON l.bin_id = rp.pick_bin_id
    LEFT JOIN sem.stock s ON s.bin_id = rp.pick_bin_id AND s.product_id = rp.product_id
   WHERE rp.site_id = p_site AND coalesce(s.available, 0) <= rp.min_qty
   ORDER BY coalesce(s.available, 0) - rp.min_qty, p.sku, l.bin_code;
END $$;

-- get_zone_summary (spec 8): stock and utilization by active zone.
CREATE FUNCTION sem.zone_summary(p_site uuid, p_as_of timestamptz, p_settings uuid)
RETURNS TABLE (zone_id uuid, zone_code text, zone_name text, zone_type text, is_prime boolean,
               products integer, on_hand bigint, allocated bigint, available bigint,
               stock_value numeric, utilization_pct numeric, active_bins integer,
               over_capacity_bins integer)
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path = sem, pg_temp
AS $$
BEGIN
  PERFORM sem.assert_current(p_site, p_as_of);
  RETURN QUERY
  WITH stock AS (
    SELECT l.zone_id,
           count(DISTINCT s.product_id) FILTER (WHERE s.on_hand > 0) AS products,
           coalesce(sum(s.on_hand), 0) AS on_hand,
           coalesce(sum(s.allocated), 0) AS allocated,
           coalesce(sum(s.available), 0) AS available,
           coalesce(sum(s.on_hand * p.unit_value), 0) AS stock_value
      FROM sem.stock s
      JOIN sem.location l ON l.bin_id = s.bin_id
      JOIN sem.product p ON p.product_id = s.product_id
     WHERE s.site_id = p_site
     GROUP BY l.zone_id
  )
  SELECT z.zone_id, z.zone_code, z.zone_name, z.zone_type, z.is_prime,
         coalesce(st.products, 0)::integer, coalesce(st.on_hand, 0)::bigint,
         coalesce(st.allocated, 0)::bigint, coalesce(st.available, 0)::bigint,
         coalesce(st.stock_value, 0), u.utilization_pct, u.active_bins, u.over_capacity_bins
    FROM sem.zone_utilization(p_site, p_as_of, p_settings) u
    JOIN sem.zone z ON z.zone_id = u.zone_id
    LEFT JOIN stock st ON st.zone_id = z.zone_id
   ORDER BY coalesce(st.on_hand, 0) DESC, z.zone_code;
END $$;

-- ------------------------------------------------------------ grants

REVOKE ALL ON sem.zone, sem.rack, sem.replenishment_policy FROM PUBLIC;
GRANT SELECT ON sem.zone, sem.rack, sem.replenishment_policy TO pw_agent_read;
REVOKE EXECUTE ON FUNCTION
  sem.bin_utilization(uuid, timestamptz, uuid), sem.zone_utilization(uuid, timestamptz, uuid),
  sem.rack_utilization(uuid, timestamptz, uuid), sem.capacity_hotspots(uuid, timestamptz, uuid),
  sem.low_stock(uuid, timestamptz, uuid), sem.zone_summary(uuid, timestamptz, uuid)
FROM PUBLIC;
GRANT EXECUTE ON FUNCTION
  sem.bin_utilization(uuid, timestamptz, uuid), sem.zone_utilization(uuid, timestamptz, uuid),
  sem.rack_utilization(uuid, timestamptz, uuid), sem.capacity_hotspots(uuid, timestamptz, uuid),
  sem.low_stock(uuid, timestamptz, uuid), sem.zone_summary(uuid, timestamptz, uuid)
TO pw_agent_read;
