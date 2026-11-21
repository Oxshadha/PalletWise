"""The evaluation environment (evals spec 7): a seeded database, the real policy, the API in-process.

The runner calls the API exactly as the browser does (POST /api/copilot/runs as a signed-in test
user), so the measured path is the real one. Identity comes from the fake OIDC provider; the model
provider is chosen per gate (none for the deterministic gate, the configured one for the model gate).
"""

import base64
import json
import os
import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import httpx
import psycopg
from fastapi.testclient import TestClient

from evals.fake_idp import CLIENT_ID, CLIENT_SECRET, ISSUER, ORIGIN, SIGNING_KEY, FakeIdp
from evals.reference import create_eval_role
from palletwise.agent.runtime import CopilotRuntime, RuntimeConfig
from palletwise.api import copilot, sites
from palletwise.api.app import create_app
from palletwise.auth.crypto import KeyRing
from palletwise.auth.oidc import OidcClient
from palletwise.auth.service import AuthService
from palletwise.db.agent_repo import AgentRegistry
from palletwise.db.audit_repo import AuditLog
from palletwise.db.connection import role_pool
from palletwise.db.copilot_repo import ConversationStore, RateLimiter, UsageLedger
from palletwise.db.session_repo import SessionStore
from palletwise.llm import LLMProvider
from palletwise.policy import PolicyClient
from palletwise.settings import OidcSettings, PolicySettings, SessionSettings, WebSettings
from palletwise.tools.catalog import v1_tools
from palletwise.tools.executor import ToolExecutor

SEED = 42
HISTORY_DAYS = 14
CLOCK_START = datetime(2026, 4, 1, 6, 0, tzinfo=ZoneInfo("Asia/Colombo"))
DATASET_VERSION = f"sim-v1-seed{SEED}-{HISTORY_DAYS}d"
EVAL_READER_PASSWORD = "eval-reader-only"  # noqa: S105 - a test-only role in a throwaway database


@dataclass
class EvalEnv:
    tenant: UUID
    site: UUID
    superuser: psycopg.Connection  # autocommit, for inspection only
    reader: psycopg.Connection  # pw_eval_reader, for reference queries
    opa_url: str
    dataset_version: str = DATASET_VERSION
    # Highlight target ids to stable codes: bins by code, zones as "zone:<code>" (evals/reference.py).
    bin_codes: dict[str, str] = field(default_factory=lambda: {})

    def __post_init__(self) -> None:
        rows = self.superuser.execute(
            "SELECT id, code FROM pw.bin WHERE site_id = %s"
            " UNION ALL SELECT id, 'zone:' || code FROM pw.zone WHERE site_id = %s",
            (self.site, self.site),
        ).fetchall()
        self.bin_codes = {str(i): c for i, c in rows}

    def executor(self) -> ToolExecutor:
        app = role_pool("PW_APP")
        return ToolExecutor(
            v1_tools(),
            AgentRegistry(app),
            PolicyClient(PolicySettings(url=self.opa_url)),
            AuditLog(app),
            role_pool("PW_AGENT_READ"),
            RateLimiter(app),
        )

    @contextmanager
    def api(
        self, provider: LLMProvider, run_timeout: float = 45.0, config: RuntimeConfig | None = None
    ) -> Generator["ApiClient"]:
        idp = FakeIdp()
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
        executor = self.executor()
        services = copilot.CopilotServices(
            runtime=CopilotRuntime(v1_tools(), executor, provider, run_timeout=run_timeout, config=config),
            agents=AgentRegistry(app_pool),
            conversations=ConversationStore(role_pool("PW_MEMORY")),
            ledger=UsageLedger(app_pool),
            limiter=_NoRateLimit(),  # type: ignore[arg-type]
            audit=AuditLog(app_pool),
            app_pool=app_pool,
        )
        reads = sites.ReadServices(
            PolicyClient(PolicySettings(url=self.opa_url)),
            AuditLog(app_pool),
            app_pool,
            role_pool("PW_AGENT_READ"),
        )
        # Entered as a context manager so every request shares one event loop, as under uvicorn.
        # Without it each request gets a fresh loop and the model client's pooled connections,
        # bound to the previous (closed) loop, fail on every other call.
        with TestClient(
            create_app(auth, WebSettings(public_origin=ORIGIN), services, reads), base_url=ORIGIN
        ) as client:
            yield ApiClient(self, idp, client)


class _NoRateLimit:
    """The evaluation asks many questions per minute; rate limits are tested elsewhere."""

    def allow(self, tenant: UUID, subject: str, key: str, per_minute: int) -> bool:
        return True


@dataclass
class RunTrace:
    status: int
    events: list[dict[str, Any]]
    run_id: str | None
    conversation_id: str | None
    first_result_s: float | None
    first_text_s: float | None
    total_s: float
    problem: dict[str, Any] | None = None

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e.get("type") == kind]

    @property
    def text(self) -> str:
        return "".join(e.get("delta", "") for e in self.of("TEXT_MESSAGE_CONTENT"))

    @property
    def tools(self) -> list[str]:
        return [e["tool"] for e in self.of("TOOL_CALL_START")]

    @property
    def outcome(self) -> str | None:
        finished = self.of("RUN_FINISHED")
        if finished:
            return finished[0].get("outcome")
        return "ERROR" if self.of("RUN_ERROR") or self.problem else None

    @property
    def path(self) -> str | None:
        finished = self.of("RUN_FINISHED") or self.of("RUN_STARTED")
        return finished[0].get("path") if finished else None


@dataclass
class ApiClient:
    env: EvalEnv
    idp: FakeIdp
    client: TestClient

    def sign_in(self, role: str) -> None:
        subject = f"eval-{role}-{uuid4().hex[:8]}"
        self.idp.users[subject] = {
            "pw_roles": [role],
            "pw_tenant": str(self.env.tenant),
            "pw_sites": [str(self.env.site)],
            "name": "Evaluation",
        }
        self.client.cookies.clear()
        start = self.client.get("/api/auth/login", follow_redirects=False)
        code = self.idp.authorize(start.headers["location"], subject)
        state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
        self.client.get(
            f"/api/auth/callback?{urlencode({'state': state, 'code': code})}", follow_redirects=False
        )

    def ask(
        self, question: str, conversation_id: str | None, page_context: dict[str, Any] | None
    ) -> RunTrace:
        body: dict[str, Any] = {"site_id": str(self.env.site), "question": question}
        if conversation_id:
            body["conversation_id"] = conversation_id
        if page_context:
            body["page_context"] = page_context
        started = time.perf_counter()
        first_result = first_text = None
        events: list[dict[str, Any]] = []
        with self.client.stream(
            "POST", "/api/copilot/runs", json=body, headers={"Origin": ORIGIN}
        ) as response:
            if response.status_code != 200:
                response.read()
                return RunTrace(
                    response.status_code,
                    [],
                    None,
                    None,
                    None,
                    None,
                    time.perf_counter() - started,
                    response.json(),
                )
            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue
                event = json.loads(line[5:])
                now = time.perf_counter() - started
                event["_t"] = round(now, 4)  # arrival time from the question being sent (ADR 0001)
                events.append(event)
                if event["type"] == "pw.result" and first_result is None:
                    first_result = now
                if event["type"] == "TEXT_MESSAGE_CONTENT" and first_text is None:
                    first_text = now
            headers = response.headers
        return RunTrace(
            200,
            events,
            headers.get("x-run-id"),
            headers.get("x-conversation-id"),
            first_result,
            first_text,
            time.perf_counter() - started,
        )


# ---------------------------------------------------------------- the standalone environment (CLI)


@contextmanager
def standalone() -> Generator[EvalEnv]:
    """PostgreSQL and OPA in containers, migrations, the seeded site loaded through the connector."""
    from testcontainers.core.container import DockerContainer
    from testcontainers.postgres import PostgresContainer

    from palletwise.db import dataset_hash, ingest_repo, migrate, sim_source, site_repo
    from palletwise.db.bootstrap import LOGIN_ROLES
    from palletwise.db.connection import dsn
    from palletwise.settings import DatabaseSettings
    from pw_simulator import store
    from pw_simulator.history import RunParams, generate

    images = dict(
        line.split("=", 1)
        for line in open("infra/images.env", encoding="utf-8").read().splitlines()  # noqa: SIM115
        if "=" in line and not line.startswith("#")
    )
    passwords = {role: f"eval-{role}" for role in LOGIN_ROLES}
    # Data in memory (tmpfs): the image declares a data volume, and an anonymous volume would be
    # left behind on the host after every run.
    with PostgresContainer(
        "postgres:17-alpine", username="postgres", password="postgres", dbname="palletwise"
    ).with_tmpfs_mount("/var/lib/postgresql/data") as pg:
        os.environ.update(
            {
                "PW_DB_HOST": pg.get_container_host_ip(),
                "PW_DB_PORT": str(pg.get_exposed_port(5432)),
                "PW_DB_NAME": "palletwise",
                "PW_BOOTSTRAP_USER": "postgres",
                "PW_BOOTSTRAP_PASSWORD": "postgres",
            }
        )
        for role, password in passwords.items():
            os.environ[f"{role.upper()}_USER"] = role
            os.environ[f"{role.upper()}_PASSWORD"] = password
        db = DatabaseSettings()
        migrate.run_bootstrap(db)
        migrate.upgrade(db)
        migrate.run_calendar(db)

        def connect(role: str) -> psycopg.Connection:
            password = "postgres" if role == "postgres" else passwords[role]
            return psycopg.connect(dsn(db, role, password))

        tenant, site, connector, run = uuid4(), uuid4(), uuid4(), uuid4()
        site_repo.bootstrap_site(
            connect("pw_sim_admin"),
            site_repo.SiteBootstrap(
                tenant_id=tenant,
                tenant_code=f"t-{tenant.hex[:8]}",
                tenant_name="Evaluation",
                site_id=site,
                site_code="PLG",
                site_name="Peliyagoda Distribution Centre",
                region_code="LK",
                time_zone="Asia/Colombo",
                width_m=80.0,
                depth_m=50.0,
                connector_id=connector,
                run_id=run,
                seed=SEED,
                dataset_version=DATASET_VERSION,
                history_start=CLOCK_START - timedelta(days=HISTORY_DAYS),
                clock_start=CLOCK_START,
                started_by="evals",
            ),
        )
        params = RunParams(str(run), SEED, DATASET_VERSION, CLOCK_START, history_days=HISTORY_DAYS)
        sim = connect("sim_owner")
        store.ensure_schema(sim)
        store.write_run(sim, params, generate(params))
        source, dest = connect("sim_outbox_reader"), connect("pw_ingest")
        while batch := sim_source.pull(
            source, run, ingest_repo.read_cursor(dest, tenant, connector, run), 5000
        ):
            ingest_repo.apply_batch(dest, tenant, site, connector, batch)
        admin = connect("pw_sim_admin")
        digest, _ = dataset_hash.compute(admin, tenant, site)
        site_repo.complete_load(admin, tenant, site, run, digest)
        # The exception engine evaluates the loaded site, as the evaluator worker does once the
        # site is READY (slice-3-acceptance 2.3), so the inbox and its tool have their records.
        evaluator = connect("pw_evaluator")
        with evaluator.transaction():
            evaluator.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant),))
            evaluated = evaluator.execute(
                "SELECT outcome FROM pw.evaluate_site_exceptions(%s)", (site,)
            ).fetchone()
        assert evaluated is not None and evaluated[0] == "evaluated", evaluated

        superuser = connect("postgres")
        superuser.autocommit = True
        create_eval_role(superuser, EVAL_READER_PASSWORD)
        reader = psycopg.connect(dsn(db, "pw_eval_reader", EVAL_READER_PASSWORD))

        container = (
            DockerContainer(images["OPA_IMAGE"])
            .with_command("run --server --addr=0.0.0.0:8181 /policy")
            .with_exposed_ports(8181)
            .with_volume_mapping(os.path.abspath("policy"), "/policy", "ro")
        )
        with container:
            opa_url = f"http://{container.get_container_host_ip()}:{container.get_exposed_port(8181)}"
            deadline = time.monotonic() + 60
            while True:
                try:
                    if httpx.get(f"{opa_url}/health").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                if time.monotonic() > deadline:
                    raise RuntimeError("OPA did not start")
                time.sleep(0.5)
            try:
                yield EvalEnv(tenant, site, superuser, reader, opa_url)
            finally:
                for conn in (reader, superuser, admin, source, dest, sim):
                    conn.close()
