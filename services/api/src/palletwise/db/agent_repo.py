"""The agent registry (identity spec 5), read fresh on every tool call for the kill switch."""

from dataclasses import dataclass
from uuid import UUID

from psycopg_pool import ConnectionPool

from palletwise.db.connection import tenant_transaction


@dataclass(frozen=True, slots=True)
class AgentRecord:
    id: str
    version: str
    status: str
    allowed_scopes: tuple[str, ...]
    primary_model: str
    fallback_model: str | None
    prompt_version: str
    max_steps: int
    daily_token_budget: int

    @property
    def active(self) -> bool:
        return self.status == "ACTIVE"


class AgentRegistry:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def load(self, tenant: UUID, agent_id: str) -> AgentRecord | None:
        with self._pool.connection() as conn, tenant_transaction(conn, tenant):
            row = conn.execute(
                """
                SELECT id, version, status, allowed_scopes, primary_model, fallback_model,
                       prompt_version, max_steps, daily_token_budget
                  FROM pw.agent WHERE id = %s
                """,
                (agent_id,),
            ).fetchone()
        if row is None:
            return None
        return AgentRecord(row[0], row[1], row[2], tuple(row[3]), *row[4:])
