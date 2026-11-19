-- PalletWise semantic layer v0 (Slice 1): site time, freshness, products, locations and stock.
-- Spec: semantic-layer-and-query-dsl.md sections 2, 3, 4.1, 7.5 and 8. Runs as pw_owner.
--
-- Access model. pw_agent_read has USAGE on sem only (CWM 11.3). The views below are owned by
-- pw_owner, so they read pw tables with the owner's privileges; row-level security is FORCED
-- on those tables, so the tenant policy still applies through the views. The functions are
-- SECURITY INVOKER and read only sem views: they run as the caller, with the caller's tenant.

-- ------------------------------------------------------------ views

-- The site's "now" is its clock, never the database clock (semantic spec 3).
CREATE VIEW sem.site_now AS
SELECT s.id AS site_id, s.code AS site_code, s.name AS site_name, s.time_zone, s.data_status,
       c.sim_time AS as_of
  FROM pw.site s
  JOIN pw.site_clock c ON c.site_id = s.id AND c.tenant_id = s.tenant_id;

-- Freshness per stream (CWM 10.2): a stream with any connector that never synced is null.
CREATE VIEW sem.stream_freshness AS
SELECT site_id, stream,
       CASE WHEN bool_or(data_as_of IS NULL) THEN NULL ELSE min(data_as_of) END AS data_as_of
  FROM pw.sync_state
 GROUP BY site_id, stream;

CREATE VIEW sem.product AS
SELECT p.id AS product_id, p.sku, p.name, p.brand, c.code AS category_code,
       c.name AS category_name, p.uom, p.unit_value, p.is_active
  FROM pw.product p
  JOIN pw.category c ON c.id = p.category_id AND c.tenant_id = p.tenant_id;

-- Storable bin (semantic spec 4.1): active, role PICK, RESERVE or BULK. Stock in staging,
-- receiving and returns bins is in transit, not available for orders.
CREATE VIEW sem.location AS
SELECT b.id AS bin_id, b.site_id, b.code AS bin_code, b.bin_role,
       (b.is_active AND b.bin_role IN ('PICK', 'RESERVE', 'BULK')) AS storable,
       b.is_active, z.id AS zone_id, z.code AS zone_code, z.name AS zone_name, z.zone_type,
       r.id AS rack_id, r.code AS rack_code, b.bay, b.level
  FROM pw.bin b
  JOIN pw.zone z ON z.id = b.zone_id AND z.tenant_id = b.tenant_id
  LEFT JOIN pw.rack r ON r.id = b.rack_id AND r.tenant_id = b.tenant_id;

-- Stock (semantic spec 4.1): on_hand, allocated and available = on_hand - allocated.
CREATE VIEW sem.stock AS
SELECT site_id, bin_id, product_id, on_hand, allocated, available, last_movement_at
  FROM pw.inventory_balance
 WHERE on_hand > 0 OR allocated > 0;

-- ------------------------------------------------------------ functions

-- Stock definitions describe the current balances, so they are valid only at the site's
-- current time. A caller that read as_of earlier in another snapshot is told to re-read it.
CREATE FUNCTION sem.assert_current(p_site uuid, p_as_of timestamptz) RETURNS void
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path = sem, pg_temp
AS $$
BEGIN
  IF p_as_of IS DISTINCT FROM (SELECT as_of FROM sem.site_now WHERE site_id = p_site) THEN
    RAISE EXCEPTION 'as_of is not the current site time' USING ERRCODE = 'object_not_in_prerequisite_state';
  END IF;
END $$;

-- find_product_locations (semantic spec 8): stock by bin for one product, storable bins
-- first, then by on_hand descending and bin code.
CREATE FUNCTION sem.product_locations(p_site uuid, p_as_of timestamptz, p_product uuid)
RETURNS TABLE (bin_id uuid, bin_code text, bin_role text, storable boolean, zone_id uuid,
               zone_code text, rack_id uuid, rack_code text, on_hand integer, allocated integer,
               available integer, last_movement_at timestamptz)
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path = sem, pg_temp
AS $$
BEGIN
  PERFORM sem.assert_current(p_site, p_as_of);
  RETURN QUERY
  SELECT l.bin_id, l.bin_code, l.bin_role, l.storable, l.zone_id, l.zone_code, l.rack_id,
         l.rack_code, s.on_hand, s.allocated, s.available, s.last_movement_at
    FROM sem.stock s
    JOIN sem.location l ON l.bin_id = s.bin_id
   WHERE s.site_id = p_site AND s.product_id = p_product
   ORDER BY l.storable DESC, s.on_hand DESC, l.bin_code;
END $$;

-- get_bin_contents (semantic spec 8): stock by product for one bin, with last_movement_at.
CREATE FUNCTION sem.bin_contents(p_site uuid, p_as_of timestamptz, p_bin uuid)
RETURNS TABLE (product_id uuid, sku text, name text, on_hand integer, allocated integer,
               available integer, last_movement_at timestamptz)
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path = sem, pg_temp
AS $$
BEGIN
  PERFORM sem.assert_current(p_site, p_as_of);
  RETURN QUERY
  SELECT p.product_id, p.sku, p.name, s.on_hand, s.allocated, s.available, s.last_movement_at
    FROM sem.stock s
    JOIN sem.product p ON p.product_id = s.product_id
   WHERE s.site_id = p_site AND s.bin_id = p_bin
   ORDER BY s.on_hand DESC, p.sku;
END $$;

-- Product lookup for a name or SKU typed by a person. Ranked: exact SKU, exact name, name
-- prefix, then every word contained in the name or SKU. LIKE wildcards in the query are
-- escaped, so they match literally.
CREATE FUNCTION sem.search_products(p_query text, p_limit integer)
RETURNS TABLE (product_id uuid, sku text, name text, brand text, category_name text, rank integer)
LANGUAGE sql STABLE SECURITY INVOKER SET search_path = sem, pg_temp
AS $$
  WITH q AS (
    SELECT lower(btrim(regexp_replace(p_query, '\s+', ' ', 'g'))) AS text
  ), words AS (
    SELECT replace(replace(replace(w, '\', '\\'), '%', '\%'), '_', '\_') AS pattern
      FROM q, regexp_split_to_table(q.text, ' ') AS w
     WHERE w <> ''
  )
  SELECT p.product_id, p.sku, p.name, p.brand, p.category_name,
         CASE WHEN lower(p.sku) = q.text THEN 0
              WHEN lower(p.name) = q.text THEN 1
              WHEN lower(p.name) LIKE (SELECT replace(replace(replace(q.text, '\', '\\'), '%', '\%'), '_', '\_')) || '%' THEN 2
              ELSE 3 END AS rank
    FROM sem.product p, q
   WHERE p.is_active
     AND EXISTS (SELECT 1 FROM words)
     AND NOT EXISTS (
       SELECT 1 FROM words
        WHERE lower(p.name) NOT LIKE '%' || words.pattern || '%'
          AND lower(p.sku) NOT LIKE '%' || words.pattern || '%')
   ORDER BY rank, p.sku
   LIMIT least(greatest(p_limit, 1), 50)
$$;

-- ------------------------------------------------------------ grants

REVOKE ALL ON ALL TABLES IN SCHEMA sem FROM PUBLIC;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA sem FROM PUBLIC;
GRANT SELECT ON sem.site_now, sem.stream_freshness, sem.product, sem.location, sem.stock TO pw_agent_read;
GRANT EXECUTE ON FUNCTION
  sem.assert_current(uuid, timestamptz),
  sem.product_locations(uuid, timestamptz, uuid),
  sem.bin_contents(uuid, timestamptz, uuid),
  sem.search_products(text, integer)
TO pw_agent_read;
-- The tenant policies call pw.current_tenant() with the privileges of the querying role, so the
-- reader needs EXECUTE on that one function. It still has no USAGE on schema pw and no table
-- privileges there: raw tables stay refused.
GRANT EXECUTE ON FUNCTION pw.current_tenant() TO pw_agent_read;
