"""The query language end to end (semantic spec 7.4 stages 3 to 7; Slice 2 step 3c).

Runs exactly as the tool does: pw_agent_read, a read snapshot bound to the tenant. Expected
numbers are worked out by hand or come from the fixed tools, which must agree (semantic spec 10).
"""

from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg
import pytest
from fixture_site import FixtureSite

from palletwise.contracts.results import ResultEnvelope, ResultStatus
from palletwise.db.connection import role_pool, tenant_session
from palletwise.semantic import query, rules
from palletwise.semantic.query_request import QueryRejected, validate

pytestmark = pytest.mark.integration

Connect = Callable[[str], psycopg.Connection]
DAY = timedelta(days=1)


def ask(
    f: FixtureSite, raw: dict[str, Any], working_set: dict[str, list[UUID]] | None = None
) -> ResultEnvelope:
    with tenant_session(role_pool("PW_AGENT_READ"), f.tenant, read_snapshot=True) as conn:
        return query.run(conn, f.site, validate(raw), working_set)


def tool(f: FixtureSite, fn: Callable[..., ResultEnvelope], *args: Any) -> ResultEnvelope:
    with tenant_session(role_pool("PW_AGENT_READ"), f.tenant, read_snapshot=True) as conn:
        return fn(conn, f.site, *args)


@pytest.fixture
def shop(connect_as: Connect) -> FixtureSite:
    """Zone A (prime pick): A-01, A-02. Zone B (reserve): B-01. Staging S-01.
    COLA and WATER are Beverages, CHIPS is Snacks."""
    f = FixtureSite(connect_as)
    now = f.as_of()
    a, b, s = f.zone("A", zone_type="FAST_PICK", prime=True), f.zone("B"), f.zone("S", zone_type="STAGING")
    a1, a2, b1, s1 = (
        f.bin(a, "A-01"),
        f.bin(a, "A-02"),
        f.bin(b, "B-01", role="RESERVE"),
        f.bin(s, "S-01", role="STAGING"),
    )
    cola = f.product("COLA", value=100, category="Beverages", brand="Fizz")
    water = f.product("WATER", value=40, category="Beverages", brand="Pure")
    chips = f.product("CHIPS", value=60, category="Snacks", brand="Crunch")
    f.stock(a1, cola, 30)
    f.stock(b1, cola, 70)  # COLA: 100 storable on hand
    f.stock(a2, water, 50)
    f.stock(b1, water, 150)  # WATER: 200
    f.stock(a1, chips, 20)
    f.stock(s1, chips, 5)  # staging: stock, but not storable
    for product, qty, ago in ((cola, 12, 1), (cola, 8, 3), (water, 30, 2), (chips, 4, 10)):
        f.pick(product, qty, now - ago * DAY, a1, s1)
    f.ids = {"a1": a1, "b1": b1, "cola": cola, "water": water, "chips": chips}  # type: ignore[attr-defined]
    return f


def rows(envelope: ResultEnvelope, *keys: str) -> list[tuple[Any, ...]]:
    assert envelope.status is ResultStatus.OK, (envelope.status, envelope.message)
    return [tuple(r[k] for k in keys) for r in envelope.rows]


# ---------------------------------------------------------------- one definition everywhere (spec 10)


def test_stock_and_utilization_by_zone_match_the_zone_summary_tool(shop: FixtureSite) -> None:
    dsl = ask(shop, {"measures": ["on_hand", "stock_value", "utilization_pct"], "dimensions": ["zone"]})
    summary = tool(shop, rules.zone_summary)
    expected = {r["zone_code"]: (r["on_hand"], r["stock_value"], r["utilization_pct"]) for r in summary.rows}
    got = {r["zone"]: (r["on_hand"], r["stock_value"], r["utilization_pct"]) for r in dsl.rows}
    assert got == expected


def test_demand_by_product_matches_the_top_movers_tool(shop: FixtureSite) -> None:
    dsl = ask(shop, {"measures": ["demand_units"], "dimensions": ["product"], "time_range": {"last_days": 7}})
    top = tool(shop, rules.top_movers, 7, 50)
    assert rows(dsl, "product", "demand_units") == [(r["sku"], r["demand_units"]) for r in top.rows]
    assert rows(dsl, "product", "demand_units") == [("WATER", 30), ("COLA", 20)]  # CHIPS: 10 days ago


def test_days_of_cover_for_a_category_is_a_ratio_of_sums(shop: FixtureSite) -> None:
    settings = shop.new_settings(trailing_window_operating_days=5)
    with tenant_session(role_pool("PW_AGENT_READ"), shop.tenant, read_snapshot=True) as conn:
        per_product = {
            r[0]: (r[1], r[2])
            for r in conn.execute(
                "SELECT sku, storable_on_hand, avg_daily_demand FROM sem.days_of_cover(%s, %s, %s)",
                (shop.site, shop.as_of(), settings),
            ).fetchall()
        }
    stock = per_product["COLA"][0] + per_product["WATER"][0]
    demand = per_product["COLA"][1] + per_product["WATER"][1]
    expected = (Decimal(stock) / demand).quantize(Decimal("0.1")) if demand else None
    result = ask(
        shop,
        {
            "measures": ["days_of_cover"],
            "dimensions": ["category"],
            "filters": [{"field": "category", "op": "=", "value": "beverages"}],
        },
    )
    assert rows(result, "category", "days_of_cover") == [("Beverages", expected)]
    assert stock == 300  # 100 COLA + 200 WATER in storable bins
    assert "5 operating days" in " ".join(result.evidence.how_computed)


# ---------------------------------------------------------------- combining measures and filters


def test_measures_from_different_sources_join_on_the_dimension(shop: FixtureSite) -> None:
    result = ask(
        shop,
        {
            "measures": ["on_hand", "demand_units"],
            "dimensions": ["product"],
            "time_range": {"last_days": 7},
            "sort": {"by": "product", "direction": "asc"},
        },
    )
    # CHIPS has stock but no demand in 7 days; every product with stock or demand appears.
    assert rows(result, "product", "on_hand", "demand_units") == [
        ("CHIPS", 25, None),
        ("COLA", 100, 20),
        ("WATER", 200, 30),
    ]


def test_dimension_and_measure_filters(shop: FixtureSite) -> None:
    by_value = ask(
        shop,
        {
            "measures": ["stock_value"],
            "dimensions": ["product"],
            "filters": [{"field": "stock_value", "op": ">", "value": 5000}],
        },
    )
    assert rows(by_value, "product", "stock_value") == [("COLA", Decimal(10000)), ("WATER", Decimal(8000))]
    in_b = ask(
        shop,
        {
            "measures": ["on_hand"],
            "dimensions": ["product"],
            "filters": [{"field": "zone", "op": "=", "value": "b"}],
        },
    )
    assert rows(in_b, "product", "on_hand") == [("WATER", 150), ("COLA", 70)]
    between = ask(
        shop,
        {
            "measures": ["on_hand"],
            "dimensions": ["bin"],
            "filters": [{"field": "on_hand", "op": "between", "value": [40, 60]}],
        },
    )
    assert rows(between, "bin", "on_hand") == [("A-01", 50), ("A-02", 50)]
    named = ask(
        shop,
        {"measures": ["on_hand"], "filters": [{"field": "product", "op": "name_contains", "value": "col"}]},
    )
    assert rows(named, "on_hand") == [(100,)]


def test_bin_level_days_since_last_pick(shop: FixtureSite) -> None:
    result = ask(
        shop,
        {
            "measures": ["days_since_last_pick"],
            "dimensions": ["product", "bin"],
            "filters": [{"field": "product", "op": "in", "value": ["COLA"]}],
            "sort": {"by": "bin", "direction": "asc"},
        },
    )
    # Every pick in the fixture comes from A-01; COLA in B-01 has never been picked there.
    assert rows(result, "product", "bin", "days_since_last_pick") == [
        ("COLA", "A-01", 1),
        ("COLA", "B-01", None),
    ]


def test_movements_by_type_over_a_date_range(shop: FixtureSite) -> None:
    today = shop.as_of().date()
    result = ask(
        shop,
        {
            "measures": ["movement_count", "units_moved"],
            "dimensions": ["movement_type"],
            "time_range": {"from": str(today - 30 * DAY), "to": str(today)},
        },
    )
    assert rows(result, "movement_type", "movement_count", "units_moved") == [("PICK", 4, 54)]


# ---------------------------------------------------------------- stage 3: values, never an empty table


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("zone", "Z9"),
        ("bin", "Q-99-99"),
        ("product", "SKU-404"),
        ("category", "Spaceships"),
        ("brand", "Nobody"),
        ("rack", "R404"),
    ],
)
def test_a_value_that_does_not_exist_is_not_found(shop: FixtureSite, field: str, value: str) -> None:
    result = ask(shop, {"measures": ["on_hand"], "filters": [{"field": field, "op": "=", "value": value}]})
    assert result.status is ResultStatus.NOT_FOUND and result.message == f"No such {field}: {value}."


# ---------------------------------------------------------------- injection (spec 7.4 stage 5, FR-COP-03)

HOSTILE = [
    "A'; DROP TABLE pw.bin; --",
    'A" OR 1=1 --',
    "%",
    "_",
    "\\",
    "$$; SELECT pg_sleep(10); $$",
    "sem.stock) UNION SELECT * FROM pw.web_session --",
    "A\u0000",
]


@pytest.mark.parametrize("value", HOSTILE)
def test_hostile_values_stay_values(shop: FixtureSite, value: str) -> None:
    raw = {
        "measures": ["on_hand"],
        "dimensions": ["product"],
        "filters": [{"field": "product", "op": "name_contains", "value": f"{value}xx"}],
    }
    request = validate(raw)
    with tenant_session(role_pool("PW_AGENT_READ"), shop.tenant, read_snapshot=True) as conn:
        from palletwise.semantic.locations import read_moment

        stmt, _ = query.compile_query(request, query.Context(shop.site, read_moment(conn, shop.site), None))
        sql, params = query.to_sql(stmt)
    token = f"{value}xx"
    assert token not in sql
    for word in ("DROP", "UNION", "web_session", "pg_sleep", "1=1"):
        assert word not in sql
    # The text travels only as a bound value, with LIKE wildcards escaped ("/" is the escape).
    escaped = token.replace("/", "//").replace("%", "/%").replace("_", "/_")
    assert any(escaped in str(v) for v in params.values())
    if "\u0000" not in value:
        assert ask(shop, raw).status is ResultStatus.NO_RESULTS  # matched literally: nothing has that name
    zone = {"measures": ["on_hand"], "filters": [{"field": "zone", "op": "=", "value": value[:80] or "x"}]}
    if "\u0000" not in value:
        assert ask(shop, zone).status is ResultStatus.NOT_FOUND


def test_like_wildcards_match_literally(shop: FixtureSite) -> None:
    # "%" would match every product if it reached LIKE unescaped.
    result = ask(
        shop,
        {"measures": ["on_hand"], "filters": [{"field": "product", "op": "name_contains", "value": "%%"}]},
    )
    assert result.status is ResultStatus.NO_RESULTS or rows(result, "on_hand") == [(None,)]


def test_the_statement_only_reads_sem_objects(shop: FixtureSite) -> None:
    with tenant_session(role_pool("PW_AGENT_READ"), shop.tenant, read_snapshot=True) as conn:
        from palletwise.semantic.locations import read_moment

        ctx = query.Context(shop.site, read_moment(conn, shop.site), None)
        for raw in (
            {"measures": ["on_hand", "utilization_pct"], "dimensions": ["zone"]},
            {"measures": ["days_of_cover", "days_since_last_move"], "dimensions": ["product"]},
            {
                "measures": ["demand_units"],
                "dimensions": ["customer", "week"],
                "time_range": {"last_days": 30},
            },
        ):
            sql, _ = query.to_sql(query.compile_query(validate(raw), ctx)[0])
            assert "pw." not in sql and "sem." in sql, sql


# ---------------------------------------------------------------- highlights (spec 7.6) and limits


def test_highlights_follow_the_dimensions(shop: FixtureSite) -> None:
    ids = shop.ids  # type: ignore[attr-defined]
    zones = ask(shop, {"measures": ["on_hand"], "dimensions": ["zone"]})
    assert {h.target_type for h in zones.highlights} == {"zone"} and len(zones.highlights) == 3
    products = ask(
        shop,
        {
            "measures": ["on_hand"],
            "dimensions": ["product"],
            "filters": [{"field": "product", "op": "=", "value": "COLA"}],
        },
    )
    # COLA's storable bins, largest first: B-01 (70) then A-01 (30).
    assert [h.target_id for h in products.highlights] == [ids["b1"], ids["a1"]]
    assert {(h.reason.value, h.severity.value) for h in products.highlights} == {("query", "info")}
    categories = ask(shop, {"measures": ["on_hand"], "dimensions": ["category"]})
    assert categories.highlights == () and categories.entities == ()  # not location based


def test_truncation_and_entities(shop: FixtureSite) -> None:
    result = ask(shop, {"measures": ["on_hand"], "dimensions": ["product"], "limit": 2})
    assert result.truncated and len(result.rows) == 2
    assert [e.label for e in result.entities] == ["WATER", "COLA"]
    assert result.normalized_request["tool"] == "query_inventory"


# ---------------------------------------------------------------- the working set hook (spec 7.5)


def test_the_working_set_narrows_to_the_previous_identifiers(shop: FixtureSite) -> None:
    ids = shop.ids  # type: ignore[attr-defined]
    raw = {
        "measures": ["on_hand"],
        "dimensions": ["product"],
        "scope": {"working_set": True},
        "filters": [{"field": "zone", "op": "=", "value": "B"}],
    }
    result = ask(shop, raw, {"product": [ids["cola"], ids["chips"]]})
    assert rows(result, "product", "on_hand") == [("COLA", 70)]  # CHIPS is not in zone B
    with pytest.raises(QueryRejected, match="no previous result to narrow down"):
        ask(shop, raw, None)
    with pytest.raises(QueryRejected, match="no previous result to narrow down"):
        ask(
            shop,
            {"measures": ["utilization_pct"], "dimensions": ["zone"], "scope": {"working_set": True}},
            {"product": [ids["cola"]]},
        )  # products cannot narrow a zone utilization
