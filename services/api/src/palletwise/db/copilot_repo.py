"""Conversations and runs (pw_memory), model usage, budget and rate limits (pw_app).

Conversations are visible only to their owner: every query binds tenant and subject, and RLS
checks both (FR-COP-11).
"""

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from palletwise.contracts.results import MAX_ENTITIES, WorkingSet
from palletwise.db.connection import tenant_session
from palletwise.db.ids import uuid7

MAX_TITLE = 60
# Longer than any run can last (run timeout plus disconnect grace), so only runs whose process
# died are caught.
INTERRUPTED_AFTER = "5 minutes"


@dataclass(frozen=True, slots=True)
class Owner:
    """Whose conversation this is. Always from the session, never from a request."""

    tenant: UUID
    subject: str
    site: UUID


@dataclass(frozen=True, slots=True)
class Turn:
    question: str
    answer: str | None


@dataclass(frozen=True, slots=True)
class RunRecord:
    id: UUID
    seq: int
    question: str
    path: str | None
    status: str
    outcome: str | None
    answer_text: str | None
    results: list[Any]
    suggestions: list[Any]
    trace_id: str
    created_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True, slots=True)
class ConversationSummary:
    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class RunOutcome:
    status: str  # COMPLETED, CANCELLED, FAILED
    path: str | None = None
    outcome: str | None = None
    answer_text: str | None = None
    results: list[Any] = field(default_factory=lambda: [])
    suggestions: list[Any] = field(default_factory=lambda: [])
    entities: list[Any] = field(default_factory=lambda: [])
    error_code: str | None = None


def title_from(question: str) -> str:
    """The first question, shortened to 60 characters (UX spec 6)."""
    text = " ".join(question.split())
    return text if len(text) <= MAX_TITLE else text[: MAX_TITLE - 3].rstrip() + "..."


class ConversationStore:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def _session(self, owner: Owner) -> Any:
        return tenant_session(self._pool, owner.tenant, subject=owner.subject)

    @staticmethod
    def _close_interrupted(conn: Any) -> None:
        """Runs whose API process died never stay RUNNING: the owner's next visit closes them."""
        conn.execute(
            "UPDATE pw.copilot_run SET status = 'FAILED', error_code = 'INTERRUPTED', finished_at = now()"
            " WHERE status = 'RUNNING' AND created_at < now() - %s::interval",
            (INTERRUPTED_AFTER,),
        )

    def owns(self, owner: Owner, conversation_id: UUID) -> bool:
        with self._session(owner) as conn:
            row = conn.execute(
                "SELECT 1 FROM pw.conversation WHERE id = %s AND site_id = %s", (conversation_id, owner.site)
            ).fetchone()
        return row is not None

    def start_run(
        self,
        owner: Owner,
        conversation_id: UUID | None,
        question: str,
        trace_id: str,
        agent_id: str,
        agent_version: str | None,
        prompt_version: str | None,
    ) -> tuple[UUID, UUID]:
        """Creates the conversation when needed; returns (conversation id, run id)."""
        run_id = uuid7()
        with self._session(owner) as conn:
            self._close_interrupted(conn)
            if conversation_id is None:
                conversation_id = uuid7()
                conn.execute(
                    "INSERT INTO pw.conversation (id, tenant_id, site_id, user_subject, title)"
                    " VALUES (%s, %s, %s, %s, %s)",
                    (conversation_id, owner.tenant, owner.site, owner.subject, title_from(question)),
                )
            else:
                conn.execute(
                    "UPDATE pw.conversation SET updated_at = now() WHERE id = %s AND site_id = %s",
                    (conversation_id, owner.site),
                )
            # Serializes runs of one conversation, so sequence numbers never collide.
            locked = conn.execute(
                "SELECT 1 FROM pw.conversation WHERE id = %s AND site_id = %s FOR UPDATE",
                (conversation_id, owner.site),
            ).fetchone()
            if locked is None:
                raise LookupError("conversation not found")
            conn.execute(
                """
                INSERT INTO pw.copilot_run (id, tenant_id, conversation_id, site_id, user_subject, seq,
                                            question, status, trace_id, agent_id, agent_version,
                                            prompt_version)
                SELECT %s, %s, %s, %s, %s, coalesce(max(seq), 0) + 1, %s, 'RUNNING', %s, %s, %s, %s
                  FROM pw.copilot_run WHERE conversation_id = %s
                """,
                (run_id, owner.tenant, conversation_id, owner.site, owner.subject, question, trace_id,
                 agent_id, agent_version, prompt_version, conversation_id),
            )  # fmt: skip
        return conversation_id, run_id

    def finish_run(self, owner: Owner, run_id: UUID, result: RunOutcome) -> None:
        with self._session(owner) as conn:
            conn.execute(
                """
                UPDATE pw.copilot_run
                   SET status = %s, path = %s, outcome = %s, answer_text = %s, results = %s,
                       suggestions = %s, entities = %s, error_code = %s, finished_at = now()
                 WHERE id = %s AND status = 'RUNNING'
                """,
                (result.status, result.path, result.outcome, result.answer_text, Jsonb(result.results),
                 Jsonb(result.suggestions), Jsonb(result.entities), result.error_code, run_id),
            )  # fmt: skip

    def recent_turns(self, owner: Owner, conversation_id: UUID, limit: int) -> list[Turn]:
        """Completed questions and answers, oldest first (agent spec 5: history)."""
        with self._session(owner) as conn:
            rows = conn.execute(
                """
                SELECT question, answer_text FROM (
                  SELECT seq, question, answer_text FROM pw.copilot_run
                   WHERE conversation_id = %s AND status = 'COMPLETED'
                   ORDER BY seq DESC LIMIT %s) t
                 ORDER BY seq
                """,
                (conversation_id, limit),
            ).fetchall()
        return [Turn(q, a) for q, a in rows]

    def working_set(self, owner: Owner, conversation_id: UUID, since: datetime | None) -> WorkingSet | None:
        """Identifiers of the last successful result in this conversation (semantic spec 7.5), with
        their provenance. Results from before `since` (a simulation reset) do not count."""
        with self._session(owner) as conn:
            row = conn.execute(
                """
                SELECT id, created_at, entities, results -> -1 -> 'normalized_request' ->> 'tool'
                  FROM pw.copilot_run
                 WHERE conversation_id = %s AND status = 'COMPLETED' AND jsonb_array_length(entities) > 0
                 ORDER BY seq DESC LIMIT 1
                """,
                (conversation_id,),
            ).fetchone()
        if row is None or (since is not None and row[1] < since):
            return None
        ids: dict[str, list[UUID]] = {"product": [], "bin": [], "zone": []}
        for entity in row[2][:MAX_ENTITIES]:
            if entity.get("type") in ids:
                ids[entity["type"]].append(UUID(str(entity["id"])))
        return WorkingSet(
            run_id=row[0], tool=row[3], created_at=row[1],
            products=tuple(ids["product"]), bins=tuple(ids["bin"]), zones=tuple(ids["zone"]),
        )  # fmt: skip

    def request_cancel(self, owner: Owner, run_id: UUID) -> bool:
        with self._session(owner) as conn:
            row = conn.execute(
                "UPDATE pw.copilot_run SET cancel_requested = true"
                " WHERE id = %s AND status = 'RUNNING' RETURNING 1",
                (run_id,),
            ).fetchone()
        return row is not None

    def cancel_requested(self, owner: Owner, run_id: UUID) -> bool:
        with self._session(owner) as conn:
            row = conn.execute(
                "SELECT cancel_requested FROM pw.copilot_run WHERE id = %s", (run_id,)
            ).fetchone()
        return bool(row and row[0])

    def conversations(self, owner: Owner, limit: int = 50) -> list[ConversationSummary]:
        with self._session(owner) as conn:
            rows = conn.execute(
                "SELECT id, title, created_at, updated_at FROM pw.conversation WHERE site_id = %s"
                " ORDER BY updated_at DESC LIMIT %s",
                (owner.site, limit),
            ).fetchall()
        return [ConversationSummary(*r) for r in rows]

    def conversation(
        self, owner: Owner, conversation_id: UUID
    ) -> tuple[ConversationSummary, list[RunRecord]] | None:
        with self._session(owner) as conn:
            self._close_interrupted(conn)
            head = conn.execute(
                "SELECT id, title, created_at, updated_at FROM pw.conversation"
                " WHERE id = %s AND site_id = %s",
                (conversation_id, owner.site),
            ).fetchone()
            if head is None:
                return None
            with conn.cursor(row_factory=dict_row) as cur:
                runs = cur.execute(
                    """
                    SELECT id, seq, question, path, status, outcome, answer_text, results, suggestions,
                           trace_id, created_at, finished_at
                      FROM pw.copilot_run WHERE conversation_id = %s ORDER BY seq
                    """,
                    (conversation_id,),
                ).fetchall()
        return ConversationSummary(*head), [RunRecord(**r) for r in runs]

    def add_feedback(self, owner: Owner, run_id: UUID, rating: str, comment: str | None) -> bool:
        """One rating per run; repeating it replaces the earlier one. False for an unknown run."""
        with self._session(owner) as conn:
            run = conn.execute(
                "SELECT trace_id FROM pw.copilot_run WHERE id = %s AND site_id = %s", (run_id, owner.site)
            ).fetchone()
            if run is None:
                return False
            conn.execute(
                """
                INSERT INTO pw.run_feedback (run_id, tenant_id, user_subject, rating, comment, trace_id)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (run_id) DO UPDATE SET rating = EXCLUDED.rating, comment = EXCLUDED.comment,
                                                   created_at = now()
                """,
                (run_id, owner.tenant, owner.subject, rating, comment, run[0]),
            )
        return True


# ---------------------------------------------------------------- usage, budget and rate limits


@dataclass(frozen=True, slots=True, kw_only=True)
class UsageRecord:
    site_id: UUID | None
    run_id: UUID | None
    agent_id: str
    agent_version: str | None
    purpose: str  # WORDING, AGENT, CLASSIFIER, BRIEF
    model: str
    region: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    cost_estimated: Decimal
    latency_ms: int
    outcome: str  # OK, ERROR, CANCELLED
    stage: str  # TOOL_CHOICE, ANSWER_TEXT, WORDING, CLASSIFIER, UNKNOWN (ADR 0001)
    usage_estimated: bool
    brief_id: UUID | None = None  # the daily brief a BRIEF call worded

    def as_json(self) -> dict[str, Any]:
        return {
            k: (str(v) if isinstance(v, UUID | Decimal) else v)
            for k, v in asdict(self).items()
            if v is not None
        }


class UsageLedger:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def reserve(self, tenant: UUID, agent_id: str, day: date, estimate: int, budget: int) -> bool:
        with tenant_session(self._pool, tenant) as conn:
            row = conn.execute(
                "SELECT pw.reserve_budget(%s, %s, %s, %s, %s)", (tenant, agent_id, day, estimate, budget)
            ).fetchone()
        return bool(row and row[0])

    def settle(self, tenant: UUID, agent_id: str, day: date, reserved: int, used: int) -> None:
        with tenant_session(self._pool, tenant) as conn:
            conn.execute(
                "SELECT pw.settle_budget(%s, %s, %s, %s, %s)", (tenant, agent_id, day, reserved, used)
            )

    def record(self, tenant: UUID, usage: UsageRecord) -> None:
        with tenant_session(self._pool, tenant) as conn:
            conn.execute("SELECT pw.record_model_usage(%s, %s)", (tenant, Jsonb(usage.as_json())))


class RateLimiter:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def allow(self, tenant: UUID, subject: str, key: str, per_minute: int) -> bool:
        with tenant_session(self._pool, tenant) as conn:
            row = conn.execute(
                "SELECT pw.rate_limit_hit(%s, %s, %s, %s)", (tenant, subject, key, per_minute)
            ).fetchone()
        return bool(row and row[0])
