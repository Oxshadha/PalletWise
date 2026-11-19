"""Appends to the hash-chained audit log through pw.append_audit() (evals spec 15).

The database computes the sequence, time and hashes; callers only describe what happened.
"""

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any, Literal, cast
from uuid import UUID

from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from palletwise.db.connection import tenant_transaction

ActorType = Literal["USER", "AGENT", "SYSTEM"]


@dataclass(frozen=True, slots=True, kw_only=True)
class AuditEntry:
    actor_type: ActorType
    action: str
    outcome: str
    trace_id: str
    site_id: UUID | None = None
    user_subject: str | None = None
    user_role: str | None = None
    agent_id: str | None = None
    agent_version: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    run_id: UUID | None = None
    scope: str | None = None
    tool: str | None = None
    parameters: Any = None
    decision: Literal["ALLOW", "DENY"] | None = None
    deny_reasons: tuple[str, ...] | None = None
    row_count: int | None = None
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_estimated: Decimal | None = None

    def as_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in asdict(self).items():
            if value is None:
                continue
            if isinstance(value, UUID | Decimal):
                value = str(value)
            elif isinstance(value, tuple):
                value = list(cast(tuple[str, ...], value))
            out[key] = value
        return out


@dataclass(frozen=True, slots=True)
class ChainCheck:
    ok: bool
    checked: int
    bad_seq: int | None
    problem: str | None


class AuditLog:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def append(self, tenant: UUID, entry: AuditEntry) -> UUID:
        with self._pool.connection() as conn, tenant_transaction(conn, tenant):
            row = conn.execute("SELECT pw.append_audit(%s, %s)", (tenant, Jsonb(entry.as_json()))).fetchone()
        assert row is not None
        return row[0]

    def verify(self, tenant: UUID) -> ChainCheck:
        with self._pool.connection() as conn, tenant_transaction(conn, tenant):
            row = conn.execute("SELECT * FROM pw.verify_audit_chain(%s)", (tenant,)).fetchone()
        assert row is not None
        return ChainCheck(*row)
