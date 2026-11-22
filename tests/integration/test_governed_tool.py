"""The governed query tool through tools.executor (slice-5-acceptance step 3, ADR 0002 sections 5,
6a and 7): the switch, the gate fingerprint, the limits, and the audit record naming the agent."""

from collections.abc import Callable, Generator
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from test_copilot_api import Site
from test_copilot_api import ready_site as ready_site  # this module's own loaded site

from palletwise.db.agent_repo import AgentRegistry
from palletwise.db.audit_repo import AuditLog
from palletwise.db.connection import role_pool
from palletwise.db.copilot_repo import RateLimiter
from palletwise.governed import tool as governed
from palletwise.policy import PolicyClient
from palletwise.settings import PolicySettings
from palletwise.tools.catalog import v1_tools
from palletwise.tools.executor import Outcome, RunContext, ToolExecutor, ToolRegistry

pytestmark = pytest.mark.integration

Connect = Callable[[str], psycopg.Connection]
VALID = (
    "SELECT p.category_name, sum(s.on_hand) AS units FROM gq.stock s"
    " JOIN gq.products p ON s.product_id = p.product_id GROUP BY p.category_name ORDER BY units DESC"
)


@pytest.fixture
def executor(opa_url: str) -> ToolExecutor:
    app = role_pool("PW_APP")
    # The tool itself is under test: registered whether or not a configuration has passed yet
    # (the catalog offers it to the model only after a pass; the per-call check is tested here).
    registry = (
        v1_tools()
        if governed.passed_fingerprints()
        else ToolRegistry([*v1_tools(), governed.RUN_GOVERNED_QUERY])
    )
    return ToolExecutor(
        registry, AgentRegistry(app), PolicyClient(PolicySettings(url=opa_url)), AuditLog(app),
        role_pool("PW_AGENT_READ"), RateLimiter(app),
    )  # fmt: skip


@pytest.fixture
def evaluated(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    """This configuration counts as having passed the gate (the real list is reviewed and committed)."""
    monkeypatch.setattr(governed, "passed_fingerprints", lambda: {"*"})
    real = governed.fingerprint
    monkeypatch.setattr(
        governed, "fingerprint", lambda conn, model, prompt: "*" if real(conn, model, prompt) else ""
    )
    yield


def run_of(site: Site, role: str = "warehouse_manager") -> RunContext:
    return RunContext(
        run_id=uuid4(), trace_id=uuid4().hex, subject=f"governed-{uuid4().hex[:6]}", role=role,
        tenant=site.tenant, sites=(site.site,), site=site.site, model="test-model",
    )  # fmt: skip


def audited(connect_as: Connect, site: Site, run: RunContext) -> dict[str, Any]:
    su = connect_as("postgres")
    row = su.execute(
        "SELECT actor_type, agent_id, agent_version, prompt_version, run_id, user_subject, tool, outcome,"
        " parameters FROM pw.audit_event WHERE tenant_id = %s AND run_id = %s ORDER BY seq DESC LIMIT 1",
        (site.tenant, run.run_id),
    ).fetchone()
    assert row is not None
    keys = ("actor_type", "agent_id", "agent_version", "prompt_version", "run_id", "user_subject", "tool",
            "outcome", "parameters")  # fmt: skip
    return dict(zip(keys, row, strict=True))


def call(executor: ToolExecutor, run: RunContext, sql: str) -> Any:
    return executor.execute(run, "run_governed_query", {"sql": sql, "purpose": "test question"})


def test_an_unevaluated_configuration_does_not_run(
    executor: ToolExecutor, ready_site: Site, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(governed, "passed_fingerprints", set)  # no configuration has passed
    run = run_of(ready_site)
    result = call(executor, run, VALID)
    assert result.outcome is Outcome.VALIDATION_FAILED and "not yet evaluated" in str(result.message)


def test_a_governed_query_runs_labelled_and_audited_with_the_agent(
    executor: ToolExecutor, ready_site: Site, connect_as: Connect, evaluated: None
) -> None:
    run = run_of(ready_site)
    result = call(executor, run, VALID)
    assert result.outcome is Outcome.OK, result.message
    envelope = result.output
    assert envelope.rows and envelope.normalized_request["source"] == "governed_query"
    assert envelope.evidence.how_computed[0].startswith("Answered with a custom query")
    record = audited(connect_as, ready_site, run)
    assert record["actor_type"] == "AGENT" and record["agent_id"] == "copilot" and record["agent_version"]
    assert (
        record["prompt_version"] and record["run_id"] == run.run_id and record["user_subject"] == run.subject
    )
    params = record["parameters"]
    assert (
        params["verdict"] == "RAN"
        and params["sql_hash"]
        and "SELECT" in params["sql"]
        and params["fingerprint"]
    )
    assert params["rows"] == len(envelope.rows) and params["truncated"] is False


def test_refusals_are_audited_with_their_rule(
    executor: ToolExecutor, ready_site: Site, connect_as: Connect, evaluated: None
) -> None:
    run = run_of(ready_site)
    result = call(executor, run, "SELECT sum(unit_value) FROM gq.products")
    assert result.outcome is Outcome.VALIDATION_FAILED and "non_additive" in str(result.message)
    assert audited(connect_as, ready_site, run)["parameters"]["rule"] == "non_additive"


def test_the_limits_hold(
    executor: ToolExecutor, ready_site: Site, evaluated: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    many = call(executor, run_of(ready_site), "SELECT bin_id, product_id, on_hand FROM gq.stock")
    assert many.outcome is Outcome.OK and len(many.output.rows) == 200 and many.output.truncated
    monkeypatch.setattr(governed, "MAX_BYTES", 2000)
    small = call(executor, run_of(ready_site), "SELECT sku, name FROM gq.products")
    assert small.output.truncated and len(small.output.rows) < 200
    monkeypatch.setattr(governed, "TIMEOUT_MS", 1)
    slow = call(executor, run_of(ready_site), "SELECT movement_type, sum(qty) FROM gq.movements GROUP BY 1")
    assert slow.outcome is Outcome.VALIDATION_FAILED and "took too long" in str(slow.message)


def test_a_runtime_error_is_a_refusal_the_model_can_fix(
    executor: ToolExecutor, ready_site: Site, connect_as: Connect, evaluated: None
) -> None:
    run = run_of(ready_site)
    result = call(executor, run, "SELECT count(*) / 0 AS n FROM gq.stock")
    assert result.outcome is Outcome.VALIDATION_FAILED and "division by zero" in str(result.message)
    assert audited(connect_as, ready_site, run)["parameters"]["rule"] == "runtime"


def test_the_switch_and_the_scope_are_checked_per_call(
    executor: ToolExecutor, ready_site: Site, connect_as: Connect, evaluated: None
) -> None:
    su = connect_as("postgres")
    su.execute(
        "UPDATE pw.agent SET governed_query_enabled = false WHERE tenant_id = %s", (ready_site.tenant,)
    )
    su.commit()  # visible to the executor at once
    try:
        off = call(executor, run_of(ready_site), VALID)
        assert off.outcome is Outcome.VALIDATION_FAILED and "switched off" in str(off.message)
    finally:
        su.execute(
            "UPDATE pw.agent SET governed_query_enabled = true WHERE tenant_id = %s", (ready_site.tenant,)
        )
        su.commit()  # visible to the executor at once
    su.execute(
        "UPDATE pw.agent SET allowed_scopes = array_remove(allowed_scopes, 'query.governed')"
        " WHERE tenant_id = %s",
        (ready_site.tenant,),
    )
    su.commit()  # visible to the executor at once
    try:
        denied = call(executor, run_of(ready_site), VALID)
        assert denied.outcome is Outcome.DENIED
    finally:
        su.execute(
            "UPDATE pw.agent SET allowed_scopes = allowed_scopes || ARRAY['query.governed']"
            " WHERE tenant_id = %s",
            (ready_site.tenant,),
        )
        su.commit()  # visible to the executor at once


def test_a_query_cannot_reach_another_sites_rows(
    executor: ToolExecutor, ready_site: Site, evaluated: None
) -> None:
    """The site comes from the session: a bin id of no site in this session matches nothing."""
    result = call(
        executor, run_of(ready_site),
        "SELECT count(*) AS n FROM gq.stock WHERE bin_id = '00000000-0000-0000-0000-000000000000'",
    )  # fmt: skip
    assert result.outcome is Outcome.OK and result.output.rows[0]["n"] == 0
    assert isinstance(ready_site.site, UUID)
