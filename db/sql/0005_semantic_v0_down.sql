-- Reverses 0005. Semantic objects hold no data.
DROP FUNCTION IF EXISTS sem.search_products(text, integer);
DROP FUNCTION IF EXISTS sem.bin_contents(uuid, timestamptz, uuid);
DROP FUNCTION IF EXISTS sem.product_locations(uuid, timestamptz, uuid);
DROP FUNCTION IF EXISTS sem.assert_current(uuid, timestamptz);
DROP VIEW IF EXISTS sem.stock;
DROP VIEW IF EXISTS sem.location;
DROP VIEW IF EXISTS sem.product;
DROP VIEW IF EXISTS sem.stream_freshness;
DROP VIEW IF EXISTS sem.site_now;
REVOKE EXECUTE ON FUNCTION pw.current_tenant() FROM pw_agent_read;
