"""The query language compiler and executor (semantic spec 7.4 stages 3 to 7; Slice 2 step 3c).

Model output never becomes SQL. A validated request (query_request.py) names measures, dimensions
and filters; each name is looked up here in a fixed table of reviewed expressions over sem
objects, and every value is a bound parameter. The statement is built with SQLAlchemy Core and
compiled for the driver only at the end (engineering baseline 4.1).

Measures belong to families, one per semantic source. Each family groups its rows by the
requested dimensions; families are joined on the dimension keys. Dimension filters apply to rows
before grouping, measure filters to the joined result.
"""

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import (
    ColumnElement,
    Date,
    FromClause,
    Numeric,
    Select,
    String,
    Subquery,
    TableClause,
    and_,
    bindparam,
    case,
    cast,
    column,
    func,
    literal,
    null,
    or_,
    select,
    table,
    true,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.elements import ColumnClause, Label

from palletwise.contracts.results import (
    DEFAULT_SEVERITY,
    MAX_ENTITIES,
    MAX_HIGHLIGHTS,
    Column,
    EntityRef,
    Highlight,
    HighlightReason,
    ResultEnvelope,
    ResultStatus,
)
from palletwise.db.connection import Connection, query
from palletwise.semantic.locations import Moment, read_moment
from palletwise.semantic.model import semantic_model
from palletwise.semantic.query_request import Filter, QueryRejected, QueryRequest

TOOL = "query_inventory"
SCOPE = "inventory.read"  # every catalogue measure is inventory data (spec 7.4 stage 4)
DIALECT = postgresql.psycopg.dialect()  # type: ignore[no-untyped-call]

# ---------------------------------------------------------------- sem objects (names only)


def _sem(name: str, *columns: str) -> TableClause:
    """A sem object by name, with only the columns the compiler reads."""
    clauses: list[ColumnClause[Any]] = [column(c) for c in columns]
    return table(name, *clauses, schema="sem")


_stock = _sem("stock", "site_id", "bin_id", "product_id", "on_hand", "allocated", "available")
_location = _sem(
    "location", "bin_id", "site_id", "bin_code", "bin_role", "zone_id", "zone_code", "rack_id", "rack_code"
)
_product = _sem(
    "product", "product_id", "sku", "name", "brand", "category_name", "category_code", "unit_value"
)
_movement = _sem("movement", "site_id", "movement_type", "product_id", "qty", "occurred_at")
_demand = _sem("demand_event", "site_id", "product_id", "occurred_at", "qty", "order_line_id")
_order_line = _sem("order_line", "order_line_id", "customer_code")


@dataclass(frozen=True)
class Context:
    """One request's fixed inputs: site, moment and settings version (semantic spec 2)."""

    site: UUID
    moment: Moment
    working_set: dict[str, list[UUID]] | None

    @property
    def as_of(self) -> datetime:
        return self.moment.as_of

    @property
    def zone(self) -> str:
        return self.moment.time_zone

    def p(self, value: Any) -> ColumnElement[Any]:
        return bindparam(None, value, unique=True)


@dataclass(frozen=True)
class Dim:
    label: ColumnElement[Any]
    ident: ColumnElement[Any] | None = None  # the typed id of product, zone, rack and bin
    kind: str = "text"  # column type: text or datetime (time buckets)


@dataclass
class Source:
    """One family's rows: FROM, base conditions, dimensions and measure aggregates."""

    from_: FromClause
    where: list[ColumnElement[bool]]
    dims: dict[str, Dim]
    measures: dict[str, ColumnElement[Any]]
    streams: tuple[str, ...]
    time: ColumnElement[Any] | None = None  # the event time of a flow family
    ids: dict[str, ColumnElement[Any]] = field(default_factory=lambda: {})  # working-set columns


def _local(ctx: Context, ts: ColumnElement[Any]) -> ColumnElement[Any]:
    return func.timezone(ctx.p(ctx.zone), ts)


def _time_dims(ctx: Context, ts: ColumnElement[Any]) -> dict[str, Dim]:
    local = _local(ctx, ts)
    return {
        "day": Dim(cast(local, Date), kind="date"),
        "week": Dim(cast(func.date_trunc("week", local), Date), kind="date"),
        "month": Dim(cast(func.date_trunc("month", local), Date), kind="date"),
    }


def _product_dims() -> dict[str, Dim]:
    return {
        "product": Dim(_product.c.sku, _product.c.product_id),
        "category": Dim(_product.c.category_name),
        "brand": Dim(_product.c.brand),
    }


def _location_dims(loc: Any) -> dict[str, Dim]:
    return {
        "zone": Dim(loc.c.zone_code, loc.c.zone_id),
        "rack": Dim(loc.c.rack_code, loc.c.rack_id),
        "bin": Dim(loc.c.bin_code, loc.c.bin_id),
        "bin_role": Dim(loc.c.bin_role),
    }


# ---------------------------------------------------------------- families (spec 4 definitions)


def _stock_source(ctx: Context) -> Source:
    s, loc, p = _stock, _location, _product
    return Source(
        from_=s.join(loc, loc.c.bin_id == s.c.bin_id).join(p, p.c.product_id == s.c.product_id),
        where=[s.c.site_id == ctx.p(ctx.site)],
        dims={**_product_dims(), **_location_dims(loc)},
        measures={
            "on_hand": func.sum(s.c.on_hand),
            "allocated": func.sum(s.c.allocated),
            "available": func.sum(s.c.available),
            "stock_value": func.sum(s.c.on_hand * p.c.unit_value),
        },
        streams=("MASTER", "MOVEMENTS", "ALLOCATIONS"),
        ids={"product": s.c.product_id, "bin": s.c.bin_id, "zone": loc.c.zone_id},
    )


def _utilization_source(ctx: Context) -> Source:
    args = (ctx.p(ctx.site), ctx.p(ctx.as_of), ctx.p(ctx.moment.settings_version_id))
    u = (
        func.sem.bin_utilization(*args)
        .table_valued("bin_id", "volume_used", "weight_used", "capacity_volume_m3", "capacity_weight_kg")
        .alias("u")
    )
    loc = _location.alias("ul")
    pct = func.round(
        100
        * func.greatest(
            func.sum(u.c.volume_used) / func.sum(u.c.capacity_volume_m3),
            func.sum(u.c.weight_used) / func.sum(u.c.capacity_weight_kg),
        ),
        1,
    )
    dims = _location_dims(loc)
    return Source(
        from_=u.join(loc, loc.c.bin_id == u.c.bin_id),
        where=[],
        dims={k: dims[k] for k in ("zone", "rack", "bin")},
        measures={"utilization_pct": pct},
        streams=("MASTER", "MOVEMENTS", "ALLOCATIONS"),
        ids={"bin": loc.c.bin_id, "zone": loc.c.zone_id},
    )


def _cover_source(ctx: Context) -> Source:
    args = (ctx.p(ctx.site), ctx.p(ctx.as_of), ctx.p(ctx.moment.settings_version_id))
    c = (
        func.sem.days_of_cover(*args)
        .table_valued("product_id", "storable_on_hand", "avg_daily_demand")
        .alias("c")
    )
    p = _product
    # Ratio of sums (spec 7.2): total stock over total demand, never an average of products.
    cover = case(
        (
            func.sum(c.c.avg_daily_demand) > 0,
            func.round(cast(func.sum(c.c.storable_on_hand), Numeric) / func.sum(c.c.avg_daily_demand), 1),
        ),
        else_=null(),
    )
    return Source(
        from_=c.join(p, p.c.product_id == c.c.product_id),
        where=[],
        dims=_product_dims(),
        measures={"days_of_cover": cover},
        streams=("MASTER", "MOVEMENTS", "ALLOCATIONS", "ORDERS"),
        ids={"product": c.c.product_id},
    )


def _held(ctx: Context) -> Any:
    """Product and bin pairs holding stock now: the subjects of days-since measures."""
    s = _stock.alias("hs")
    return (
        select(s.c.product_id, s.c.bin_id)
        .where(s.c.site_id == ctx.p(ctx.site), s.c.on_hand > 0)
        .subquery("held")
    )


def _last_pick_source(ctx: Context, by_bin: bool) -> Source:
    held, p, loc = _held(ctx), _product, _location.alias("pl")
    if by_bin:
        lp = (
            func.sem.last_pick_by_bin(ctx.p(ctx.site), ctx.p(ctx.as_of))
            .table_valued("product_id", "bin_id", "days_since_last_pick")
            .alias("lpb")
        )
        joined = (
            held.join(p, p.c.product_id == held.c.product_id)
            .join(loc, loc.c.bin_id == held.c.bin_id)
            .outerjoin(lp, and_(lp.c.product_id == held.c.product_id, lp.c.bin_id == held.c.bin_id))
        )
        dims = {"product": _product_dims()["product"], "bin": _location_dims(loc)["bin"]}
        ids: dict[str, ColumnElement[Any]] = {"product": held.c.product_id, "bin": held.c.bin_id}
    else:
        products = select(held.c.product_id).distinct().subquery("hp")
        lp = (
            func.sem.last_pick(ctx.p(ctx.site), ctx.p(ctx.as_of))
            .table_valued("product_id", "days_since_last_pick")
            .alias("lp")
        )
        joined = products.join(p, p.c.product_id == products.c.product_id).outerjoin(
            lp, lp.c.product_id == products.c.product_id
        )
        dims = {"product": _product_dims()["product"]}
        ids = {"product": products.c.product_id}
    return Source(
        from_=joined,
        where=[],
        dims=dims,
        measures={"days_since_last_pick": func.min(lp.c.days_since_last_pick)},
        streams=("MASTER", "MOVEMENTS", "ORDERS"),
        ids=ids,
    )


def _last_move_source(ctx: Context) -> Source:
    held, p = _held(ctx), _product
    products = select(held.c.product_id).distinct().subquery("mp")
    lm = (
        func.sem.last_move(ctx.p(ctx.site), ctx.p(ctx.as_of))
        .table_valued("product_id", "days_since_last_move")
        .alias("lm")
    )
    joined = products.join(p, p.c.product_id == products.c.product_id).outerjoin(
        lm, lm.c.product_id == products.c.product_id
    )
    return Source(
        from_=joined,
        where=[],
        dims={"product": _product_dims()["product"]},
        measures={"days_since_last_move": func.min(lm.c.days_since_last_move)},
        streams=("MASTER", "MOVEMENTS"),
        ids={"product": products.c.product_id},
    )


def _demand_source(ctx: Context) -> Source:
    d, p, ol = _demand, _product, _order_line
    return Source(
        from_=d.join(p, p.c.product_id == d.c.product_id).outerjoin(
            ol, ol.c.order_line_id == d.c.order_line_id
        ),
        where=[d.c.site_id == ctx.p(ctx.site)],
        dims={**_product_dims(), "customer": Dim(ol.c.customer_code), **_time_dims(ctx, d.c.occurred_at)},
        measures={"demand_units": func.sum(d.c.qty)},
        streams=("MASTER", "MOVEMENTS", "ORDERS"),
        time=d.c.occurred_at,
        ids={"product": d.c.product_id},
    )


def _movement_source(ctx: Context) -> Source:
    m, p = _movement, _product
    return Source(
        from_=m.join(p, p.c.product_id == m.c.product_id),
        where=[m.c.site_id == ctx.p(ctx.site)],
        dims={**_product_dims(), "movement_type": Dim(m.c.movement_type), **_time_dims(ctx, m.c.occurred_at)},
        measures={"units_moved": func.sum(m.c.qty), "movement_count": func.count()},
        streams=("MASTER", "MOVEMENTS"),
        time=m.c.occurred_at,
        ids={"product": m.c.product_id},
    )


FAMILIES: dict[str, Callable[[Context, QueryRequest], Source]] = {
    "on_hand": lambda c, r: _stock_source(c),
    "allocated": lambda c, r: _stock_source(c),
    "available": lambda c, r: _stock_source(c),
    "stock_value": lambda c, r: _stock_source(c),
    "utilization_pct": lambda c, r: _utilization_source(c),
    "days_of_cover": lambda c, r: _cover_source(c),
    "days_since_last_pick": lambda c, r: _last_pick_source(c, "bin" in r.dimensions),
    "days_since_last_move": lambda c, r: _last_move_source(c),
    "demand_units": lambda c, r: _demand_source(c),
    "units_moved": lambda c, r: _movement_source(c),
    "movement_count": lambda c, r: _movement_source(c),
}
FAMILY_OF = {
    "on_hand": "stock",
    "allocated": "stock",
    "available": "stock",
    "stock_value": "stock",
    "utilization_pct": "utilization",
    "days_of_cover": "cover",
    "days_since_last_pick": "last_pick",
    "days_since_last_move": "last_move",
    "demand_units": "demand",
    "units_moved": "movement",
    "movement_count": "movement",
}
INTEGER_MEASURES = {
    "on_hand",
    "allocated",
    "available",
    "days_since_last_pick",
    "days_since_last_move",
    "demand_units",
    "units_moved",
    "movement_count",
}

# ---------------------------------------------------------------- filters


def _text_condition(expr: ColumnElement[Any], f: Filter, ctx: Context) -> ColumnElement[bool]:
    lowered = func.lower(expr)
    if f.op == "=":
        return lowered == func.lower(ctx.p(f.value))
    if f.op == "!=":
        return lowered.is_distinct_from(func.lower(ctx.p(f.value)))
    values = [str(v).lower() for v in f.value]
    return lowered.in_([ctx.p(v) for v in values])


def _dimension_condition(source: Source, f: Filter, ctx: Context) -> ColumnElement[bool]:
    if f.field in ("day", "week", "month"):
        bucket = source.dims[f.field].label
        today = ctx.as_of.astimezone(ZoneInfo(ctx.zone)).date()
        edge = ctx.p(today - timedelta(days=int(f.value)))
        return bucket >= edge if f.op == "within_last_days" else bucket < edge
    if f.field == "product" and f.op == "name_contains":
        words = str(f.value).strip()
        return _product.c.name.icontains(words, autoescape=True) | _product.c.sku.icontains(
            words, autoescape=True
        )
    if f.field == "category":  # a category is named by its name or by its code
        by_name = _text_condition(_product.c.category_name, f, ctx)
        by_code = _text_condition(_product.c.category_code, f, ctx)
        return and_(by_name, by_code) if f.op == "!=" else or_(by_name, by_code)
    return _text_condition(source.dims[f.field].label, f, ctx)


def _measure_condition(expr: ColumnElement[Any], f: Filter, ctx: Context) -> ColumnElement[bool]:
    if f.op == "between":
        low, high = f.value
        return expr.between(ctx.p(low), ctx.p(high))
    value = ctx.p(f.value)
    return {
        "=": expr == value,
        "!=": expr != value,
        "<": expr < value,
        "<=": expr <= value,
        ">": expr > value,
        ">=": expr >= value,
    }[f.op]


def _time_range(source: Source, request: QueryRequest, ctx: Context) -> list[ColumnElement[bool]]:
    if source.time is None or request.time_range is None:
        return []
    tr = request.time_range
    if tr.last_days is not None:
        start = ctx.as_of - timedelta(days=tr.last_days)
        return [source.time > ctx.p(start), source.time <= ctx.p(ctx.as_of)]
    local_day = cast(_local(ctx, source.time), Date)
    return [local_day >= ctx.p(tr.from_date), local_day <= ctx.p(tr.to_date), source.time <= ctx.p(ctx.as_of)]


# ---------------------------------------------------------------- stage 3: values


# Dimension -> statement that finds one value, with the site bound where the dimension is per site.
LOOKUPS: dict[str, tuple[str, bool]] = {
    "product": ("SELECT 1 FROM sem.product WHERE lower(sku) = lower(%(v)s)", False),
    "category": (
        "SELECT 1 FROM sem.product WHERE lower(category_name) = lower(%(v)s)"
        " OR lower(category_code) = lower(%(v)s)",
        False,
    ),
    "brand": ("SELECT 1 FROM sem.product WHERE lower(brand) = lower(%(v)s)", False),
    "zone": ("SELECT 1 FROM sem.zone WHERE site_id = %(site)s AND lower(zone_code) = lower(%(v)s)", True),
    "rack": ("SELECT 1 FROM sem.rack WHERE site_id = %(site)s AND lower(rack_code) = lower(%(v)s)", True),
    "bin": ("SELECT 1 FROM sem.location WHERE site_id = %(site)s AND lower(bin_code) = lower(%(v)s)", True),
    "customer": (
        "SELECT 1 FROM sem.order_line WHERE site_id = %(site)s AND lower(customer_code) = lower(%(v)s)",
        True,
    ),
}


class NotFound(Exception):
    def __init__(self, dimension: str, value: str) -> None:
        super().__init__(f"No such {dimension}: {value}.")


def check_values(conn: Connection, site: UUID, request: QueryRequest) -> None:
    """Stage 3: every named dimension value exists, so a typo is never an empty table."""
    for f in request.filters:
        if f.field not in LOOKUPS or f.op == "name_contains":
            continue
        sql, per_site = LOOKUPS[f.field]
        for value in f.value if f.op == "in" else [f.value]:
            args = {"v": value, "site": site} if per_site else {"v": value}
            if query(conn, f"query_lookup_{f.field}", sql + " LIMIT 1", args).fetchone() is None:
                raise NotFound(f.field, str(value))


# ---------------------------------------------------------------- stages 4 to 6: compile


def compile_query(request: QueryRequest, ctx: Context) -> tuple[Select[Any], dict[str, Any]]:
    """The statement and a description of its output columns. Names resolve only through FAMILIES."""
    computed = list(
        dict.fromkeys([*request.measures, *(f.field for f in request.filters if f.field in FAMILY_OF)])
    )
    by_family: dict[str, list[str]] = {}
    for measure in computed:
        by_family.setdefault(FAMILY_OF[measure], []).append(measure)

    subqueries: list[Subquery] = []
    streams: list[str] = []
    for family, measures in by_family.items():
        source = FAMILIES[measures[0]](ctx, request)
        streams += source.streams
        columns: list[Any] = []
        group: list[Any] = []
        for d in request.dimensions:
            dim = source.dims[d]
            columns.append(dim.label.label(f"l_{d}"))
            group.append(dim.label)
            if dim.ident is not None:
                columns.append(dim.ident.label(f"i_{d}"))
                group.append(dim.ident)
        columns += [source.measures[m].label(f"m_{m}") for m in measures]
        conditions = [*source.where, *_time_range(source, request, ctx)]
        conditions += [
            _dimension_condition(source, f, ctx) for f in request.filters if f.field not in FAMILY_OF
        ]
        conditions += _working_set(source, request, ctx)
        stmt = select(*columns).select_from(source.from_).where(and_(true(), *conditions))
        if group:
            stmt = stmt.group_by(*group)
        subqueries.append(stmt.subquery(f"f_{family}"))

    first = subqueries[0]
    joined: FromClause = first
    for sub in subqueries[1:]:
        keys = [_key(first, d) == _key(sub, d) for d in request.dimensions]
        joined = joined.join(sub, and_(*keys), full=True) if keys else joined.join(sub, true())

    out: list[ColumnElement[Any]] = []
    for d in request.dimensions:
        out.append(func.coalesce(*(s.c[f"l_{d}"] for s in subqueries)).label(d))
        if f"i_{d}" in first.c:
            out.append(func.coalesce(*(s.c[f"i_{d}"] for s in subqueries)).label(f"_id_{d}"))
    measure_col: dict[str, ColumnElement[Any]] = {
        m: next(s.c[f"m_{m}"] for s in subqueries if f"m_{m}" in s.c) for m in computed
    }
    out += [measure_col[m].label(m) for m in request.measures]

    stmt = select(*out).select_from(joined)
    filters = [
        _measure_condition(measure_col[f.field], f, ctx) for f in request.filters if f.field in FAMILY_OF
    ]
    if filters:
        stmt = stmt.where(and_(*filters))
    sort_by = request.sort.by if request.sort else request.measures[0]
    direction = request.sort.direction if request.sort else "desc"
    named = {c.name: c for c in out if isinstance(c, Label)}
    primary: ColumnElement[Any] = measure_col[sort_by] if sort_by in measure_col else named[sort_by]
    order = [primary.desc().nulls_last() if direction == "desc" else primary.asc().nulls_last()]
    order += [named[d].asc().nulls_last() for d in request.dimensions]
    stmt = stmt.order_by(*order).limit(request.limit + 1)
    return stmt, {"streams": tuple(dict.fromkeys(streams))}


def _key(sub: Any, d: str) -> Any:
    """The join key of a dimension: its id when it has one, else its label, as text with a missing
    value as empty text, so equal keys (missing included) match in a hash join (FULL JOIN needs it)."""
    value = sub.c[f"i_{d}"] if f"i_{d}" in sub.c else sub.c[f"l_{d}"]
    return func.coalesce(cast(value, String), literal(""))


def _working_set(source: Source, request: QueryRequest, ctx: Context) -> list[ColumnElement[bool]]:
    """scope.working_set restricts every family to the previous result's identifiers (7.5)."""
    if not request.scope.working_set:
        return []
    usable = [t for t in ("product", "bin", "zone") if t in source.ids and (ctx.working_set or {}).get(t)]
    if not usable:
        raise QueryRejected("there is no previous result to narrow down")
    kind = usable[0]
    return [source.ids[kind].in_([ctx.p(i) for i in (ctx.working_set or {})[kind]])]


def to_sql(stmt: Select[Any]) -> tuple[str, dict[str, Any]]:
    """The driver statement and its bound values; request text appears only among the values."""
    compiled = stmt.compile(dialect=DIALECT)
    return str(compiled), dict(compiled.params)


# ---------------------------------------------------------------- stage 7: the envelope


def run(
    conn: Connection, site: UUID, request: QueryRequest, working_set: dict[str, list[UUID]] | None = None
) -> ResultEnvelope:
    moment = read_moment(conn, site)
    ctx = Context(site, moment, working_set)
    row = query(
        conn,
        "setting",
        "SELECT sem.setting(%s, 'trailing_window_operating_days')",
        (moment.settings_version_id,),
    ).fetchone()
    window = f"{row[0]:g}" if row else ""
    normalized = {"tool": TOOL, **request.model_dump(mode="json", by_alias=True, exclude_defaults=False)}
    try:
        check_values(conn, site, request)
        stmt, meta = compile_query(request, ctx)
    except NotFound as exc:
        return _envelope(
            moment, request, normalized, ("MASTER",), window, status=ResultStatus.NOT_FOUND, message=str(exc)
        )
    streams: tuple[str, ...] = meta["streams"]
    if moment.missing_streams(streams):
        return _envelope(
            moment,
            request,
            normalized,
            streams,
            window,
            status=ResultStatus.UNAVAILABLE,
            message="This data has not been loaded yet.",
        )
    sql, params = to_sql(stmt)
    cursor = query(conn, TOOL, sql, params)
    names = [c.name for c in cursor.description or ()]
    fetched = [dict(zip(names, r, strict=True)) for r in cursor.fetchall()]
    truncated = len(fetched) > request.limit
    rows = fetched[: request.limit]
    if not rows:
        return _envelope(
            moment,
            request,
            normalized,
            streams,
            window,
            status=ResultStatus.NO_RESULTS,
            message="No rows match this query.",
        )
    highlights, highlights_truncated, entities = _locations(conn, site, request, rows)
    visible = [*request.dimensions, *request.measures]
    return _envelope(
        moment,
        request,
        normalized,
        streams,
        window,
        status=ResultStatus.OK,
        columns=_columns(request),
        rows=tuple({k: r[k] for k in visible} for r in rows),
        truncated=truncated,
        highlights=highlights,
        highlights_truncated=highlights_truncated,
        entities=entities,
    )


def _columns(request: QueryRequest) -> tuple[Column, ...]:
    model = semantic_model()
    cols = [Column(key=d, label=model.query.dimensions[d].label, type="text") for d in request.dimensions]
    for m in request.measures:
        measure = model.measures[m]
        cols.append(
            Column(
                key=m,
                label=measure.label,
                type="integer" if m in INTEGER_MEASURES else "number",
                unit=measure.unit,
            )
        )
    return tuple(cols)


def _locations(
    conn: Connection, site: UUID, request: QueryRequest, rows: list[dict[str, Any]]
) -> tuple[tuple[Highlight, ...], bool, tuple[EntityRef, ...]]:
    """Highlights by semantic spec 7.6 and the typed identifiers for the working set."""
    from palletwise.semantic.rules import product_bins

    reason = HighlightReason.QUERY
    severity = DEFAULT_SEVERITY[reason]
    entities: list[EntityRef] = []
    for kind in ("product", "bin", "zone"):
        if kind in request.dimensions:
            entities += [
                EntityRef(type=kind, id=r[f"_id_{kind}"], label=str(r[kind]))  # type: ignore[arg-type]
                for r in rows
                if r.get(f"_id_{kind}") is not None
            ]
    located = [d for d in ("bin", "rack", "zone") if d in request.dimensions]
    if located:
        d = located[0]
        targets = list(dict.fromkeys(r[f"_id_{d}"] for r in rows if r.get(f"_id_{d}") is not None))
        found = tuple(
            Highlight(target_type=d, target_id=t, reason=reason, severity=severity)  # type: ignore[arg-type]
            for t in targets[:MAX_HIGHLIGHTS]
        )
        return found, len(targets) > MAX_HIGHLIGHTS, _distinct(entities)
    if "product" in request.dimensions:
        products = [r["_id_product"] for r in rows if r.get("_id_product") is not None]
        highlights, more = product_bins(conn, site, products, reason, severity)
        return highlights, more, _distinct(entities)
    return (), False, _distinct(entities)


def _distinct(entities: Iterable[EntityRef]) -> tuple[EntityRef, ...]:
    seen: dict[tuple[str, UUID], EntityRef] = {}
    for e in entities:
        seen.setdefault((e.type, e.id), e)
    return tuple(seen.values())[:MAX_ENTITIES]


def _how(moment: Moment, request: QueryRequest, window: str) -> tuple[str, ...]:
    """The card states both points in time (spec 7.1): stock as of now; demand over the range."""
    model = semantic_model()
    names = ", ".join(model.measures[m].label.lower() for m in request.measures)
    by = (
        f" by {', '.join(model.query.dimensions[d].label.lower() for d in request.dimensions)}"
        if request.dimensions
        else ""
    )
    tr = request.time_range
    period = ""
    if tr is not None:
        span = f"the last {tr.last_days} days" if tr.last_days else f"{tr.from_date} to {tr.to_date}"
        period = f" Demand and movements over {span}, up to {moment.local_time}."
    summary = model.results[TOOL].how_computed.format(
        measures=names, by=by, as_of=moment.local_time, period=period
    )
    texts = [model.measures[m].how_computed.replace("{window}", window) for m in request.measures]
    return (summary, *texts)


def _envelope(
    moment: Moment,
    request: QueryRequest,
    normalized: dict[str, Any],
    streams: Sequence[str],
    window: str,
    **fields: Any,
) -> ResultEnvelope:
    return ResultEnvelope(
        evidence=moment.evidence(tuple(streams), _how(moment, request, window)),
        normalized_request=normalized,
        **fields,
    )
