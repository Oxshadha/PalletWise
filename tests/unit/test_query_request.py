"""Query language stages 1 and 2: schema and catalogue (semantic spec 7.1 to 7.4)."""

from typing import Any

import pytest

from palletwise.semantic.query_request import QueryRejected, validate

SPEC_EXAMPLE = {
    "measures": ["on_hand", "stock_value"],
    "dimensions": ["zone"],
    "filters": [
        {"field": "category", "op": "in", "value": ["Beverages"]},
        {"field": "days_since_last_pick", "op": ">", "value": 30},
    ],
    "sort": {"by": "stock_value", "direction": "desc"},
    "limit": 10,
    "scope": {"working_set": False},
}


def rejected(raw: dict[str, Any]) -> str:
    with pytest.raises(QueryRejected) as exc:
        validate(raw)
    return str(exc.value)


def test_the_spec_example_shape_is_accepted_up_to_its_measure_filter() -> None:
    # The spec example filters by days_since_last_pick while grouping by zone; that measure is
    # defined by product (or product and bin), so the catalogue refuses the combination.
    assert "days_since_last_pick cannot be grouped by zone" in rejected(SPEC_EXAMPLE)
    valid = {**SPEC_EXAMPLE, "filters": SPEC_EXAMPLE["filters"][:1]}
    request = validate(valid)
    assert request.measures == ["on_hand", "stock_value"] and request.limit == 10


def test_the_spec_rejection_message_names_the_allowed_dimensions() -> None:
    assert rejected({"measures": ["days_of_cover"], "dimensions": ["zone"]}) == (
        "measure days_of_cover cannot be grouped by zone; allowed: product, category, brand"
    )


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ({"measures": ["on_hand"], "sql": "DROP TABLE"}, "does not match the query schema"),
        ({"measures": []}, "does not match the query schema"),
        (
            {"measures": ["on_hand", "allocated", "available", "stock_value"]},
            "does not match the query schema",
        ),
        ({"measures": ["on_hand"], "dimensions": ["zone", "rack", "bin"]}, "does not match the query schema"),
        ({"measures": ["on_hand"], "limit": 101}, "does not match the query schema"),
        ({"measures": ["on_hand"], "limit": 0}, "does not match the query schema"),
        (
            {"measures": ["on_hand"], "filters": [{"field": "zone", "op": "=", "value": "A"}] * 6},
            "query schema",
        ),
        ({"measures": ["revenue"]}, "unknown measure revenue"),
        ({"measures": ["on_hand"], "dimensions": ["supplier"]}, "unknown dimension supplier"),
        ({"measures": ["on_hand", "on_hand"]}, "measures are repeated"),
        (
            {"measures": ["projected_days_of_cover"]},
            "not available yet; it arrives with the festival projection",
        ),
        ({"measures": ["days_since_last_pick"], "dimensions": ["bin"]}, "needs the product dimension"),
        ({"measures": ["demand_units"]}, "a time_range is required"),
        ({"measures": ["on_hand"], "time_range": {"last_days": 7}}, "applies only to flow measures"),
        (
            {
                "measures": ["demand_units"],
                "time_range": {"last_days": 7, "from": "2026-03-01", "to": "2026-03-07"},
            },
            "does not match the query schema",
        ),
        (
            {"measures": ["demand_units"], "time_range": {"from": "2026-03-08", "to": "2026-03-01"}},
            "query schema",
        ),
        (
            {"measures": ["on_hand"], "sort": {"by": "stock_value"}},
            "sort by a requested measure or dimension",
        ),
        (
            {"measures": ["on_hand"], "filters": [{"field": "price", "op": ">", "value": 1}]},
            "unknown filter field",
        ),
        (
            {"measures": ["on_hand"], "filters": [{"field": "on_hand", "op": "in", "value": [1]}]},
            "does not apply",
        ),
        (
            {"measures": ["on_hand"], "filters": [{"field": "on_hand", "op": ">", "value": "10"}]},
            "takes a number",
        ),
        (
            {"measures": ["on_hand"], "filters": [{"field": "on_hand", "op": ">", "value": True}]},
            "takes a number",
        ),
        (
            {"measures": ["on_hand"], "filters": [{"field": "on_hand", "op": "between", "value": [9, 1]}]},
            "low value",
        ),
        (
            {"measures": ["on_hand"], "filters": [{"field": "zone", "op": ">", "value": "A"}]},
            "does not apply to zone",
        ),
        (
            {"measures": ["on_hand"], "filters": [{"field": "zone", "op": "name_contains", "value": "A"}]},
            "does not apply to zone",
        ),
        (
            {"measures": ["on_hand"], "filters": [{"field": "product", "op": "name_contains", "value": "x"}]},
            "2 to 80 characters",
        ),
        (
            {"measures": ["on_hand"], "filters": [{"field": "zone", "op": "in", "value": ["A"] * 51}]},
            "1 to 50",
        ),
        ({"measures": ["on_hand"], "filters": [{"field": "zone", "op": "=", "value": 7}]}, "text of 1 to 80"),
        (
            {"measures": ["on_hand"], "filters": [{"field": "bin_role", "op": "=", "value": "SHELF"}]},
            "no such bin_role: SHELF",
        ),
        (
            {
                "measures": ["demand_units"],
                "time_range": {"last_days": 7},
                "filters": [{"field": "zone", "op": "=", "value": "A"}],
            },
            "demand_units cannot be filtered by zone",
        ),
        (
            {
                "measures": ["demand_units"],
                "time_range": {"last_days": 7},
                "filters": [{"field": "day", "op": "=", "value": "x"}],
            },
            "time dimension day takes",
        ),
        (
            {
                "measures": ["utilization_pct"],
                "dimensions": ["zone"],
                "filters": [{"field": "days_of_cover", "op": "<", "value": 3}],
            },
            "days_of_cover cannot be grouped by zone",
        ),
    ],
)
def test_requests_outside_the_catalogue_are_refused_with_a_reason(raw: dict[str, Any], reason: str) -> None:
    assert reason in rejected(raw)


@pytest.mark.parametrize(
    "raw",
    [
        {
            "measures": ["days_of_cover"],
            "dimensions": ["category"],
            "filters": [{"field": "category", "op": "=", "value": "Beverages"}],
        },
        {
            "measures": ["demand_units"],
            "dimensions": ["product"],
            "time_range": {"last_days": 30},
            "sort": {"by": "demand_units"},
            "limit": 5,
        },
        {
            "measures": ["units_moved", "movement_count"],
            "dimensions": ["movement_type", "week"],
            "time_range": {"from": "2026-03-01", "to": "2026-03-31"},
        },
        {
            "measures": ["days_since_last_pick"],
            "dimensions": ["product", "bin"],
            "filters": [{"field": "days_since_last_pick", "op": ">", "value": 30}],
        },
        {
            "measures": ["on_hand"],
            "dimensions": ["product"],
            "filters": [
                {"field": "product", "op": "name_contains", "value": "mouse"},
                {"field": "bin_role", "op": "in", "value": ["pick", "RESERVE"]},
                {"field": "stock_value", "op": "between", "value": [1000, 50000]},
            ],
        },
        {"measures": ["on_hand"], "scope": {"working_set": True}},
        {
            "measures": ["demand_units"],
            "dimensions": ["day"],
            "time_range": {"last_days": 14},
            "filters": [{"field": "day", "op": "within_last_days", "value": 7}],
        },
    ],
)
def test_requests_inside_the_catalogue_are_accepted(raw: dict[str, Any]) -> None:
    assert validate(raw).measures == raw["measures"]
