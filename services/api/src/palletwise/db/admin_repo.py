"""What the admin panel reads and the one thing it changes (FR-ADM-03, 05, 06, 07).

Reads run as pw_admin_read (its own role, AGENTS.md rule 7) under the tenant's RLS. The kill switch
runs as pw_app through pw.set_agent_status() with its audit entry in the same transaction.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, LiteralString, cast
from uuid import UUID

from psycopg.types.json import Jsonb

from palletwise.db.audit_repo import AuditEntry
from palletwise.db.connection import Pool, role_pool, tenant_session, tenant_transaction

AUDIT_LIMIT = 500


def _pool() -> Pool:
    return role_pool("PW_ADMIN_READ", max_size=2)


def _rows(
    tenant: UUID, sql: LiteralString, params: tuple[Any, ...], subject: str | None = None
) -> list[dict[str, Any]]:
    with tenant_session(_pool(), tenant, read_snapshot=True, subject=subject) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        names = [c.name for c in cur.description or []]
        return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]


def agent(tenant: UUID, agent_id: str, day: str) -> dict[str, Any] | None:
    """The agent's registry entry and today's token use (wall-clock day, as the budget counts)."""
    found = _rows(
        tenant,
        """
        SELECT a.id, a.version, a.status, a.primary_model, a.prompt_version, a.daily_token_budget,
               a.governed_query_enabled,
               coalesce((SELECT sum(b.used) FROM pw.agent_budget_day b
                          WHERE b.agent_id = a.id AND b.day = %s::date), 0) AS used_tokens_today
          FROM pw.agent a WHERE a.id = %s
        """,
        (day, agent_id),
    )
    return found[0] if found else None


def usage(tenant: UUID, since: datetime) -> list[dict[str, Any]]:
    """Model calls by day, model and purpose; cost is estimated (FR-ADM-06)."""
    return _rows(
        tenant,
        """
        SELECT date_trunc('day', occurred_at)::date AS day, model, purpose,
               count(*) AS calls, sum(input_tokens) AS input_tokens,
               sum(output_tokens) AS output_tokens, round(sum(cost_estimated), 4) AS cost_estimated,
               round(avg(latency_ms)) AS avg_latency_ms,
               count(*) FILTER (WHERE outcome = 'ERROR') AS errors
          FROM pw.model_usage WHERE occurred_at >= %s
         GROUP BY 1, 2, 3 ORDER BY 1 DESC, 2, 3
        """,
        (since,),
    )


def unanswered(tenant: UUID, since: datetime, admin: str) -> list[dict[str, Any]]:
    """Declined or failed questions, grouped by the question as asked (FR-ADM-07). Read as the
    Admin: runs are private to their user, and the admin read role sees other users' runs only
    when they ended without an answer (migration 0028)."""
    return _rows(
        tenant,
        """
        SELECT lower(btrim(question)) AS question, count(*) AS times,
               max(created_at) AS last_asked, array_agg(DISTINCT coalesce(error_code, outcome)) AS reasons
          FROM pw.copilot_run
         WHERE created_at >= %s AND outcome IN ('DECLINE', 'ERROR', 'NO_RESULTS')
         GROUP BY 1 ORDER BY 2 DESC, 3 DESC LIMIT 200
        """,
        (since,),
        subject=admin,
    )


@dataclass(frozen=True, slots=True)
class AuditFilter:
    since: datetime | None = None
    until: datetime | None = None
    user: str | None = None
    agent: str | None = None
    tool: str | None = None
    action: str | None = None
    decision: str | None = None


AUDIT_COLUMNS = (
    "occurred_at", "actor_type", "user_subject", "user_role", "agent_id", "agent_version", "model",
    "prompt_version", "run_id", "action", "scope", "tool", "decision", "deny_reasons", "outcome",
    "row_count", "latency_ms", "cost_estimated", "trace_id", "parameters",
)  # fmt: skip


def audit(tenant: UUID, f: AuditFilter) -> list[dict[str, Any]]:
    """The audit trail, newest first, filtered (FR-ADM-05; the fields of NFR-AUD-02)."""
    where: list[str] = ["true"]
    params: list[object] = []
    for column, value, op in (
        ("occurred_at", f.since, ">="), ("occurred_at", f.until, "<"),
        ("user_subject", f.user, "="), ("agent_id", f.agent, "="), ("tool", f.tool, "="),
        ("action", f.action, "="), ("decision", f.decision, "="),
    ):  # fmt: skip
        if value is not None:
            where.append(f"{column} {op} %s")  # column and operator from the fixed list above
            params.append(value)
    # Built only from the fixed column list and the fixed conditions above; values are bound.
    query = cast(
        LiteralString,
        f"SELECT {', '.join(AUDIT_COLUMNS)} FROM pw.audit_event"  # noqa: S608
        f" WHERE {' AND '.join(where)} ORDER BY seq DESC LIMIT {AUDIT_LIMIT}",
    )
    return _rows(tenant, query, tuple(params))


@dataclass(frozen=True, slots=True)
class StatusChange:
    outcome: str  # changed, unchanged, not_found
    before: str | None
    after: str | None


def set_status(
    pool: Pool, tenant: UUID, agent_id: str, status: str, audit_entry: Callable[[StatusChange], AuditEntry]
) -> StatusChange:
    """The kill switch (FR-ADM-03) and its audit entry in one transaction."""
    with pool.connection() as conn, tenant_transaction(conn, tenant):
        row = conn.execute(
            "SELECT outcome, before_status, after_status FROM pw.set_agent_status(%s, %s)",
            (agent_id, status),
        ).fetchone()
        assert row is not None
        change = StatusChange(*row)
        conn.execute("SELECT pw.append_audit(%s, %s)", (tenant, Jsonb(audit_entry(change).as_json())))
    return change


def set_governed_query(
    pool: Pool, tenant: UUID, agent_id: str, enabled: bool, audit_entry: Callable[[StatusChange], AuditEntry]
) -> StatusChange:
    """The governed query switch (ADR 0002 section 7, D5) and its audit entry in one transaction."""
    with pool.connection() as conn, tenant_transaction(conn, tenant):
        row = conn.execute(
            "SELECT outcome, before_enabled, after_enabled FROM pw.set_governed_query(%s, %s)",
            (agent_id, enabled),
        ).fetchone()
        assert row is not None
        change = StatusChange(row[0], None if row[1] is None else str(row[1]).lower(),
                              None if row[2] is None else str(row[2]).lower())  # fmt: skip
        conn.execute("SELECT pw.append_audit(%s, %s)", (tenant, Jsonb(audit_entry(change).as_json())))
    return change
