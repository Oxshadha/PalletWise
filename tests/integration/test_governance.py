"""Agent registry, audit chain and tools.executor with the real OPA policy and PostgreSQL.

Identity spec 5, 6 and 8; agent spec 6 and 7; evals spec 15 and 16.
"""

import threading
from collections.abc import Callable, Generator
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from pydantic import BaseModel, ConfigDict, Field
from support import provision_site

from palletwise.db.agent_repo import AgentRegistry
from palletwise.db.audit_repo import AuditEntry, AuditLog, ChainCheck
from palletwise.db.connection import role_pool
from palletwise.policy import PolicyClient
from palletwise.settings import PolicySettings
from palletwise.tools.executor import (
    AuditUnavailable,
    Outcome,
    RunContext,
    RunStopped,
    ToolContext,
    ToolExecutor,
    ToolRegistry,
    ToolSpec,
)

pytestmark = pytest.mark.integration

Connect = Callable[[str], psycopg.Connection]
TRACE = "0123456789abcdef0123456789abcdef"


# ---------------------------------------------------------------- a test tool


class EchoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sku: str = Field(min_length=1, max_length=40)
    limit: int = Field(default=5, ge=1, le=50)


@dataclass(frozen=True)
class EchoOutput:
    role: str
    sku: str
    row_count: int


calls: list[str] = []


def echo(ctx: ToolContext, params: EchoInput) -> EchoOutput:
    calls.append(params.sku)
    row = ctx.conn.execute("SELECT current_user, current_setting('app.tenant_id')").fetchone()
    assert row is not None and row[1] == str(ctx.tenant)
    return EchoOutput(role=row[0], sku=params.sku, row_count=3)


def broken(ctx: ToolContext, params: EchoInput) -> EchoOutput:
    raise RuntimeError("secret internal detail")


TOOLS = ToolRegistry(
    [
        ToolSpec("echo", "test read tool", EchoInput, "inventory.read", "read", echo),
        ToolSpec("echo_orders", "test orders tool", EchoInput, "orders.read", "read", echo),
        ToolSpec("draft", "test propose tool", EchoInput, "proposal.create", "propose", echo),
        ToolSpec("approve", "must never run for an agent", EchoInput, "proposal.approve", "propose", echo),
        ToolSpec("broken", "fails", EchoInput, "inventory.read", "read", broken),
    ]
)


# ---------------------------------------------------------------- fixtures


@dataclass
class Gov:
    tenant: UUID
    site: UUID
    db: psycopg.Connection  # superuser
    executor: ToolExecutor
    audit: AuditLog

    def run(self, role: str = "warehouse_manager", sites: tuple[UUID, ...] | None = None) -> RunContext:
        return RunContext(
            run_id=uuid4(), trace_id=TRACE, subject="user-1", role=role, tenant=self.tenant,
            sites=sites if sites is not None else (self.site,), site=self.site, model="gemini-flash",
        )  # fmt: skip

    def set_agent(self, **values: Any) -> None:
        assignments = ", ".join(f"{k} = %s" for k in values)
        self.db.execute(
            f"UPDATE pw.agent SET {assignments} WHERE tenant_id = %s AND id = 'copilot'",
            (*values.values(), self.tenant),
        )

    def events(self) -> list[dict[str, Any]]:
        with self.db.cursor(row_factory=psycopg.rows.dict_row) as cur:
            return cur.execute(
                "SELECT * FROM pw.audit_event WHERE tenant_id = %s ORDER BY seq", (self.tenant,)
            ).fetchall()


def make_executor(
    opa_url: str, *, audit: AuditLog | None = None, agents: AgentRegistry | None = None
) -> ToolExecutor:
    app = role_pool("PW_APP")
    return ToolExecutor(
        TOOLS,
        agents or AgentRegistry(app),
        PolicyClient(PolicySettings(url=opa_url)),
        audit or AuditLog(app),
        role_pool("PW_AGENT_READ"),
    )


@pytest.fixture
def gov(database: dict[str, Any], connect_as: Connect, opa_url: str) -> Generator[Gov]:
    tenant, site = provision_site(connect_as)
    db = connect_as("postgres")
    db.autocommit = True
    calls.clear()
    yield Gov(tenant, site, db, make_executor(opa_url), AuditLog(role_pool("PW_APP")))


# ---------------------------------------------------------------- registry


def test_every_new_tenant_gets_an_active_copilot(gov: Gov) -> None:
    agent = AgentRegistry(role_pool("PW_APP")).load(gov.tenant, "copilot")
    assert agent is not None and agent.active
    assert agent.allowed_scopes == ("inventory.read", "orders.read", "proposal.create")
    # Flash-Lite words path A answers; the tool-using fallback is the full Flash model.
    assert (agent.primary_model, agent.fallback_model) == ("gemini-flash", "gemini-flash-fallback")
    assert agent.prompt_version == "system.v3"  # question shape and conditions (ADR 0001)


def test_registry_rows_are_tenant_isolated(gov: Gov, connect_as: Connect) -> None:
    other, _ = provision_site(connect_as)
    gov.set_agent(status="SUSPENDED")
    registry = AgentRegistry(role_pool("PW_APP"))
    other_agent = registry.load(other, "copilot")
    assert other_agent is not None and other_agent.active  # one tenant's kill switch only


# ---------------------------------------------------------------- executor


def test_allowed_call_runs_as_the_read_role_and_is_fully_audited(gov: Gov) -> None:
    run = gov.run()
    result = gov.executor.execute(run, "echo", {"sku": "SKU-102"})
    assert result.outcome is Outcome.OK and result.output.role == "pw_agent_read"
    [event] = gov.events()
    assert {k: event[k] for k in (
        "actor_type", "action", "outcome", "user_subject", "user_role", "agent_id", "agent_version",
        "model", "prompt_version", "run_id", "scope", "tool", "parameters", "decision", "deny_reasons",
        "row_count", "trace_id", "site_id")} == {
        "actor_type": "AGENT", "action": "tool.call", "outcome": "OK", "user_subject": "user-1",
        "user_role": "warehouse_manager", "agent_id": "copilot", "agent_version": "1",
        "model": "gemini-flash", "prompt_version": "system.v3", "run_id": run.run_id,
        "scope": "inventory.read", "tool": "echo", "parameters": {"sku": "SKU-102", "limit": 5},
        "decision": "ALLOW", "deny_reasons": None, "row_count": 3, "trace_id": TRACE, "site_id": gov.site,
    }  # fmt: skip
    assert event["latency_ms"] is not None


@pytest.mark.parametrize(
    "arguments",
    [{}, {"sku": ""}, {"sku": "A", "limit": 0}, {"sku": "A", "where": "1=1"}],
    ids=["missing", "empty", "out of range", "unexpected parameter"],
)
def test_invalid_arguments_never_reach_the_handler(gov: Gov, arguments: dict[str, Any]) -> None:
    result = gov.executor.execute(gov.run(), "echo", arguments)
    assert result.outcome is Outcome.VALIDATION_FAILED and result.message and not calls
    [event] = gov.events()
    assert event["outcome"] == "VALIDATION_FAILED" and event["decision"] is None


def test_unknown_tool_is_refused_and_audited(gov: Gov) -> None:
    result = gov.executor.execute(gov.run(), "drop_tables", {"sku": "A"})
    assert result.outcome is Outcome.UNKNOWN_TOOL
    assert gov.events()[0]["outcome"] == "UNKNOWN_TOOL"


def test_scope_the_user_has_but_the_agent_lacks_is_denied(gov: Gov) -> None:
    gov.set_agent(allowed_scopes=["orders.read"])
    result = gov.executor.execute(gov.run(), "echo", {"sku": "A"})
    assert result.outcome is Outcome.DENIED and result.reasons == ("agent_lacks_scope",) and not calls
    [event] = gov.events()
    assert event["decision"] == "DENY" and event["deny_reasons"] == ["agent_lacks_scope"]
    assert gov.executor.execute(gov.run(), "echo_orders", {"sku": "A"}).outcome is Outcome.OK


def test_scope_the_agent_has_but_the_user_lacks_is_denied(gov: Gov) -> None:
    result = gov.executor.execute(gov.run(role="admin"), "draft", {"sku": "A"})
    assert result.outcome is Outcome.DENIED and result.reasons == ("role_lacks_scope",) and not calls


def test_copilot_never_approves_even_if_the_registry_lists_it(gov: Gov) -> None:
    gov.set_agent(allowed_scopes=["inventory.read", "proposal.approve"])
    result = gov.executor.execute(gov.run(), "approve", {"sku": "A"})
    assert result.outcome is Outcome.DENIED and result.reasons == ("agent_forbidden_scope",) and not calls


def test_site_outside_the_session_is_denied(gov: Gov) -> None:
    result = gov.executor.execute(gov.run(sites=(uuid4(),)), "echo", {"sku": "A"})
    assert result.outcome is Outcome.DENIED and result.reasons == ("site_not_allowed",) and not calls


def test_agent_suspended_during_a_run_stops_the_next_call(gov: Gov) -> None:
    run = gov.run()
    assert gov.executor.execute(run, "echo", {"sku": "A"}).outcome is Outcome.OK
    gov.set_agent(status="SUSPENDED")
    with pytest.raises(RunStopped) as stopped:
        gov.executor.execute(run, "echo", {"sku": "B"})
    assert stopped.value.reason == "suspended" and calls == ["A"]
    assert [e["outcome"] for e in gov.events()] == ["OK", "SUSPENDED"]


def test_unreachable_policy_denies(gov: Gov) -> None:
    def down(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("opa down")

    executor = ToolExecutor(
        TOOLS, AgentRegistry(role_pool("PW_APP")),
        PolicyClient(PolicySettings(url="http://opa.invalid"), transport=httpx.MockTransport(down)),
        AuditLog(role_pool("PW_APP")), role_pool("PW_AGENT_READ"),
    )  # fmt: skip
    result = executor.execute(gov.run(), "echo", {"sku": "A"})
    assert result.outcome is Outcome.DENIED and result.reasons == ("policy_unavailable",) and not calls


def test_malformed_policy_answer_denies(gov: Gov) -> None:
    def malformed(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": {"allow": "yes"}})

    executor = ToolExecutor(
        TOOLS, AgentRegistry(role_pool("PW_APP")),
        PolicyClient(PolicySettings(url="http://opa.invalid"), transport=httpx.MockTransport(malformed)),
        AuditLog(role_pool("PW_APP")), role_pool("PW_AGENT_READ"),
    )  # fmt: skip
    assert executor.execute(gov.run(), "echo", {"sku": "A"}).outcome is Outcome.DENIED and not calls


def test_unreachable_registry_stops_the_run(gov: Gov, opa_url: str) -> None:
    class DownRegistry(AgentRegistry):
        def load(self, tenant: UUID, agent_id: str) -> Any:
            raise psycopg.OperationalError("database down")

    executor = make_executor(opa_url, agents=DownRegistry(role_pool("PW_APP")))
    with pytest.raises(RunStopped) as stopped:
        executor.execute(gov.run(), "echo", {"sku": "A"})
    assert stopped.value.reason == "registry_unavailable" and not calls


def test_handler_failure_is_audited_without_leaking_details(gov: Gov) -> None:
    result = gov.executor.execute(gov.run(), "broken", {"sku": "A"})
    assert result.outcome is Outcome.ERROR and result.message and "secret" not in result.message
    assert gov.events()[0]["outcome"] == "ERROR"


def test_no_result_leaves_the_executor_unaudited(gov: Gov, opa_url: str) -> None:
    class DownAudit(AuditLog):
        def append(self, tenant: UUID, entry: AuditEntry) -> UUID:
            raise psycopg.OperationalError("database down")

    executor = make_executor(opa_url, audit=DownAudit(role_pool("PW_APP")))
    with pytest.raises(AuditUnavailable):
        executor.execute(gov.run(), "echo", {"sku": "A"})


def test_tool_input_models_must_forbid_extra_fields() -> None:
    class Loose(BaseModel):
        sku: str

    with pytest.raises(ValueError, match="forbid extra"):
        ToolSpec("loose", "x", Loose, "inventory.read", "read", echo)  # type: ignore[arg-type]


# ---------------------------------------------------------------- audit chain


def entry(n: int) -> AuditEntry:
    return AuditEntry(actor_type="SYSTEM", action="test", outcome="OK", trace_id=TRACE, row_count=n,
                      parameters={"b": [1, 2.5, None, True], "a": "x\"y"})  # fmt: skip


def test_chain_verifies_and_detects_modified_missing_and_reordered_events(gov: Gov) -> None:
    for n in range(5):
        gov.audit.append(gov.tenant, entry(n))
    check = gov.audit.verify(gov.tenant)
    assert check.ok and check.checked == 5

    def tamper(*statements: str) -> Any:
        """Tampers inside a transaction, verifies there, then rolls back."""
        gov.db.execute("BEGIN")
        gov.db.execute("SET LOCAL session_replication_role = replica")  # bypass the append-only trigger
        for statement in statements:
            gov.db.execute(statement, (gov.tenant,))
        gov.db.execute("SELECT set_config('app.tenant_id', %s, true)", (str(gov.tenant),))
        result = gov.db.execute("SELECT * FROM pw.verify_audit_chain(%s)", (gov.tenant,)).fetchone()
        gov.db.execute("ROLLBACK")
        return ChainCheck(*result)

    modified = tamper("UPDATE pw.audit_event SET row_count = 99 WHERE tenant_id = %s AND seq = 3")
    assert not modified.ok and (modified.bad_seq, modified.problem) == (3, "modified")
    missing = tamper("DELETE FROM pw.audit_event WHERE tenant_id = %s AND seq = 2")
    assert not missing.ok and (missing.bad_seq, missing.problem) == (2, "missing")
    last = tamper("DELETE FROM pw.audit_event WHERE tenant_id = %s AND seq = 5")
    assert not last.ok and last.problem == "missing"
    swap = "UPDATE pw.audit_event SET seq = {new} WHERE tenant_id = %s AND seq = {old}"
    reordered = tamper(swap.format(new=100, old=2), swap.format(new=2, old=3), swap.format(new=3, old=100))
    assert not reordered.ok and (reordered.bad_seq, reordered.problem) == (2, "broken link")
    assert gov.audit.verify(gov.tenant).ok  # every tamper was rolled back


def test_concurrent_appends_form_one_linear_chain(gov: Gov) -> None:
    threads = [threading.Thread(target=gov.audit.append, args=(gov.tenant, entry(n))) for n in range(24)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check = gov.audit.verify(gov.tenant)
    assert check.ok and check.checked == 24


def test_chains_are_per_tenant(gov: Gov, connect_as: Connect) -> None:
    other, _ = provision_site(connect_as)
    gov.audit.append(gov.tenant, entry(1))
    gov.audit.append(other, entry(1))
    assert gov.audit.verify(gov.tenant).checked == 1 and gov.audit.verify(other).checked == 1


def test_unknown_audit_fields_are_refused(gov: Gov, connect_as: Connect) -> None:
    conn = connect_as("pw_app")
    conn.execute("SELECT set_config('app.tenant_id', %s, false)", (str(gov.tenant),))
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        conn.execute(
            "SELECT pw.append_audit(%s, %s::jsonb)",
            (
                gov.tenant,
                '{"actor_type":"SYSTEM","action":"x","outcome":"OK","trace_id":"' + TRACE + '","hash":"x"}',
            ),
        )


@pytest.mark.parametrize(
    "role", ["pw_app", "pw_ingest", "pw_evaluator", "pw_proposal_writer", "pw_workflow", "pw_sim_admin",
             "pw_agent_read", "pw_memory"],
)  # fmt: skip
def test_no_runtime_role_can_write_audit_directly(gov: Gov, connect_as: Connect, role: str) -> None:
    gov.audit.append(gov.tenant, entry(1))
    conn = connect_as(role)
    conn.autocommit = True
    conn.execute("SELECT set_config('app.tenant_id', %s, false)", (str(gov.tenant),))
    for statement in (
        "UPDATE pw.audit_event SET outcome = 'X'",
        "DELETE FROM pw.audit_event",
        "INSERT INTO pw.audit_chain_head (tenant_id) VALUES (gen_random_uuid())",
        "UPDATE pw.audit_chain_head SET last_seq = 0",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(statement)


def test_even_the_owner_cannot_update_or_delete_audit(gov: Gov) -> None:
    gov.audit.append(gov.tenant, entry(1))
    for statement in (
        "UPDATE pw.audit_event SET outcome = 'X'",
        "DELETE FROM pw.audit_event",
        "TRUNCATE pw.audit_event",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            gov.db.execute(statement)


def test_canonical_serialization(gov: Gov) -> None:
    row = gov.db.execute(
        """SELECT pw.canonical_json('{"b": 1.50, "a": [true, null, "x\\"y"], "é": {"z": 0, "y": -2}}')"""
    ).fetchone()
    assert row == ('{"a":[true,null,"x\\"y"],"b":"1.50","é":{"y":"-2","z":"0"}}',)
