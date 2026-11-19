"""Query language requests (semantic spec 7.1 to 7.3): the schema and the catalogue checks.

Pipeline stages 1 and 2 of semantic spec 7.4, with no database access: the request matches the
schema exactly (unknown fields rejected), and every measure, dimension, filter field and operator
exists in the catalogue and fits together. Values that need the database (does zone B exist?)
are stage 3, in the compiler. Every rejection is a plain reason the agent can act on.
"""

from datetime import date
from typing import Any, Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from palletwise.semantic.model import QueryCatalogue, semantic_model

TEXT_OPS = ("=", "!=", "in")
NUMERIC_OPS = ("=", "!=", "<", "<=", ">", ">=", "between")
TIME_OPS = ("within_last_days", "older_than_days")
PRODUCT_TEXT_OPS = ("name_contains",)
MAX_IN_VALUES = 50


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class Filter(Strict):
    field: str = Field(min_length=1, max_length=40)
    op: str = Field(min_length=1, max_length=20)
    value: Any


class TimeRange(Strict):
    last_days: int | None = Field(default=None, ge=1, le=365)
    from_date: date | None = Field(default=None, alias="from")
    to_date: date | None = Field(default=None, alias="to")

    @model_validator(mode="after")
    def one_form(self) -> Self:
        """Either {"last_days": n} or both "from" and "to" (semantic spec 7.1)."""
        dates = (self.from_date, self.to_date)
        if (self.last_days is None) == (dates == (None, None)) or (None in dates and dates != (None, None)):
            raise ValueError('give either {"last_days": n} or both "from" and "to" dates')
        if self.from_date and self.to_date and self.from_date > self.to_date:
            raise ValueError('"from" must not be after "to"')
        return self


class Sort(Strict):
    by: str = Field(min_length=1, max_length=40)
    direction: Literal["asc", "desc"] = "desc"


class Scope(Strict):
    working_set: bool = False


class QueryRequest(Strict):
    """The request shape of semantic spec 7.1."""

    measures: list[str] = Field(min_length=1, max_length=3)
    dimensions: list[str] = Field(default_factory=lambda: [], max_length=2)
    filters: list[Filter] = Field(default_factory=lambda: [], max_length=5)
    time_range: TimeRange | None = None
    sort: Sort | None = None
    limit: int = Field(default=20, ge=1, le=100)
    scope: Scope = Field(default_factory=Scope)


class QueryRejected(ValueError):
    """A request the catalogue refuses; the message is the structured reason (semantic spec 7.4)."""


def parse(raw: dict[str, Any]) -> QueryRequest:
    """Stage 1: the schema. Unknown fields, wrong types and out-of-range values are refused."""
    try:
        return QueryRequest.model_validate(raw)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(map(str, e['loc'])) or 'request'}: {e['msg']}" for e in exc.errors(include_url=False)
        )
        raise QueryRejected(f"the request does not match the query schema: {problems}") from exc


def _names(items: list[str], kind: str, known: dict[str, Any]) -> None:
    if len(set(items)) != len(items):
        raise QueryRejected(f"{kind}s are repeated: {', '.join(items)}")
    for item in items:
        if item not in known:
            raise QueryRejected(f"unknown {kind} {item}; allowed: {', '.join(sorted(known))}")


def check(request: QueryRequest, catalogue: QueryCatalogue | None = None) -> QueryRequest:
    """Stage 2: the catalogue. Returns the request unchanged when every part fits."""
    cat = catalogue or semantic_model().query
    _names(request.measures, "measure", cat.measures)
    _names(request.dimensions, "dimension", cat.dimensions)

    filter_measures = [f.field for f in request.filters if f.field in cat.measures]
    computed = list(dict.fromkeys([*request.measures, *filter_measures]))
    for name in computed:
        measure = cat.measures[name]
        if measure.available_from:
            raise QueryRejected(
                f"measure {name} is not available yet; it arrives with {measure.available_from}"
            )
        for dimension in request.dimensions:
            if dimension not in measure.dimensions:
                raise QueryRejected(
                    f"measure {name} cannot be grouped by {dimension}; "
                    f"allowed: {', '.join(measure.dimensions)}"
                )
        for required in measure.requires:
            if required not in request.dimensions:
                raise QueryRejected(f"measure {name} needs the {required} dimension")

    for f in request.filters:
        if f.field in cat.measures:
            if f.op not in NUMERIC_OPS:
                raise QueryRejected(
                    f"operator {f.op} does not apply to measure {f.field}; "
                    f"use one of {', '.join(NUMERIC_OPS)}"
                )
            _numeric(f)
            continue
        if f.field not in cat.dimensions:
            raise QueryRejected(f"unknown filter field {f.field}; filter by a dimension or a measure")
        for name in computed:
            if f.field not in cat.measures[name].dimensions:
                raise QueryRejected(
                    f"measure {name} cannot be filtered by {f.field}; "
                    f"allowed: {', '.join(cat.measures[name].dimensions)}"
                )
        _dimension_filter(f, cat)

    flows = [m for m in computed if cat.measures[m].measure_class == "flow"]
    if flows and request.time_range is None:
        raise QueryRejected(f"measure {flows[0]} counts events over time, so a time_range is required")
    if not flows and request.time_range is not None:
        raise QueryRejected(
            "time_range applies only to flow measures (demand_units, units_moved, movement_count)"
        )

    if request.sort is not None and request.sort.by not in (*request.measures, *request.dimensions):
        raise QueryRejected(f"sort by a requested measure or dimension, not {request.sort.by}")
    return request


def _items(value: Any) -> list[object] | None:
    """A JSON list as a list of objects, or None when the value is not a list."""
    return list(cast("list[object]", value)) if isinstance(value, list) else None


def _numeric(f: Filter) -> None:
    def number(v: Any) -> bool:
        return isinstance(v, (int, float)) and not isinstance(v, bool)

    if f.op == "between":
        pair = _items(f.value)
        if pair is None or len(pair) != 2 or not all(number(v) for v in pair):
            raise QueryRejected(f"between on {f.field} takes two numbers, [low, high]")
        low, high = cast("tuple[float, float]", tuple(pair))
        if low > high:
            raise QueryRejected(f"between on {f.field}: the low value is above the high value")
    elif not number(f.value):
        raise QueryRejected(f"{f.field} {f.op} takes a number")


def _dimension_filter(f: Filter, cat: QueryCatalogue) -> None:
    dimension = cat.dimensions[f.field]
    if dimension.type == "time":
        if f.op not in TIME_OPS:
            raise QueryRejected(f"time dimension {f.field} takes {', '.join(TIME_OPS)}")
        if not (isinstance(f.value, int) and not isinstance(f.value, bool) and 1 <= f.value <= 365):
            raise QueryRejected(f"{f.field} {f.op} takes a whole number of days from 1 to 365")
        return
    allowed = (*TEXT_OPS, *PRODUCT_TEXT_OPS) if dimension.type == "product" else TEXT_OPS
    if f.op not in allowed:
        raise QueryRejected(f"operator {f.op} does not apply to {f.field}; use one of {', '.join(allowed)}")
    if f.op == "name_contains":
        if not (isinstance(f.value, str) and 2 <= len(f.value.strip()) <= 80):
            raise QueryRejected("name_contains takes text of 2 to 80 characters")
        return
    values = _items(f.value) if f.op == "in" else [cast("object", f.value)]
    if values is None or not 1 <= len(values) <= MAX_IN_VALUES:
        raise QueryRejected(f"in on {f.field} takes a list of 1 to {MAX_IN_VALUES} values")
    texts = [v for v in values if isinstance(v, str) and 1 <= len(v.strip()) <= 80]
    if len(texts) != len(values):
        raise QueryRejected(f"{f.field} values are text of 1 to 80 characters")
    if dimension.values is not None:
        unknown = [v for v in texts if v.upper() not in dimension.values]
        if unknown:
            raise QueryRejected(f"no such {f.field}: {unknown[0]}; allowed: {', '.join(dimension.values)}")


def validate(raw: dict[str, Any]) -> QueryRequest:
    """Stages 1 and 2 together: the schema, then the catalogue."""
    return check(parse(raw))
