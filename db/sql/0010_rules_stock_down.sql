-- Reverses 0010: drops the rules and views, and restores sem.product and sem.location to their
-- 0005 definitions (a view cannot drop columns, so they are recreated with their grants).
DROP FUNCTION IF EXISTS sem.zone_summary(uuid, timestamptz, uuid);
DROP FUNCTION IF EXISTS sem.low_stock(uuid, timestamptz, uuid);
DROP FUNCTION IF EXISTS sem.capacity_hotspots(uuid, timestamptz, uuid);
DROP FUNCTION IF EXISTS sem.rack_utilization(uuid, timestamptz, uuid);
DROP FUNCTION IF EXISTS sem.zone_utilization(uuid, timestamptz, uuid);
DROP FUNCTION IF EXISTS sem.bin_utilization(uuid, timestamptz, uuid);
DROP VIEW IF EXISTS sem.replenishment_policy;
DROP VIEW IF EXISTS sem.rack;
DROP VIEW IF EXISTS sem.zone;

DROP VIEW sem.product;
CREATE VIEW sem.product AS
SELECT p.id AS product_id, p.sku, p.name, p.brand, c.code AS category_code,
       c.name AS category_name, p.uom, p.unit_value, p.is_active
  FROM pw.product p
  JOIN pw.category c ON c.id = p.category_id AND c.tenant_id = p.tenant_id;

DROP VIEW sem.location;
CREATE VIEW sem.location AS
SELECT b.id AS bin_id, b.site_id, b.code AS bin_code, b.bin_role,
       (b.is_active AND b.bin_role IN ('PICK', 'RESERVE', 'BULK')) AS storable,
       b.is_active, z.id AS zone_id, z.code AS zone_code, z.name AS zone_name, z.zone_type,
       r.id AS rack_id, r.code AS rack_code, b.bay, b.level
  FROM pw.bin b
  JOIN pw.zone z ON z.id = b.zone_id AND z.tenant_id = b.tenant_id
  LEFT JOIN pw.rack r ON r.id = b.rack_id AND r.tenant_id = b.tenant_id;

REVOKE ALL ON sem.product, sem.location FROM PUBLIC;
GRANT SELECT ON sem.product, sem.location TO pw_agent_read;
