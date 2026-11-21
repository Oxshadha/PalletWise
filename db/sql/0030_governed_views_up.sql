-- Governed query tier, step 1 (ADR 0002 sections 3 and 5, slice-5-acceptance step 1). Runs as
-- pw_owner.
--
-- Schema gq: read-only views for the governed query tool only. Owner-rights views (not
-- security-invoker, so the query role is never granted the tables underneath) over tables with
-- forced RLS keyed on pw.current_tenant(), which raises when the tenant is missing; each site-scoped
-- view also filters on the request's site, current_setting('app.site_id'), which raises when it is
-- missing. Both settings are set by the runner from the authenticated session. Columns are the
-- semantic layer's; their meaning, grain, keys and measure rules are declared in
-- semantic/gq_catalog.yaml.

CREATE SCHEMA gq;

CREATE VIEW gq.site_now AS
SELECT n.as_of, n.time_zone, n.site_code, n.site_name
  FROM sem.site_now n WHERE n.site_id = current_setting('app.site_id')::uuid;

CREATE VIEW gq.products AS
SELECT p.product_id, p.sku, p.name, p.brand, p.category_code, p.category_name, p.uom, p.unit_value
  FROM sem.product p WHERE p.is_active;

CREATE VIEW gq.locations AS
SELECT l.bin_id, l.bin_code, l.bin_role, l.storable, l.zone_id, l.zone_code, l.zone_name, l.zone_type,
       l.rack_code, l.bay, l.level, l.is_prime
  FROM sem.location l WHERE l.site_id = current_setting('app.site_id')::uuid AND l.is_active;

CREATE VIEW gq.stock AS
SELECT s.bin_id, s.product_id, s.on_hand, s.allocated, s.available,
       round(s.on_hand * p.unit_value, 2) AS inventory_value, s.last_movement_at
  FROM sem.stock s JOIN sem.product p ON p.product_id = s.product_id
 WHERE s.site_id = current_setting('app.site_id')::uuid;

CREATE VIEW gq.movements AS
SELECT m.movement_type, m.product_id, m.qty, m.from_bin_id, m.to_bin_id, m.occurred_at, m.reason_code,
       m.order_line_id
  FROM sem.movement m WHERE m.site_id = current_setting('app.site_id')::uuid;

CREATE VIEW gq.orders AS
SELECT o.order_id, min(o.order_no) AS order_no, min(o.customer_code) AS customer_code,
       min(o.customer_brand) AS customer_brand, min(o.status) AS status,
       min(o.dispatch_deadline) AS dispatch_deadline, min(o.shipped_at) AS shipped_at,
       min(o.delivery_date) AS delivery_date, count(*)::int AS line_count
  FROM sem.order_line o WHERE o.site_id = current_setting('app.site_id')::uuid
 GROUP BY o.order_id;

CREATE VIEW gq.order_lines AS
SELECT o.order_line_id, o.order_id, o.line_no, o.product_id, o.qty_ordered, o.qty_allocated,
       o.qty_picked, o.qty_shipped, o.unallocated_qty, o.is_open
  FROM sem.order_line o WHERE o.site_id = current_setting('app.site_id')::uuid;

CREATE VIEW gq.allocations AS
SELECT a.order_line_id, a.product_id, a.bin_id, a.open_qty
  FROM sem.allocation a WHERE a.site_id = current_setting('app.site_id')::uuid;

CREATE VIEW gq.exceptions AS
SELECT x.exception_id, x.exception_type, x.product_id, x.zone_id, x.order_id, x.status, x.critical,
       x.score, x.first_detected_at, x.last_evaluated_at, x.acknowledged_at IS NOT NULL AS acknowledged
  FROM sem.exception x WHERE x.site_id = current_setting('app.site_id')::uuid;

CREATE VIEW gq.calendar AS
SELECT c.date, c.is_operating
  FROM sem.calendar c JOIN pw.site s ON s.region_code = c.region_code
 WHERE s.id = current_setting('app.site_id')::uuid;

-- pw_query_read: USAGE on gq and SELECT on its views, nothing else (ADR 0002 section 2). No CREATE
-- anywhere and no TEMPORARY (both revoked from PUBLIC at bootstrap), never BYPASSRLS; its
-- search_path is fixed at bootstrap (pg_catalog, gq), so every allowed name resolves to the
-- built-in object of that name.
REVOKE ALL ON SCHEMA gq FROM PUBLIC;
GRANT USAGE ON SCHEMA gq TO pw_query_read;
GRANT SELECT ON ALL TABLES IN SCHEMA gq TO pw_query_read;
-- Row-level security calls pw.current_tenant() with the querying role's rights. The role has no
-- USAGE on schema pw, so a query cannot name the function itself.
GRANT EXECUTE ON FUNCTION pw.current_tenant() TO pw_query_read;
