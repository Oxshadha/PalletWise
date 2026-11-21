"""The governed query guard (slice-5-acceptance step 2): deterministic, no model, no database.

Three sets: adversarial queries (100 percent refused), semantically invalid ones (100 percent
refused by the correctness rules) and valid ones (accepted, deparsed stably).
"""

import pytest
from pglast.parser import get_postgresql_version

from palletwise.governed.guard import Refused, check

ADVERSARIAL = [
    "DELETE FROM gq.stock",
    "UPDATE gq.stock SET on_hand = 0",
    "INSERT INTO gq.stock VALUES (1)",
    "SELECT * FROM gq.stock; DROP TABLE pw.movement",
    "SELECT * FROM pw.movement",
    "SELECT * FROM sem.stock",
    "SELECT * FROM pg_catalog.pg_authid",
    "SELECT * FROM pg_user",
    "SELECT * FROM information_schema.tables",
    "SELECT pw.current_tenant() FROM gq.stock",
    "SELECT set_config('app.site_id', '00000000-0000-0000-0000-000000000000', true) FROM gq.stock",
    "SET app.tenant_id = 'x'",
    "SELECT pg_sleep(10) FROM gq.stock",
    "SELECT pg_read_file('/etc/passwd') FROM gq.stock",
    "SELECT lo_import('/etc/passwd') FROM gq.stock",
    "SELECT * FROM dblink('host=x', 'select 1') AS t(a int)",
    "SELECT * FROM gq.stock FOR UPDATE",
    "WITH x AS (DELETE FROM gq.stock RETURNING *) SELECT * FROM x",
    "WITH x AS (SELECT * FROM gq.stock) SELECT * FROM x",
    "SELECT * INTO new_table FROM gq.stock",
    "COPY (SELECT * FROM gq.stock) TO '/tmp/x'",
    "SELECT on_hand::regclass FROM gq.stock",
    "SELECT on_hand::public.text FROM gq.stock",
    "SELECT on_hand::text::regproc FROM gq.stock",
    "SELECT * FROM (SELECT * FROM gq.stock) s",
    "SELECT rank() OVER (ORDER BY on_hand) FROM gq.stock",
    "SELECT sku FROM gq.products UNION SELECT relname FROM pg_class",
    "SELECT * FROM generate_series(1, 1000000000)",
    "SELECT * FROM gq.stock WHERE on_hand > $1",
    "SELECT now() FROM gq.stock",
    "SELECT current_date FROM gq.stock",
    "SELECT * FROM gq.stock LIMIT (SELECT count(*) FROM gq.movements)",
    "SELECT pg_catalog.lower(sku) FROM gq.products JOIN pg_catalog.pg_proc ON true",
    "SELECT * FROM gq.stock s, gq.products p",
    "SELECT txid_current() FROM gq.stock",
    "SELECT * FROM gq.stock OPERATOR(pg_catalog.=) 1",
    "SELECT sku FROM gq.products WHERE sku ~ '.*'",
]

INVALID = [
    # rule 3: non-additive measures
    "SELECT sum(unit_value) FROM gq.products",
    "SELECT avg(score) FROM gq.exceptions",
    # rule 2: summing the one side over the many side
    "SELECT sum(o.line_count) FROM gq.order_lines l JOIN gq.orders o ON l.order_id = o.order_id",
    "SELECT sum(p.unit_value) FROM gq.stock s JOIN gq.products p ON s.product_id = p.product_id",
    # rule 1: joins that multiply rows
    "SELECT count(*) FROM gq.orders o JOIN gq.order_lines l ON l.order_id = o.order_id",
    "SELECT sum(s.on_hand) FROM gq.stock s JOIN gq.allocations a ON a.bin_id = s.bin_id",
    "SELECT count(*) FROM gq.stock s JOIN gq.movements m ON m.product_id = s.product_id",
    "SELECT count(*) FROM gq.order_lines l JOIN gq.orders o ON l.qty_ordered = o.line_count",
    # rule 4: units mixed
    "SELECT sum(on_hand + inventory_value) FROM gq.stock",
    "SELECT sum(on_hand) - sum(inventory_value) FROM gq.stock",
    # rule 5 and definitions
    "SELECT count(DISTINCT on_hand) FROM gq.stock",
    "SELECT sum(bay) FROM gq.locations",
    "SELECT days_of_cover FROM gq.stock",
    "SELECT sum(margin) FROM gq.products",
    "SELECT sum(on_hand) FROM gq.stock WHERE sum(on_hand) > 5",
    # a scalar subquery keeps the join rule: its value would change with repeated rows
    "SELECT sku, (SELECT sum(s.on_hand) FROM gq.stock s JOIN gq.exceptions x ON x.product_id = s.product_id)"
    " FROM gq.products",
    "SELECT count(*) FROM gq.products p WHERE p.unit_value > (SELECT sum(x.score) FROM gq.exceptions x)",
]

VALID = [
    # the ADR's three example questions
    "SELECT o.customer_code, count(DISTINCT o.order_id) AS orders FROM gq.order_lines l"
    " JOIN gq.orders o ON l.order_id = o.order_id JOIN gq.products p ON l.product_id = p.product_id"
    " WHERE p.sku = 'SKU-258' AND o.dispatch_deadline >= date_trunc('month', (SELECT as_of FROM gq.site_now))"
    " GROUP BY o.customer_code HAVING count(DISTINCT o.order_id) > 2 ORDER BY orders DESC",
    "SELECT count(*) FROM gq.orders WHERE line_count > 10"
    " AND dispatch_deadline >= (SELECT as_of FROM gq.site_now) - interval '7 days'",
    "SELECT DISTINCT l.rack_code FROM gq.locations l WHERE l.rack_code IS NOT NULL"
    " AND EXISTS (SELECT 1 FROM gq.stock s JOIN gq.exceptions x ON x.product_id = s.product_id"
    "   WHERE s.bin_id = l.bin_id AND x.exception_type = 'DEAD_STOCK' AND x.status <> 'RESOLVED')"
    " AND EXISTS (SELECT 1 FROM gq.stock s JOIN gq.exceptions x ON x.product_id = s.product_id"
    "   WHERE s.bin_id = l.bin_id AND x.exception_type = 'LOW_STOCK' AND x.status <> 'RESOLVED')",
    # more shapes the first version allows
    "SELECT p.category_name, sum(s.on_hand) AS units, sum(s.inventory_value) AS value FROM gq.stock s"
    " JOIN gq.products p ON s.product_id = p.product_id GROUP BY p.category_name ORDER BY value DESC LIMIT 5",
    "SELECT l.zone_code, count(*) FROM gq.stock s JOIN gq.locations l ON s.bin_id = l.bin_id"
    " WHERE s.available = 0 GROUP BY l.zone_code",
    "SELECT max(unit_value), min(unit_value), avg(unit_value) FROM gq.products",
    "SELECT movement_type, sum(qty) FROM gq.movements"
    " WHERE occurred_at >= (SELECT as_of FROM gq.site_now) - interval '30 days' GROUP BY 1",
    "SELECT sku, name FROM gq.products WHERE lower(name) LIKE '%rice%'",
    "SELECT o.order_no, o.dispatch_deadline FROM gq.orders o WHERE o.shipped_at IS NULL"
    " AND o.dispatch_deadline < (SELECT as_of FROM gq.site_now)",
    "SELECT count(*) FROM gq.calendar WHERE is_operating AND date BETWEEN '2026-04-01' AND '2026-04-30'",
    "SELECT sum(on_hand) - sum(allocated) AS free FROM gq.stock",
    "SELECT round(avg(qty_ordered), 1) FROM gq.order_lines WHERE is_open",
    "SELECT max(score) FROM gq.exceptions WHERE status = 'OPEN'",
]


def test_the_parser_matches_the_server_major_version() -> None:
    assert get_postgresql_version()[0] == 17  # the stack runs PostgreSQL 17 (infra/images.env)


@pytest.mark.parametrize("sql", ADVERSARIAL)
def test_adversarial_queries_are_refused(sql: str) -> None:
    with pytest.raises(Refused):
        check(sql)


@pytest.mark.parametrize("sql", INVALID)
def test_semantically_invalid_queries_are_refused(sql: str) -> None:
    with pytest.raises(Refused) as refused:
        check(sql)
    assert refused.value.rule in {
        "non_additive", "measure_grain", "join_fanout", "units", "count", "aggregate", "unknown_column",
        "construct", "join",
    }  # fmt: skip


@pytest.mark.parametrize("sql", VALID)
def test_valid_queries_are_accepted_and_deparse_stably(sql: str) -> None:
    accepted = check(sql)
    assert check(accepted.sql).sql == accepted.sql  # the normalized text is itself accepted, unchanged


def test_comments_never_survive_normalization() -> None:
    accepted = check("SELECT sku /* hidden */ FROM gq.products -- trailing")
    assert "hidden" not in accepted.sql and "trailing" not in accepted.sql


def test_refusals_say_why_in_words() -> None:
    with pytest.raises(Refused) as refused:
        check("SELECT sum(unit_value) FROM gq.products")
    assert refused.value.rule == "non_additive" and "cannot be aggregated with sum" in str(refused.value)


def test_the_tool_is_offered_only_after_a_passed_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Until a configuration passes, the copilot works exactly as without the governed tier."""
    from palletwise.governed import tool as governed
    from palletwise.tools.catalog import v1_tools

    monkeypatch.setattr(governed, "passed_fingerprints", set)
    assert v1_tools().get("run_governed_query") is None
    monkeypatch.setattr(governed, "passed_fingerprints", lambda: {"x"})
    assert v1_tools().get("run_governed_query") is not None


def test_an_eval_run_offers_the_tool_it_measures(monkeypatch: pytest.MonkeyPatch) -> None:
    """Found on ee15c06: the eval's 'evaluated' mark was an empty set, so the catalog never offered
    the tool and the run measured nothing."""
    from evals.__main__ import _Any
    from palletwise.governed import tool as governed
    from palletwise.tools.catalog import v1_tools

    monkeypatch.setattr(governed, "passed_fingerprints", _Any)
    assert v1_tools().get("run_governed_query") is not None and "any" in governed.passed_fingerprints()
