# pglast's node classes carry only partial type information and declare every field optional, so
# pyright cannot know the parse tree's attribute types. The checks below test each node's class
# before use, and check() refuses any tree whose shape is not as expected.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false
# pyright: reportOptionalMemberAccess=false, reportOptionalSubscript=false, reportOptionalIterable=false
# pyright: reportArgumentType=false, reportCallIssue=false
"""The governed query guard (ADR 0002 sections 2 and 4, slice-5-acceptance step 2).

Parsing proves only that a text is SQL. Acceptance comes from walking the whole tree and allowing
nothing unlisted (section 2), then checking the query against the governed views' declared
semantics (section 4): joins only many to one, measures aggregated only at the base grain and only
as declared, units never mixed by addition or subtraction, counts of defined things. Every refusal
names its rule in words the model and the Admin can read. The accepted tree is deparsed; that
normalized text is what runs. Pure: no database, no model.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

import yaml
from pglast import ast, parse_sql
from pglast.enums import A_Expr_Kind, BoolExprType, JoinType, SetOperation, SubLinkType
from pglast.parser import ParseError
from pglast.stream import RawStream

CATALOG = Path(__file__).resolve().parents[1] / "semantic" / "gq_catalog.yaml"
GUARD_VERSION = "1"

AGGREGATES = {"count", "sum", "avg", "min", "max"}
FUNCTIONS = AGGREGATES | {"round", "abs", "lower", "upper", "date_trunc", "extract", "date_part"}
OPERATORS = {"=", "<>", "!=", "<", "<=", ">", ">=", "+", "-", "*", "/", "~~", "!~~", "~~*", "!~~*"}
TYPES = {"int2", "int4", "int8", "integer", "bigint", "numeric", "text", "varchar", "date",
         "timestamptz", "interval", "bool", "boolean"}  # fmt: skip
EXPR_KINDS = {
    A_Expr_Kind.AEXPR_OP, A_Expr_Kind.AEXPR_IN, A_Expr_Kind.AEXPR_LIKE, A_Expr_Kind.AEXPR_ILIKE,
    A_Expr_Kind.AEXPR_BETWEEN, A_Expr_Kind.AEXPR_NOT_BETWEEN, A_Expr_Kind.AEXPR_NULLIF,
    A_Expr_Kind.AEXPR_DISTINCT, A_Expr_Kind.AEXPR_NOT_DISTINCT,
}  # fmt: skip
# Node types a plain select uses; anything else is refused (section 2).
NODES = (
    ast.RawStmt, ast.SelectStmt, ast.ResTarget, ast.ColumnRef, ast.String, ast.A_Const,
    ast.Integer, ast.Float, ast.Boolean, ast.A_Star, ast.RangeVar, ast.Alias, ast.JoinExpr,
    ast.A_Expr, ast.BoolExpr, ast.FuncCall, ast.SortBy, ast.NullTest, ast.BooleanTest,
    ast.CaseExpr, ast.CaseWhen, ast.SubLink, ast.TypeCast, ast.TypeName, ast.CoalesceExpr,
    ast.MinMaxExpr,
)  # fmt: skip


class Refused(ValueError):
    """The query is not accepted; `rule` names the rule, the message says why in plain words."""

    def __init__(self, rule: str, message: str) -> None:
        super().__init__(message)
        self.rule = rule


@dataclass(frozen=True, slots=True)
class Accepted:
    sql: str  # the normalized query that runs (deparsed from the accepted tree)
    views: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Column:
    view: str
    name: str
    kind: str  # key, ref, attribute, measure
    unit: str | None = None
    aggregates: tuple[str, ...] = ()
    refers: str | None = None


@dataclass
class Scope:
    """The views of one SELECT: alias to view; the base view is the first in FROM."""

    views: dict[str, str] = field(default_factory=lambda: {})
    base: str | None = None
    outer: "Scope | None" = None
    outputs: set[str] = field(default_factory=lambda: set())
    existence: bool = False


@cache
def catalog() -> dict[str, Any]:
    return dict(yaml.safe_load(CATALOG.read_text(encoding="utf-8"))["views"])


def column(view: str, name: str) -> Column | None:
    spec = catalog()[view]["columns"].get(name)
    if spec is None:
        return None
    return Column(
        view,
        name,
        str(spec["kind"]),
        spec.get("unit"),
        tuple(spec.get("aggregates") or ()),
        spec.get("refers"),
    )


def key_of(view: str) -> list[str]:
    return list(catalog()[view].get("key") or [])


def check(sql: str) -> Accepted:
    """Accepts one governed SELECT or raises Refused."""
    try:
        statements = parse_sql(sql)
    except ParseError as exc:
        raise Refused("syntax", f"The query is not valid SQL: {exc}.") from exc
    if len(statements) != 1:
        raise Refused("one_statement", "Send exactly one SELECT statement.")
    for node in _walk(statements[0]):
        _allowed(node)
    stmt = statements[0].stmt
    if not isinstance(stmt, ast.SelectStmt):
        raise Refused("select_only", "Only a SELECT statement can run.")
    views: set[str] = set()
    try:
        _select(stmt, None, views, top=True)
    except (AttributeError, TypeError, IndexError, KeyError) as exc:
        # A tree shape the rules do not expect is refused, never run.
        raise Refused("construct", "This query's structure is not supported.") from exc
    return Accepted(RawStream()(statements[0]), tuple(sorted(views)))


# ------------------------------------------------------------------ section 2: the allowlist


def _walk(node: Any) -> Iterator[Any]:
    if isinstance(node, ast.Node):
        yield node
        for name in node:  # pglast nodes iterate over their attribute names
            yield from _walk(getattr(node, name))
    elif isinstance(node, (tuple, list)):
        for item in node:  # pyright: ignore[reportUnknownVariableType]
            yield from _walk(item)


def _own_columns(node: Any) -> Iterator[ast.ColumnRef]:
    """Column references of an expression, not entering subqueries (they have their own scope)."""
    if isinstance(node, ast.SubLink):
        if node.testexpr is not None:
            yield from _own_columns(node.testexpr)
        return
    if isinstance(node, ast.ColumnRef):
        yield node
        return
    if isinstance(node, ast.Node):
        for name in node:
            yield from _own_columns(getattr(node, name))
    elif isinstance(node, (tuple, list)):
        for item in node:  # pyright: ignore[reportUnknownVariableType]
            yield from _own_columns(item)


def _allowed(node: Any) -> None:
    if isinstance(node, ast.SQLValueFunction):
        raise Refused("clock", "The system clock is not used; take the site's time from gq.site_now.as_of.")
    if not isinstance(node, NODES):
        raise Refused("construct", f"{type(node).__name__} is not allowed in a governed query.")
    if isinstance(node, ast.SelectStmt):
        if node.op != SetOperation.SETOP_NONE:
            raise Refused("construct", "UNION, INTERSECT and EXCEPT are not allowed.")
        if node.withClause or node.intoClause or node.lockingClause or node.valuesLists:
            raise Refused("construct", "WITH, INTO, locking and VALUES are not allowed.")
        if node.distinctClause is not None and any(d is not None for d in node.distinctClause):
            raise Refused("construct", "DISTINCT ON is not allowed; use SELECT DISTINCT or GROUP BY.")
        if node.windowClause:
            raise Refused("construct", "Window functions are not allowed in this version.")
    elif isinstance(node, ast.RangeVar):
        if node.schemaname not in (None, "gq") or node.catalogname or node.relname not in catalog():
            raise Refused("relation", f"{node.relname} is not a governed view. Use: {', '.join(catalog())}.")
    elif isinstance(node, ast.FuncCall):
        names = [n.sval for n in node.funcname]
        name = names[-1].lower()
        if name in ("now", "clock_timestamp", "statement_timestamp", "transaction_timestamp", "timeofday"):
            raise Refused(
                "clock", "The system clock is not used; take the site's time from gq.site_now.as_of."
            )
        if (len(names) > 1 and names[0] != "pg_catalog") or name not in FUNCTIONS:
            raise Refused("function", f"The function {'.'.join(names)} is not allowed.")
        if node.over is not None:
            raise Refused("construct", "Window functions are not allowed in this version.")
        if node.agg_filter is not None or node.agg_order or node.agg_within_group or node.func_variadic:
            raise Refused("construct", "FILTER, ordered and variadic aggregates are not allowed.")
        if node.agg_star and name != "count":
            raise Refused("function", "Only count(*) may use *.")
    elif isinstance(node, ast.A_Expr):
        if node.kind not in EXPR_KINDS:
            raise Refused("operator", "This kind of expression is not allowed.")
        if node.kind == A_Expr_Kind.AEXPR_OP:
            op = node.name[-1].sval
            if op not in OPERATORS or len(node.name) > 1:
                raise Refused("operator", f"The operator {op} is not allowed.")
    elif isinstance(node, ast.TypeCast):
        name = node.typeName.names[-1].sval.lower()
        if name not in TYPES or node.typeName.arrayBounds:
            raise Refused("cast", f"Casting to {name} is not allowed.")
    elif isinstance(node, ast.MinMaxExpr):
        pass  # greatest and least
    elif isinstance(node, ast.SubLink) and node.subLinkType not in (
        SubLinkType.EXISTS_SUBLINK,
        SubLinkType.ANY_SUBLINK,
        SubLinkType.EXPR_SUBLINK,
    ):
        raise Refused("construct", "Only EXISTS, IN and scalar subqueries are allowed.")


# ------------------------------------------------------------------ section 4: semantics


def _select(
    stmt: ast.SelectStmt, outer: Scope | None, views: set[str], top: bool = False, existence: bool = False
) -> None:
    """`existence`: an EXISTS or IN subquery, whose truth repeated rows cannot change."""
    scope = Scope(outer=outer, existence=existence)
    if not stmt.fromClause:
        raise Refused("relation", "A governed query reads from the governed views (FROM ...).")
    if len(stmt.fromClause) > 1:
        raise Refused("join", "Join views with JOIN ... ON, not with commas.")
    _from(stmt.fromClause[0], scope)
    views.update(scope.views.values())
    for target in stmt.targetList or ():
        if target.name:
            scope.outputs.add(target.name)
        _expr(target.val, scope, views, place="select")
    if stmt.whereClause is not None:
        _expr(stmt.whereClause, scope, views, place="where")
    for item in stmt.groupClause or ():
        _expr(item, scope, views, place="group")
    if stmt.havingClause is not None:
        _expr(stmt.havingClause, scope, views, place="having")
    for item in stmt.sortClause or ():
        _expr(item.node, scope, views, place="order")
    for bound in (stmt.limitCount, stmt.limitOffset):
        if bound is not None and not isinstance(bound, ast.A_Const):
            raise Refused("construct", "LIMIT and OFFSET take a number.")
    if not top and stmt.sortClause:
        raise Refused("construct", "ORDER BY is allowed only in the main query.")


def _from(node: Any, scope: Scope) -> None:
    if isinstance(node, ast.RangeVar):
        alias = node.alias.aliasname if node.alias else node.relname
        if alias in scope.views:
            raise Refused("join", f"The name {alias} is used twice; give each view its own alias.")
        scope.views[alias] = node.relname
        scope.base = scope.base or node.relname
        return
    if not isinstance(node, ast.JoinExpr):
        raise Refused("construct", "FROM may name governed views and JOINs only.")
    if node.jointype not in (JoinType.JOIN_INNER, JoinType.JOIN_LEFT):
        raise Refused("join", "Only JOIN and LEFT JOIN are allowed.")
    if node.usingClause or node.isNatural or node.quals is None or node.alias is not None:
        raise Refused("join", "Join with ON and an equality of a reference to a key.")
    _from(node.larg, scope)
    before = dict(scope.views)
    _from(node.rarg, scope)
    joined = {a: v for a, v in scope.views.items() if a not in before}
    if len(joined) != 1:
        raise Refused("join", "Join one view at a time.")
    ((alias, view),) = joined.items()
    _join_condition(node.quals, alias, view, before, scope)


def _join_condition(quals: Any, alias: str, view: str, before: dict[str, str], scope: Scope) -> None:
    """Rule 1: the joined view joins on its own key, from a reference to it in an earlier view, so
    the rows of the earlier views are never multiplied."""
    if not (
        isinstance(quals, ast.A_Expr) and quals.kind == A_Expr_Kind.AEXPR_OP and quals.name[-1].sval == "="
    ):
        raise Refused("join", "A join condition is one equality: reference = key.")
    sides = [_column(quals.lexpr, scope), _column(quals.rexpr, scope)]
    if any(s is None for s in sides):
        raise Refused("join", "A join condition compares two columns.")
    if scope.existence:
        # Inside EXISTS or IN only existence matters: any two columns identifying the same view.
        targets = {_identifies(s[1]) for s in sides if s is not None}
        if len(targets) != 1 or None in targets:
            raise Refused(
                "join", "Join on two columns that identify the same thing (for example product_id)."
            )
        return
    new = [s for s in sides if s is not None and s[0] == alias]
    old = [s for s in sides if s is not None and s[0] in before]
    keys = key_of(view)
    if len(new) != 1 or len(old) != 1 or len(keys) != 1 or new[0][1].name != keys[0]:
        raise Refused(
            "join_fanout",
            f"{view} may be joined only on its key {', '.join(keys) or '(none)'}, from a column that"
            f" refers to it. Joining it another way would count rows more than once.",
        )
    ref = old[0][1]
    if ref.kind != "ref" or ref.refers != view:
        raise Refused(
            "join_fanout", f"{ref.view}.{ref.name} does not refer to {view}; join on a declared reference."
        )


def _identifies(col: Column) -> str | None:
    """The view a column identifies a row of: its reference target, or its own view for a key."""
    if col.kind == "ref":
        return col.refers
    if col.kind == "key" and key_of(col.view) == [col.name]:
        return col.view
    return None


def _column(node: Any, scope: Scope) -> tuple[str, Column] | None:
    """(alias, column) for a column reference, searching this scope then the outer ones."""
    if not isinstance(node, ast.ColumnRef):
        return None
    parts = [f.sval for f in node.fields if isinstance(f, ast.String)]
    if any(isinstance(f, ast.A_Star) for f in node.fields):
        return None
    s: Scope | None = scope
    while s is not None:
        if len(parts) == 2:
            alias, name = parts
            if alias in s.views:
                found = column(s.views[alias], name)
                if found is None:
                    raise Refused("unknown_column", f"{s.views[alias]} has no column {name}.")
                return alias, found
        elif len(parts) == 1:
            hits = [(a, c) for a, v in s.views.items() if (c := column(v, parts[0])) is not None]
            if len(hits) > 1:
                raise Refused(
                    "ambiguous", f"{parts[0]} is in more than one view; qualify it (alias.{parts[0]})."
                )
            if hits:
                return hits[0]
        s = s.outer
    if len(parts) == 1 and parts[0] in scope.outputs:
        return None  # an output name in ORDER BY
    raise Refused("unknown_column", f"No governed view defines {'.'.join(parts)}.")


def _expr(node: Any, scope: Scope, views: set[str], place: str) -> None:
    if node is None:
        return
    if isinstance(node, ast.ColumnRef):
        _column(node, scope)
        return
    if isinstance(node, ast.SubLink):
        if place not in ("where", "having"):
            raise Refused("construct", "Subqueries are allowed only in WHERE and HAVING.")
        if node.testexpr is not None:
            _expr(node.testexpr, scope, views, place)
        _select(node.subselect, scope, views, existence=node.subLinkType != SubLinkType.EXPR_SUBLINK)
        return
    if isinstance(node, ast.FuncCall):
        name = node.funcname[-1].sval.lower()
        if name in AGGREGATES:
            if place in ("where", "group"):
                raise Refused("construct", "Aggregates belong in SELECT or HAVING, not WHERE or GROUP BY.")
            _aggregate(node, name, scope)
        for arg in node.args or ():
            _expr(arg, scope, views, place)
        return
    if (
        isinstance(node, ast.A_Expr)
        and node.kind == A_Expr_Kind.AEXPR_OP
        and node.name[-1].sval in ("+", "-")
    ):
        _units(node, scope)
    if isinstance(node, ast.BoolExpr) and node.boolop not in (
        BoolExprType.AND_EXPR,
        BoolExprType.OR_EXPR,
        BoolExprType.NOT_EXPR,
    ):
        raise Refused("construct", "This boolean expression is not allowed.")
    for name in node if isinstance(node, ast.Node) else ():
        child = getattr(node, name)
        for item in child if isinstance(child, (tuple, list)) else (child,):  # pyright: ignore[reportUnknownVariableType]
            if isinstance(item, ast.Node):
                _expr(item, scope, views, place)


def _aggregate(node: ast.FuncCall, name: str, scope: Scope) -> None:
    """Rules 2, 3 and 5."""
    if name == "count":
        if node.agg_distinct:
            arg = _column(node.args[0], scope) if node.args else None
            if arg is None or arg[1].kind == "measure":
                raise Refused(
                    "count",
                    "count(DISTINCT ...) counts identifiers or attributes, for example order ids or"
                    " customer codes, never measure values.",
                )
        return
    columns = [c for c in (_column(n, scope) for n in _own_columns(node.args)) if c]
    if not columns:
        raise Refused("aggregate", f"{name} needs a column.")
    for alias, col in columns:
        if col.kind != "measure":
            if name in ("min", "max"):
                continue  # earliest, latest, first or last of an attribute
            raise Refused("aggregate", f"{col.view}.{col.name} is not a measure; {name} of it means nothing.")
        if name not in col.aggregates:
            raise Refused(
                "non_additive",
                f"{col.view}.{col.name} ({col.unit}) cannot be aggregated with {name};"
                f" allowed: {', '.join(col.aggregates)}.",
            )
        if name in ("sum", "avg") and scope.views.get(alias) != scope.base:
            raise Refused(
                "measure_grain",
                f"{name} of {col.view}.{col.name} after joining it to {scope.base} would repeat it per"
                f" {scope.base} row. Aggregate measures of the first view in FROM only.",
            )


def _units(node: ast.A_Expr, scope: Scope) -> None:
    """Rule 4: adding or subtracting measures of different units means nothing."""
    units = set()
    for side in (node.lexpr, node.rexpr):
        for n in _own_columns(side):
            found = _column(n, scope)
            if found and found[1].kind == "measure":
                units.add(found[1].unit)
    if len(units) > 1:
        raise Refused(
            "units", f"Adding or subtracting {' and '.join(sorted(str(u) for u in units))} mixes units."
        )
