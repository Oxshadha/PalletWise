"""The governed query tool (ADR 0002 sections 1, 5, 6, 6a and 7; slice-5-acceptance step 3).

`run_governed_query(sql, purpose)`: the last resort after the tools and the query language. Per
call: the Admin's switch and a passed configuration (fingerprint) are required; the guard accepts
or refuses the query; the runner executes the normalized query as pw_query_read in its own
read-only transaction with hard limits, tagged with the agent and run; the result is labelled as
a custom query. What ran, and why anything was refused, goes into the call's audit record.
"""

import hashlib
import json
import logging
import time
from datetime import date, datetime
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import Any, LiteralString, cast
from uuid import UUID

import psycopg
from pydantic import BaseModel, ConfigDict, Field

from palletwise.contracts.results import (
    Column,
    EntityRef,
    Highlight,
    HighlightReason,
    RequestRejected,
    ResultEnvelope,
    ResultStatus,
    Severity,
)
from palletwise.db.connection import Pool, role_pool
from palletwise.governed import guard
from palletwise.semantic.locations import read_moment
from palletwise.telemetry.tracing import tracer
from palletwise.tools.executor import ToolContext, ToolSpec

log = logging.getLogger(__name__)

SCOPE = "query.governed"
MAX_ROWS = 200
MAX_COLUMNS = 20
MAX_BYTES = 256 * 1024
TIMEOUT_MS = 2000
MAX_PLAN_COST = 5_000_000.0  # EXPLAIN estimate; a heuristic on top of the hard limits
SEMANTIC = Path(__file__).resolve().parents[1] / "semantic"
GATE_FILE = SEMANTIC / "governed_gate.json"
LABEL = "Answered with a custom query"


class GovernedQueryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sql: str = Field(
        min_length=8,
        max_length=4000,
        description="One SELECT over the gq views only (see the catalog), joined only as the catalog allows.",
    )
    purpose: str = Field(
        min_length=3, max_length=200, description="In a few words, what the query answers, for the audit."
    )


def _pool() -> Pool:
    return role_pool("PW_QUERY_READ", max_size=2)


# ------------------------------------------------------------------ configuration fingerprint (D3)


@cache
def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fingerprint(conn: psycopg.Connection, model: str, prompt_version: str) -> str:
    """The configuration a gate passed for: guard version, model and prompt, the catalog and the
    semantic model files, the gq view definitions and the query role's privileges."""
    views = conn.execute(
        "SELECT coalesce(string_agg(viewname || ':' || definition, '|' ORDER BY viewname), '')"
        " FROM pg_views WHERE schemaname = 'gq'"
    ).fetchone()
    grants = conn.execute(
        "SELECT coalesce(string_agg(table_schema || '.' || table_name || ':' || privilege_type, ','"
        " ORDER BY table_schema, table_name, privilege_type), '') FROM information_schema.role_table_grants"
        " WHERE grantee = current_user"
    ).fetchone()
    parts = {
        "guard": guard.GUARD_VERSION,
        "model": model,
        "prompt": prompt_version,
        "catalog": _file_digest(guard.CATALOG),
        "semantic_model": _file_digest(SEMANTIC / "model.yaml"),
        "views": hashlib.sha256(str(views[0] if views else "").encode()).hexdigest(),
        "grants": hashlib.sha256(str(grants[0] if grants else "").encode()).hexdigest(),
    }
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()[:32]


def passed_fingerprints() -> set[str]:
    """Configurations whose gate passed and were reviewed (semantic/governed_gate.json)."""
    try:
        entries = json.loads(GATE_FILE.read_text(encoding="utf-8")).get("passed", [])
    except FileNotFoundError:
        return set()
    return {str(e["fingerprint"]) for e in entries}


# ------------------------------------------------------------------ the runner (section 5)


def _plain(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return value


def _type(values: list[Any]) -> str:
    sample = next((v for v in values if v is not None), None)
    if isinstance(sample, bool):
        return "boolean"
    if isinstance(sample, int):
        return "integer"
    if isinstance(sample, (float, Decimal)):
        return "number"
    if isinstance(sample, (datetime, date)):
        return "datetime"
    return "text"


def run(
    sql: str, tenant: UUID, site: UUID, agent_id: str, run_id: UUID | None
) -> tuple[list[str], list[tuple[Any, ...]], bool, int]:
    """Runs an accepted query: (column names, rows, truncated, bytes). Read-only transaction as
    pw_query_read; tenant and site from the session; tagged with the agent and run."""
    with _pool().connection() as conn:
        try:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                cur.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant),))
                cur.execute("SELECT set_config('app.site_id', %s, true)", (str(site),))
                cur.execute("SELECT set_config('search_path', 'pg_catalog, gq', true)")
                cur.execute("SELECT set_config('statement_timeout', %s, true)", (str(TIMEOUT_MS),))
                cur.execute("SELECT set_config('work_mem', '16MB', true)")
                # Traceability (section 6a): the statement links back to its agent run.
                cur.execute(
                    "SELECT set_config('application_name', %s, true)", (f"palletwise-agent:{agent_id}",)
                )
                cur.execute("SELECT set_config('app.agent_id', %s, true)", (agent_id,))
                cur.execute("SELECT set_config('app.run_id', %s, true)", (str(run_id or ""),))
                text = cast(LiteralString, sql)  # the guard's normalized output, never model text
                cur.execute(f"EXPLAIN (FORMAT JSON) {text}")
                plan = cur.fetchone()
                cost = float(plan[0][0]["Plan"]["Total Cost"]) if plan else 0.0
                if cost > MAX_PLAN_COST:
                    raise RequestRejected(
                        "This query would read too much data; narrow it (a filter, a period or a group).",
                        {"verdict": "REFUSED", "rule": "too_broad", "plan_cost": round(cost)},
                    )
                # The guard's accepted, deparsed SELECT; the limit is a constant.
                wrapped = cast(LiteralString, f"SELECT * FROM ({text}) AS governed LIMIT {MAX_ROWS + 1}")  # noqa: S608
                cur.execute(wrapped)
                names = [c.name for c in cur.description or []]
                if len(names) > MAX_COLUMNS:
                    raise RequestRejected(
                        f"Return at most {MAX_COLUMNS} columns.", {"verdict": "REFUSED", "rule": "too_wide"}
                    )
                rows: list[tuple[Any, ...]] = []
                size = 0
                for row in cur:
                    size += len(json.dumps([_plain(v) for v in row], default=str))
                    if size > MAX_BYTES or len(rows) == MAX_ROWS:
                        return names, rows, True, size
                    rows.append(tuple(row))
                return names, rows, False, size
        except psycopg.errors.QueryCanceled as exc:
            raise RequestRejected(
                "The query took too long (2 seconds); narrow it.", {"verdict": "REFUSED", "rule": "timeout"}
            ) from exc
        except psycopg.errors.DataError as exc:  # division by zero, a bad date: the query's own fault
            reason = exc.diag.message_primary or "invalid data"
            raise RequestRejected(
                f"The query failed when it ran ({reason}); fix it once, else decline.",
                {"verdict": "REFUSED", "rule": "runtime"},
            ) from exc


# ------------------------------------------------------------------ the tool


def _entities(names: list[str], rows: list[tuple[Any, ...]]) -> tuple[list[EntityRef], list[Highlight]]:
    """Highlights only from rows that carry a bin or product id from the views."""
    entities: list[EntityRef] = []
    highlights: list[Highlight] = []
    for row in rows:
        values = dict(zip(names, row, strict=True))
        if isinstance(values.get("bin_id"), UUID):
            label = str(values.get("bin_code") or values["bin_id"])
            entities.append(EntityRef(type="bin", id=values["bin_id"], label=label))
            highlights.append(
                Highlight(
                    target_type="bin",
                    target_id=values["bin_id"],
                    reason=HighlightReason.QUERY,
                    severity=Severity.INFO,
                )
            )
        if isinstance(values.get("product_id"), UUID):
            label = str(values.get("sku") or values["product_id"])
            entities.append(EntityRef(type="product", id=values["product_id"], label=label))
    return entities[:500], highlights[:200]


def handle(ctx: ToolContext, params: GovernedQueryInput) -> ResultEnvelope:
    agent, run_ctx = ctx.agent, ctx.run
    if agent is None or not agent.governed_query_enabled:
        raise RequestRejected(
            "Custom queries are switched off for this organisation; answer with the other tools or decline.",
            {"verdict": "OFF"},
        )
    started = time.monotonic()
    with tracer.start_as_current_span("governed.guard") as span:
        try:
            accepted = guard.check(params.sql)
        except guard.Refused as exc:
            span.set_attribute("pw.governed.rule", exc.rule)
            raise RequestRejected(
                f"The query was refused ({exc.rule}): {exc}", {"verdict": "REFUSED", "rule": exc.rule}
            ) from exc
    digest = hashlib.sha256(accepted.sql.encode()).hexdigest()
    with _pool().connection() as conn:
        found = fingerprint(conn, agent.primary_model, agent.prompt_version)
    audit: dict[str, Any] = {"sql": accepted.sql, "sql_hash": digest, "fingerprint": found}
    if found not in passed_fingerprints():
        raise RequestRejected(
            "Custom queries are not yet evaluated for this configuration; answer with the other tools"
            " or decline.",
            {**audit, "verdict": "NOT_EVALUATED"},
        )
    with tracer.start_as_current_span("governed.run") as span:
        try:
            names, rows, truncated, size = run(
                accepted.sql, ctx.tenant, ctx.site, agent.id, run_ctx.run_id if run_ctx else None
            )
        except RequestRejected as exc:
            exc.audit = {**audit, **exc.audit}
            raise
        span.set_attribute("pw.governed.rows", len(rows))
    moment = read_moment(ctx.conn, ctx.site)
    entities, highlights = _entities(names, rows)
    columns = tuple(
        Column(key=n, label=n.replace("_", " ").capitalize(), type=_type([r[i] for r in rows]))  # pyright: ignore[reportArgumentType]
        for i, n in enumerate(names)
    )
    audit.update(
        verdict="RAN", rows=len(rows), truncated=truncated, bytes=size,
        duration_ms=round((time.monotonic() - started) * 1000), views=list(accepted.views),
    )  # fmt: skip
    return ResultEnvelope(
        status=ResultStatus.OK if rows else ResultStatus.NO_RESULTS,
        message=None if rows else "The query found no rows.",
        columns=columns,
        rows=tuple(dict(zip(names, (_plain(v) for v in r), strict=True)) for r in rows),
        truncated=truncated,
        highlights=tuple(highlights),
        evidence=moment.evidence(
            ("MASTER", "MOVEMENTS", "ORDERS", "ALLOCATIONS"), (f"{LABEL}: {params.purpose}.",)
        ),
        entities=tuple(entities),
        normalized_request={"tool": "run_governed_query", "source": "governed_query", "audit": audit},
    )


def _catalog_text() -> str:
    """The governed views as the model reads them: grain, key, columns, references, measure rules."""
    lines: list[str] = []
    for name, view in guard.catalog().items():
        columns: list[str] = []
        specs: dict[str, dict[str, Any]] = view["columns"]
        for column, spec in specs.items():
            note = spec["kind"]
            if spec["kind"] == "ref":
                note = f"ref to {spec['refers']}"
            elif spec["kind"] == "measure":
                note = f"measure {spec.get('unit')}, {'/'.join(spec.get('aggregates') or [])}"
            columns.append(f"{column} ({note})")
        key = ", ".join(view.get("key") or []) or "none"
        lines.append(f"gq.{name}: {view['grain']}; key {key}; " + "; ".join(columns))
    return "\n".join(lines)


RUN_GOVERNED_QUERY = ToolSpec(
    name="run_governed_query",
    description=(
        "LAST RESORT, but use it instead of declining: when no other tool and no query_inventory "  # noqa: S608 (prose)
        "request can express the question and the data is in these governed views, write one "
        "read-only SELECT over them (nothing else is reachable). Decline only when the views do not "
        "hold the data or the rules below forbid the calculation.\n"
        f"{_catalog_text()}\n"
        "Rules: JOIN a view only on its key, from a column that refers to it (many to one); sum or "
        "avg only measures of the first view in FROM, only with the aggregates listed; never add "
        "measures of different units; count(DISTINCT) only keys or references; no WITH, window "
        "functions or subqueries in FROM; subqueries only in WHERE or HAVING; the site's time is "
        "(SELECT as_of FROM gq.site_now), never now(). A refused query says why: fix it once, else "
        "decline. The answer is labelled as a custom query."
    ),
    input_model=GovernedQueryInput,
    scope=SCOPE,
    side_effect="read",
    handler=handle,
    rate_limit=10,
)
