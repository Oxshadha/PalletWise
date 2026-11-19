"""Copilot runs through the real API, PostgreSQL roles, OPA policy and tools.executor.

Agent spec 3, 9.1, 12, 14 (Slice 1 acceptance); identity spec 5; evals spec 14 and 15. The
models are deterministic Pydantic AI function models; Gemini itself is exercised by the model
gate of the evaluation suite.
"""

import base64
import json
import os
import threading
import time
from collections.abc import Callable, Generator
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from support import load_site, provision_site

from evals.fake_idp import CLIENT_ID, CLIENT_SECRET, ISSUER, SIGNING_KEY, FakeIdp
from palletwise.agent.runtime import BUDGET_TEXT, CopilotRuntime
from palletwise.api import copilot, sites
from palletwise.api.app import create_app
from palletwise.auth.crypto import KeyRing
from palletwise.auth.oidc import OidcClient
from palletwise.auth.service import AuthService
from palletwise.db import dataset_hash, site_repo
from palletwise.db.agent_repo import AgentRegistry
from palletwise.db.audit_repo import AuditLog
from palletwise.db.connection import role_pool
from palletwise.db.copilot_repo import ConversationStore, RateLimiter, UsageLedger
from palletwise.db.session_repo import SessionStore
from palletwise.llm import ResolvedModel
from palletwise.policy import PolicyClient
from palletwise.settings import OidcSettings, PolicySettings, SessionSettings, WebSettings
from palletwise.tools.catalog import v1_tools
from palletwise.tools.executor import ToolExecutor

pytestmark = pytest.mark.integration

Connect = Callable[[str], psycopg.Connection]
ORIGIN = "https://testserver"


class Models:
    """An LLM provider whose models are set per test."""

    def __init__(self, **models: FunctionModel) -> None:
        self.models = models

    def model(self, logical: str) -> ResolvedModel | None:
        found = self.models.get(logical.replace("-", "_"))
        return ResolvedModel(found, f"test-{logical}", "test") if found else None


def tool_then_say(text: str) -> FunctionModel:
    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if not any(isinstance(p, ToolReturnPart) for m in messages for p in getattr(m, "parts", [])):
            return ModelResponse(
                parts=[ToolCallPart("find_product_locations", {"product_query": "wireless mouse"})]
            )
        return ModelResponse(parts=[TextPart(text)])

    return FunctionModel(fn)


@dataclass(frozen=True)
class Site:
    tenant: UUID
    site: UUID


@pytest.fixture(scope="module")
def ready_site(connect_session: Connect) -> Site:
    tenant = uuid4()
    site, _, run = load_site(connect_session, tenant)
    admin = connect_session("pw_sim_admin")
    digest, _ = dataset_hash.compute(admin, tenant, site)
    site_repo.complete_load(admin, tenant, site, run, digest)
    return Site(tenant, site)


@dataclass
class Env:
    idp: FakeIdp
    client: TestClient
    site: Site
    db: psycopg.Connection
    models: Models

    def sign_in(
        self, subject: str = "user-1", role: str = "warehouse_manager", sites: list[UUID] | None = None
    ) -> None:
        self.idp.users[subject] = {
            "pw_roles": [role],
            "pw_tenant": str(self.site.tenant),
            "pw_sites": [str(s) for s in (sites or [self.site.site])],
            "name": "Demo",
        }
        self.client.cookies.clear()
        start = self.client.get("/api/auth/login", follow_redirects=False)
        code = self.idp.authorize(start.headers["location"], subject)
        state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
        done = self.client.get(
            f"/api/auth/callback?{urlencode({'state': state, 'code': code})}", follow_redirects=False
        )
        assert done.headers["location"] == "/"

    def ask(self, question: str, **extra: Any) -> tuple[int, dict[str, Any], Any]:
        body = {"site_id": str(extra.pop("site_id", self.site.site)), "question": question, **extra}
        with self.client.stream(
            "POST", "/api/copilot/runs", json=body, headers={"Origin": ORIGIN}
        ) as response:
            if response.status_code != 200:
                response.read()
                return response.status_code, dict(response.headers), response.json()
            events = [json.loads(line[5:]) for line in response.iter_lines() if line.startswith("data:")]
            return 200, dict(response.headers), events

    def set_agent(self, **values: Any) -> None:
        assignments = ", ".join(f"{k} = %s" for k in values)
        self.db.execute(
            f"UPDATE pw.agent SET {assignments} WHERE tenant_id = %s",
            (*values.values(), self.site.tenant),
        )


@pytest.fixture
def env(ready_site: Site, connect_session: Connect, opa_url: str) -> Generator[Env]:
    idp, models = FakeIdp(), Models()
    oidc = OidcClient(
        OidcSettings(
            public_url=ISSUER,
            internal_url="http://idp.internal/realms/palletwise",
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
        ),
        WebSettings(public_origin=ORIGIN),
        signing_key=lambda _: SIGNING_KEY.public_key(),
        transport=httpx.MockTransport(idp.handle),
    )
    app_pool = role_pool("PW_APP")
    sessions = SessionSettings(keys="k1:" + base64.b64encode(os.urandom(32)).decode())
    auth = AuthService(oidc, KeyRing(sessions.keys), sessions, SessionStore(app_pool), AuditLog(app_pool))
    agents, audit, limiter = AgentRegistry(app_pool), AuditLog(app_pool), RateLimiter(app_pool)
    executor = ToolExecutor(
        v1_tools(),
        agents,
        PolicyClient(PolicySettings(url=opa_url)),
        audit,
        role_pool("PW_AGENT_READ"),
        limiter,
    )
    services = copilot.CopilotServices(
        runtime=CopilotRuntime(v1_tools(), executor, models, run_timeout=30),
        agents=agents,
        conversations=ConversationStore(role_pool("PW_MEMORY")),
        ledger=UsageLedger(app_pool),
        limiter=limiter,
        audit=audit,
        app_pool=app_pool,
    )
    reads = sites.ReadServices(
        PolicyClient(PolicySettings(url=opa_url)), audit, app_pool, role_pool("PW_AGENT_READ")
    )
    client = TestClient(create_app(auth, WebSettings(public_origin=ORIGIN), services, reads), base_url=ORIGIN)
    db = connect_session("postgres")
    db.autocommit = True
    env = Env(idp, client, ready_site, db, models)
    yield env
    env.set_agent(
        status="ACTIVE",
        daily_token_budget=2_000_000,
        allowed_scopes=["inventory.read", "orders.read", "proposal.create"],
    )


def of_type(events: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [e for e in events if e["type"] == kind]


def answer(events: list[dict[str, Any]]) -> str:
    return "".join(e["delta"] for e in of_type(events, "TEXT_MESSAGE_CONTENT"))


# ---------------------------------------------------------------- Slice 1 acceptance


def test_where_is_sku_102_on_path_b_with_highlights_stored_and_audited(env: Env) -> None:
    env.sign_in()
    status, headers, events = env.ask("Where is product SKU-102 stored?")
    assert status == 200
    run_id = UUID(headers["x-run-id"])
    assert events[0]["type"] == "RUN_STARTED" and events[0]["path"] == "B"
    [result] = of_type(events, "pw.result")
    bins = env.db.execute(
        """SELECT b.id FROM pw.inventory_balance ib JOIN pw.product p ON p.id = ib.product_id
           JOIN pw.bin b ON b.id = ib.bin_id
           WHERE ib.tenant_id = %s AND p.sku = 'SKU-102' AND (ib.on_hand > 0 OR ib.allocated > 0)""",
        (env.site.tenant,),
    ).fetchall()
    assert {h["target_id"] for h in result["result"]["highlights"]} == {str(b[0]) for b in bins}
    order = [e["type"] for e in events]
    assert order.index("pw.result") < order.index("TEXT_MESSAGE_CONTENT") and order[-1] == "RUN_FINISHED"
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    assert answer(events).startswith("SKU-102 is stored in")
    assert of_type(events, "pw.suggestions")[0]["suggestions"][0]["tool"] == "get_bin_contents"

    stored = env.client.get(
        f"/api/copilot/conversations/{headers['x-conversation-id']}", params={"site_id": str(env.site.site)}
    ).json()
    [run] = stored["runs"]
    assert run["status"] == "COMPLETED" and run["path"] == "B" and run["outcome"] == "ANSWER"
    assert run["results"][0]["highlights"] == result["result"]["highlights"]

    audit = env.db.execute(
        "SELECT action, tool, decision, outcome FROM pw.audit_event WHERE run_id = %s ORDER BY seq", (run_id,)
    ).fetchall()
    assert audit == [
        ("tool.call", "find_product_locations", "ALLOW", "OK"),
        ("copilot.run", None, None, "ANSWER"),
    ]
    assert env.idp.exchanges >= 1  # the copilot acted through a validated token exchange


def test_the_chip_uses_path_a_and_without_a_model_path_b(env: Env) -> None:
    env.sign_in()
    chip = {
        "label": "Where is SKU-102 stored?",
        "tool": "find_product_locations",
        "arguments": {"sku": "SKU-102"},
    }
    env.models.models["gemini_flash_lite"] = FunctionModel(
        lambda m, i: ModelResponse(parts=[TextPart("SKU-102 is mostly in A8-01-02.")])
    )
    _, _, with_model = env.ask(chip["label"], suggestion=chip)
    assert with_model[0]["path"] == "A"
    env.models.models.clear()
    _, _, without = env.ask(chip["label"], suggestion=chip)
    assert without[0]["path"] == "B" and answer(without).startswith("SKU-102 is stored in")


def test_model_path_records_usage_and_budget(env: Env) -> None:
    env.sign_in()
    env.models.models["gemini_flash"] = tool_then_say("The wireless mouse is SKU-102.")
    status, headers, events = env.ask("Which bins hold the wireless mouse?")
    assert status == 200 and events[0]["path"] == "C" and answer(events) == "The wireless mouse is SKU-102."
    usage = env.db.execute(
        "SELECT purpose, model, outcome FROM pw.model_usage WHERE run_id = %s", (UUID(headers["x-run-id"]),)
    ).fetchall()
    assert usage == [("AGENT", "test-gemini-flash", "OK")] * 2
    budget = env.db.execute(
        "SELECT reserved, used FROM pw.agent_budget_day WHERE tenant_id = %s", (env.site.tenant,)
    ).fetchone()
    assert budget is not None and budget[0] == 0  # every reservation settled


def test_budget_exhausted_falls_back_to_the_template_and_says_so(env: Env) -> None:
    env.sign_in()
    env.set_agent(daily_token_budget=1)
    env.models.models["gemini_flash_lite"] = FunctionModel(lambda m, i: ModelResponse(parts=[TextPart("x")]))
    _, _, events = env.ask("Where is SKU-102 stored?")
    assert answer(events).endswith(BUDGET_TEXT)
    assert of_type(events, "RUN_FINISHED")[0]["path"] == "B"


def test_a_role_without_inventory_read_on_the_agent_is_denied_and_audited(env: Env) -> None:
    env.sign_in()
    env.set_agent(allowed_scopes=["orders.read"])
    _, headers, events = env.ask("Where is SKU-102 stored?")
    assert of_type(events, "RUN_FINISHED")[0]["outcome"] == "DENIED" and not of_type(events, "pw.result")
    decision = env.db.execute(
        "SELECT decision, deny_reasons FROM pw.audit_event WHERE run_id = %s AND action = 'tool.call'",
        (UUID(headers["x-run-id"]),),
    ).fetchone()
    assert decision == ("DENY", ["agent_lacks_scope"])


# ---------------------------------------------------------------- pre-checks (agent spec 3.1)


def test_suspended_copilot_refuses_every_question(env: Env) -> None:
    env.sign_in()
    env.set_agent(status="SUSPENDED")
    status, _, body = env.ask("Where is SKU-102 stored?")
    assert status == 503 and body["code"] == "COPILOT_SUSPENDED" and body["trace_id"]


def test_site_not_ready_is_refused(env: Env, connect_session: Connect) -> None:
    _, loading_site = provision_site(connect_session, tenant=env.site.tenant, site_code="LDG")
    env.sign_in(sites=[env.site.site, loading_site])
    status, _, body = env.ask("Where is SKU-102 stored?", site_id=loading_site)
    assert status == 503 and body["code"] == "DATA_NOT_READY"


def test_a_site_outside_the_session_is_not_found(env: Env) -> None:
    env.sign_in()
    status, _, body = env.ask("Where is SKU-102 stored?", site_id=uuid4())
    assert status == 404 and body["code"] == "NOT_FOUND"


def test_too_long_and_empty_questions_are_rejected(env: Env) -> None:
    env.sign_in()
    assert env.ask("x" * 1_001)[0] == 422
    assert env.ask(" " + chr(0x200B) + " ")[0] == 422


def test_refused_delegation_denies_the_run(env: Env) -> None:
    env.sign_in()
    env.idp.refuse_exchange = True
    status, _, body = env.ask("Where is SKU-102 stored?")
    assert status == 403 and body["code"] == "FORBIDDEN"


def test_runs_are_rate_limited(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    env.sign_in(subject=f"limited-{uuid4()}")
    monkeypatch.setattr(copilot, "RUNS_PER_MINUTE", 2)
    assert env.ask("Where is SKU-102 stored?")[0] == 200
    assert env.ask("Where is SKU-102 stored?")[0] == 200
    status, headers, body = env.ask("Where is SKU-102 stored?")
    assert status == 429 and body["code"] == "RATE_LIMITED" and headers["retry-after"] == "60"


def test_needs_a_session_and_our_origin(env: Env) -> None:
    env.client.cookies.clear()
    body = {"site_id": str(env.site.site), "question": "Where is SKU-102 stored?"}
    assert env.client.post("/api/copilot/runs", json=body, headers={"Origin": ORIGIN}).status_code == 401
    env.sign_in()
    assert (
        env.client.post(
            "/api/copilot/runs", json=body, headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )


# ---------------------------------------------------------------- conversations, feedback, cancel


def test_follow_up_continues_the_conversation_and_others_cannot_see_it(env: Env) -> None:
    env.sign_in(subject="owner")
    _, headers, _ = env.ask("Where is SKU-102 stored?")
    conversation = headers["x-conversation-id"]
    _, again, _ = env.ask("Where is SKU-102 stored?", conversation_id=conversation)
    assert again["x-conversation-id"] == conversation
    params = {"site_id": str(env.site.site)}
    listed = env.client.get("/api/copilot/conversations", params=params).json()
    assert listed[0]["id"] == conversation and listed[0]["title"] == "Where is SKU-102 stored?"
    assert (
        len(env.client.get(f"/api/copilot/conversations/{conversation}", params=params).json()["runs"]) == 2
    )

    env.sign_in(subject="someone-else")
    assert env.client.get(f"/api/copilot/conversations/{conversation}", params=params).status_code == 404
    assert env.ask("Where is SKU-102 stored?", conversation_id=conversation)[0] == 404
    assert all(
        c["id"] != conversation for c in env.client.get("/api/copilot/conversations", params=params).json()
    )


def test_feedback_is_stored_with_the_trace_id(env: Env) -> None:
    env.sign_in()
    _, headers, _ = env.ask("Where is SKU-102 stored?")
    run_id = headers["x-run-id"]
    response = env.client.post(
        f"/api/copilot/runs/{run_id}/feedback",
        params={"site_id": str(env.site.site)},
        json={"rating": "NOT_HELPFUL", "comment": "wrong bin"},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 204
    row = env.db.execute(
        "SELECT rating, comment, trace_id FROM pw.run_feedback WHERE run_id = %s", (run_id,)
    ).fetchone()
    assert row == ("NOT_HELPFUL", "wrong bin", headers["x-trace-id"])


def test_cancel_stops_a_running_answer(env: Env) -> None:
    env.sign_in()
    release = threading.Event()

    async def slow(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        import asyncio

        await asyncio.sleep(20)
        return ModelResponse(parts=[TextPart("too late")])

    env.models.models["gemini_flash"] = FunctionModel(slow)
    result: dict[str, Any] = {}

    def ask() -> None:
        result["value"] = env.ask("Tell me something about the warehouse")
        release.set()

    worker = threading.Thread(target=ask)
    worker.start()
    deadline = time.monotonic() + 10
    run_id = None
    while run_id is None and time.monotonic() < deadline:
        row = env.db.execute(
            "SELECT id FROM pw.copilot_run WHERE tenant_id = %s AND status = 'RUNNING'"
            " ORDER BY created_at DESC LIMIT 1",
            (env.site.tenant,),
        ).fetchone()
        run_id = row[0] if row else None
        time.sleep(0.1)
    assert run_id is not None
    cancel = env.client.post(
        f"/api/copilot/runs/{run_id}/cancel",
        params={"site_id": str(env.site.site)},
        headers={"Origin": ORIGIN},
    )
    assert cancel.status_code == 204
    assert release.wait(10), "the run did not stop"
    worker.join()
    status = env.db.execute("SELECT status FROM pw.copilot_run WHERE id = %s", (run_id,)).fetchone()
    assert status == ("CANCELLED",)


def test_an_admin_cannot_read_a_managers_conversation(env: Env) -> None:
    env.sign_in(subject="manager-x")
    _, headers, _ = env.ask("Where is SKU-102 stored?")
    env.sign_in(subject="admin-x", role="admin")
    params = {"site_id": str(env.site.site)}
    assert (
        env.client.get(
            f"/api/copilot/conversations/{headers['x-conversation-id']}", params=params
        ).status_code
        == 404
    )


def test_the_database_refuses_a_run_in_another_users_conversation(env: Env, connect_session: Connect) -> None:
    env.sign_in(subject="victim")
    _, headers, _ = env.ask("Where is SKU-102 stored?")
    memory = connect_session("pw_memory")
    with memory.transaction():
        memory.execute("SELECT set_config('app.tenant_id', %s, true)", (str(env.site.tenant),))
        memory.execute("SELECT set_config('app.user_subject', 'attacker', true)")
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            memory.execute(
                "INSERT INTO pw.copilot_run (id, tenant_id, conversation_id, site_id, user_subject, seq,"
                " question, status, trace_id, agent_id) VALUES (gen_random_uuid(), %s, %s, %s, 'attacker',"
                " 99, 'x', 'RUNNING', %s, 'copilot')",
                (env.site.tenant, headers["x-conversation-id"], env.site.site, "ab" * 16),
            )


def test_a_run_left_running_by_a_dead_process_is_closed(env: Env) -> None:
    env.sign_in(subject="crashed")
    _, headers, _ = env.ask("Where is SKU-102 stored?")
    run_id = headers["x-run-id"]
    env.db.execute(
        "UPDATE pw.copilot_run SET status = 'RUNNING', finished_at = NULL,"
        " created_at = now() - interval '10 minutes' WHERE id = %s",
        (run_id,),
    )
    stored = env.client.get(
        f"/api/copilot/conversations/{headers['x-conversation-id']}", params={"site_id": str(env.site.site)}
    ).json()
    assert stored["runs"][0]["status"] == "FAILED"
    assert env.db.execute("SELECT error_code FROM pw.copilot_run WHERE id = %s", (run_id,)).fetchone() == (
        "INTERRUPTED",
    )


# ---------------------------------------------------------------- map endpoints (UX spec 3 to 5)


def test_layout_is_generated_from_location_data_and_cached_by_etag(env: Env) -> None:
    env.sign_in()
    response = env.client.get(f"/api/sites/{env.site.site}/layout")
    assert response.status_code == 200
    layout = response.json()
    counts = env.db.execute(
        """SELECT (SELECT count(*) FROM pw.zone WHERE site_id = %(s)s AND is_active),
                  (SELECT count(*) FROM pw.rack WHERE site_id = %(s)s AND is_active),
                  (SELECT count(*) FROM pw.bin WHERE site_id = %(s)s AND is_active)""",
        {"s": env.site.site},
    ).fetchone()
    assert (len(layout["zones"]), len(layout["racks"]), len(layout["bins"])) == counts
    assert layout["site"]["code"] == "PLG" and layout["zones"][0]["outline"]
    again = env.client.get(
        f"/api/sites/{env.site.site}/layout", headers={"If-None-Match": response.headers["etag"]}
    )
    assert again.status_code == 304


def test_a_deactivated_bin_leaves_the_map_without_code_changes(env: Env) -> None:
    env.sign_in()
    code = env.client.get(f"/api/sites/{env.site.site}/layout").json()["bins"][0]["code"]
    env.db.execute(
        "UPDATE pw.bin SET is_active = false WHERE site_id = %s AND code = %s", (env.site.site, code)
    )
    try:
        codes = {b["code"] for b in env.client.get(f"/api/sites/{env.site.site}/layout").json()["bins"]}
        assert code not in codes
    finally:
        env.db.execute(
            "UPDATE pw.bin SET is_active = true WHERE site_id = %s AND code = %s", (env.site.site, code)
        )


def test_map_state_comes_from_the_semantic_layer(env: Env) -> None:
    env.sign_in()
    state = env.client.get(f"/api/sites/{env.site.site}/map-state").json()
    empty = env.db.execute(
        """SELECT b.id FROM pw.bin b WHERE b.site_id = %s AND b.is_active AND NOT EXISTS (
             SELECT 1 FROM pw.inventory_balance ib WHERE ib.bin_id = b.id AND ib.on_hand > 0)""",
        (env.site.site,),
    ).fetchall()
    assert {b["bin_id"] for b in state["bins"] if b["state"] == "EMPTY"} == {str(r[0]) for r in empty}
    clock = env.db.execute(
        "SELECT sim_time FROM pw.site_clock WHERE site_id = %s", (env.site.site,)
    ).fetchone()
    assert clock is not None and datetime.fromisoformat(state["as_of"]) == clock[0]


def test_bin_details_use_the_same_definition_as_the_copilot(env: Env) -> None:
    env.sign_in()
    _, _, events = env.ask("Where is SKU-102 stored?")
    code = of_type(events, "pw.result")[0]["result"]["rows"][0]["bin_code"]
    details = env.client.get(f"/api/sites/{env.site.site}/bins/{code}").json()
    assert details["status"] == "OK" and "SKU-102" in {r["sku"] for r in details["rows"]}
    assert env.client.get(f"/api/sites/{env.site.site}/bins/Z9-99-99").status_code == 404
    assert env.client.get(f"/api/sites/{env.site.site}/bins/bad%20code").status_code == 422


def test_map_endpoints_are_site_scoped_and_fail_closed(env: Env) -> None:
    env.sign_in()
    other = uuid4()
    for path in ("layout", "map-state", "bins/A1-01-01"):
        assert env.client.get(f"/api/sites/{other}/{path}").status_code == 404
    down = httpx.MockTransport(lambda _: httpx.Response(503))
    env.client.app.state.reads = sites.ReadServices(  # type: ignore[attr-defined]
        PolicyClient(PolicySettings(url="http://opa.invalid"), transport=down),
        AuditLog(role_pool("PW_APP")),
        role_pool("PW_APP"),
        role_pool("PW_AGENT_READ"),
    )
    response = env.client.get(f"/api/sites/{env.site.site}/layout")
    assert response.status_code == 403 and response.json()["code"] == "FORBIDDEN"
    denied = env.db.execute(
        "SELECT deny_reasons FROM pw.audit_event WHERE tenant_id = %s AND action = 'map.layout'"
        " ORDER BY seq DESC LIMIT 1",
        (env.site.tenant,),
    ).fetchone()
    assert denied == (["policy_unavailable"],)


def test_starters_come_from_the_server_and_only_name_built_tools(env: Env) -> None:
    env.sign_in()
    starters = env.client.get("/api/copilot/starters").json()
    assert starters == [
        {
            "label": "Where is SKU-102 stored?",
            "tool": "find_product_locations",
            "arguments": {"sku": "SKU-102"},
        }
    ]
    status = env.client.get(f"/api/sites/{env.site.site}/status").json()
    assert status["time_zone"] == "Asia/Colombo"


# ---------------------------------------------------------------- traces (evals spec 10, 16)


def test_a_model_run_is_one_trace_with_every_span_correctly_parented(env: Env, exported_spans: Any) -> None:
    env.sign_in()
    env.models.models["gemini_flash"] = tool_then_say("The wireless mouse is SKU-102.")
    exported_spans.clear()
    status, headers, _ = env.ask("Which bins hold the wireless mouse?")
    assert status == 200
    trace_id = headers["x-trace-id"]
    mine = [s for s in exported_spans.get_finished_spans() if format(s.context.trace_id, "032x") == trace_id]
    names = [s.name for s in mine]
    for expected in (
        "http.server",
        "copilot.run",
        "gen_ai.chat",
        "tool.execute",
        "policy.decision",
        "db.query",
    ):
        assert expected in names, (expected, names)
    by_id = {s.context.span_id: s for s in mine}

    def parent(span: Any) -> str | None:
        return by_id[span.parent.span_id].name if span.parent and span.parent.span_id in by_id else None

    def ancestors(span: Any) -> list[str]:
        found: list[str] = []
        while span.parent and span.parent.span_id in by_id:
            span = by_id[span.parent.span_id]
            found.append(span.name)
        return found

    run_span = next(s for s in mine if s.name == "copilot.run")
    assert ancestors(run_span) == ["http.server"]
    assert names.count("http.server") == 1 and not [n for n in names if n.startswith(("POST", "fastapi"))]
    for span in mine:
        expected_parent = {
            "gen_ai.chat": "copilot.run",
            "tool.execute": "copilot.run",
            "policy.decision": "tool.execute",
            "db.query": "tool.execute",
        }.get(span.name)
        if expected_parent:
            assert parent(span) == expected_parent, (span.name, parent(span))
    run = next(s for s in mine if s.name == "copilot.run")
    attributes = dict(run.attributes or {})
    assert attributes["pw.run.path"] == "C" and attributes["pw.run.outcome"] == "ANSWER"
    assert attributes["pw.user.hash"].startswith("h:") and "manager" not in attributes["pw.user.hash"]
    chat = next(s for s in mine if s.name == "gen_ai.chat")
    assert dict(chat.attributes or {})["gen_ai.usage.input_tokens"] >= 0
    tool = next(s for s in mine if s.name == "tool.execute")
    assert dict(tool.attributes or {})["pw.tool.highlight_count"] >= 1
    # Never exported: no cookie or session value appears anywhere in the run's exported spans.
    exported_text = repr([(s.name, dict(s.attributes or {}), [dict(e.attributes or {}) for e in s.events])
                          for s in mine])  # fmt: skip
    secrets_seen = [v for v in env.client.cookies.values() if v and v in exported_text]
    assert not secrets_seen and "SELECT" not in exported_text
    # The audit entries carry the same trace id (NFR-OBS-03).
    audited = env.db.execute(
        "SELECT DISTINCT trace_id FROM pw.audit_event WHERE run_id = %s", (UUID(headers["x-run-id"]),)
    ).fetchall()
    assert audited == [(trace_id,)]


def test_an_error_response_carries_the_trace_id_of_its_request(env: Env, exported_spans: Any) -> None:
    env.sign_in()
    env.set_agent(status="SUSPENDED")
    exported_spans.clear()
    status, _, body = env.ask("Where is SKU-102 stored?")
    assert status == 503
    servers = [s for s in exported_spans.get_finished_spans() if s.name == "http.server"]
    assert body["trace_id"] in {format(s.context.trace_id, "032x") for s in servers}


# ---------------------------------------------------------------- working sets (semantic spec 7.5)


def narrow_previous(seen: list[str]) -> FunctionModel:
    """A model that narrows the previous answer by location, recording what it was told."""

    def fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.extend(str(m) for m in messages)
        if not any(isinstance(p, ToolReturnPart) for m in messages for p in getattr(m, "parts", [])):
            arguments = {"measures": ["on_hand"], "dimensions": ["bin"], "scope": {"working_set": True}}
            return ModelResponse(parts=[ToolCallPart("query_inventory", arguments)])
        return ModelResponse(parts=[TextPart("Those are the locations holding it.")])

    return FunctionModel(fn)


def query_audit(env: Env, run_id: str) -> tuple[Any, ...]:
    row = env.db.execute(
        "SELECT outcome, parameters FROM pw.audit_event WHERE run_id = %s AND tool = 'query_inventory'",
        (UUID(run_id),),
    ).fetchone()
    assert row is not None
    return tuple(row)


def test_of_those_narrows_the_previous_answer_with_fresh_data(env: Env) -> None:
    env.sign_in(subject="ws-narrow")  # its own run budget (20 runs per user per minute)
    _, first, _ = env.ask("Where is SKU-102 stored?")  # fast path: product and its bins
    conversation = first["x-conversation-id"]
    seen: list[str] = []
    env.models.models["gemini_flash"] = narrow_previous(seen)
    _, follow, events = env.ask("Of those, which are in zone B?", conversation_id=conversation)

    assert any("The previous answer listed 1 product" in m for m in seen)
    assert not any(str(env.site.site) in m for m in seen if "previous answer" in m)  # counts, not ids
    [result] = of_type(events, "pw.result")
    expected = {
        r[0]
        for r in env.db.execute(
            "SELECT b.code FROM pw.inventory_balance ib JOIN pw.bin b ON b.id = ib.bin_id"
            " JOIN pw.product p ON p.id = ib.product_id WHERE ib.tenant_id = %s AND p.sku = 'SKU-102'"
            " AND ib.on_hand > 0",
            (env.site.tenant,),
        ).fetchall()
    }
    assert {r["bin"] for r in result["result"]["rows"]} == expected  # only SKU-102, re-read now
    assert "Narrowed to the 1 product of the previous answer." in " ".join(
        result["result"]["evidence"]["how_computed"]
    )
    outcome, parameters = query_audit(env, follow["x-run-id"])
    assert outcome == "OK" and parameters["working_set_from"]["run_id"] == first["x-run-id"]


def test_without_a_previous_result_there_is_nothing_to_narrow(env: Env) -> None:
    env.sign_in(subject="ws-empty")  # its own run budget (20 runs per user per minute)
    env.models.models["gemini_flash"] = narrow_previous([])
    _, headers, _ = env.ask("Of those, which are in zone B?")  # a new conversation
    outcome, _ = query_audit(env, headers["x-run-id"])
    assert outcome == "VALIDATION_FAILED"


def test_a_simulation_reset_invalidates_earlier_working_sets(env: Env) -> None:
    env.sign_in(subject="ws-reset")  # its own run budget (20 runs per user per minute)
    _, first, _ = env.ask("Where is SKU-102 stored?")
    reset = uuid4()
    current = env.db.execute(
        "SELECT id FROM pw.simulation_run WHERE site_id = %s AND status = 'ACTIVE'", (env.site.site,)
    ).fetchone()
    assert current is not None
    # A reset ends the current run and starts a new one (one active run per site).
    env.db.execute("UPDATE pw.simulation_run SET status = 'ENDED' WHERE id = %s", (current[0],))
    env.db.execute(
        "INSERT INTO pw.simulation_run (id, tenant_id, site_id, seed, dataset_version, history_start,"
        " clock_start, status, started_at, started_by)"
        " SELECT %s, tenant_id, site_id, seed, dataset_version, history_start, clock_start, 'ACTIVE',"
        " now(), 'test-reset' FROM pw.simulation_run WHERE id = %s",
        (reset, current[0]),
    )
    try:
        env.models.models["gemini_flash"] = narrow_previous([])
        _, follow, _ = env.ask("Of those, which are in zone B?", conversation_id=first["x-conversation-id"])
        outcome, _ = query_audit(env, follow["x-run-id"])
        assert outcome == "VALIDATION_FAILED"  # the earlier result describes replaced data
    finally:
        env.db.execute("UPDATE pw.simulation_run SET status = 'ENDED' WHERE id = %s", (reset,))
        env.db.execute("UPDATE pw.simulation_run SET status = 'ACTIVE' WHERE id = %s", (current[0],))
