"""Semantic layer v0 and the first two tools on a site loaded from the simulator.

Semantic spec 4.1, 7.5, 7.6, 8 and 10; agent spec 4.1. Expected values come from independent
queries on the raw tables as the superuser, never from the code under test.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from support import load_site, provision_site

from palletwise.contracts.results import ResultEnvelope, ResultStatus
from palletwise.db.agent_repo import AgentRegistry
from palletwise.db.audit_repo import AuditLog
from palletwise.db.connection import role_pool, tenant_session
from palletwise.policy import PolicyClient
from palletwise.semantic import locations
from palletwise.settings import PolicySettings
from palletwise.tools.catalog import v1_tools
from palletwise.tools.executor import Outcome, RunContext, ToolContext, ToolExecutor, ToolRegistry, ToolSpec

pytestmark = pytest.mark.integration

Connect = Callable[[str], psycopg.Connection]
DASHES = (chr(0x2013), chr(0x2014))  # en and em dash


@dataclass(frozen=True)
class Site:
    tenant: UUID
    site: UUID
    db: psycopg.Connection  # superuser, for independent expectations


@pytest.fixture(scope="module")
def site(connect_session: Connect) -> Site:
    tenant = uuid4()
    site_id, _, _ = load_site(connect_session, tenant)
    db = connect_session("postgres")
    db.autocommit = True
    return Site(tenant, site_id, db)


def call(site: Site, fn: Callable[..., ResultEnvelope], *args: Any, **kwargs: Any) -> ResultEnvelope:
    """Runs a semantic function exactly as a tool does: pw_agent_read, read snapshot, tenant."""
    with tenant_session(role_pool("PW_AGENT_READ"), site.tenant, read_snapshot=True) as conn:
        return fn(conn, site.site, *args, **kwargs)


def expected_locations(site: Site, sku: str) -> list[tuple[Any, ...]]:
    return site.db.execute(
        """
        SELECT b.code, b.bin_role, ib.on_hand, ib.allocated, ib.on_hand - ib.allocated,
               b.is_active AND b.bin_role IN ('PICK', 'RESERVE', 'BULK') AS storable
          FROM pw.inventory_balance ib
          JOIN pw.product p ON p.id = ib.product_id
          JOIN pw.bin b ON b.id = ib.bin_id
         WHERE ib.tenant_id = %s AND ib.site_id = %s AND p.sku = %s AND (ib.on_hand > 0 OR ib.allocated > 0)
         ORDER BY storable DESC, ib.on_hand DESC, b.code
        """,
        (site.tenant, site.site, sku),
    ).fetchall()


def busiest_bin(site: Site) -> str:
    row = site.db.execute(
        """
        SELECT b.code FROM pw.inventory_balance ib JOIN pw.bin b ON b.id = ib.bin_id
         WHERE ib.tenant_id = %s AND ib.on_hand > 0
         GROUP BY b.code ORDER BY count(*) DESC, b.code LIMIT 1
        """,
        (site.tenant,),
    ).fetchone()
    assert row is not None
    return row[0]


# ---------------------------------------------------------------- find_product_locations


def test_product_locations_match_the_balances(site: Site) -> None:
    result = call(site, locations.product_locations, sku="SKU-102")
    expected = expected_locations(site, "SKU-102")
    assert expected, "the simulator plants SKU-102 with stock"
    assert result.status is ResultStatus.OK
    got = [
        (r["bin_code"], r["bin_role"], r["on_hand"], r["allocated"], r["available"], r["storable"])
        for r in result.rows
    ]
    assert got == expected


def test_product_locations_highlight_exactly_those_bins(site: Site) -> None:
    result = call(site, locations.product_locations, sku="sku-102")  # case-insensitive
    bins = {e.label: e.id for e in result.entities if e.type == "bin"}
    assert [h.target_id for h in result.highlights] == [bins[r["bin_code"]] for r in result.rows]
    assert {(h.target_type, h.reason, h.severity) for h in result.highlights} == {("bin", "location", "info")}
    assert result.entities[0].type == "product" and result.entities[0].label == "SKU-102"
    assert result.normalized_request["sku"] == "SKU-102"


def test_evidence_uses_the_site_clock_and_the_oldest_stream(site: Site) -> None:
    result = call(site, locations.product_locations, sku="SKU-102")
    clock = site.db.execute("SELECT sim_time FROM pw.site_clock WHERE site_id = %s", (site.site,)).fetchone()
    oldest = site.db.execute(
        "SELECT min(data_as_of) FROM pw.sync_state"
        " WHERE site_id = %s AND stream IN ('MASTER', 'MOVEMENTS', 'ALLOCATIONS')",
        (site.site,),
    ).fetchone()
    assert clock and oldest and result.evidence.as_of == clock[0] and result.evidence.data_as_of == oldest[0]
    assert {f.stream for f in result.evidence.freshness} == {"MASTER", "MOVEMENTS", "ALLOCATIONS"}
    # Every answer names the settings version in effect (Slice 2 acceptance), even when its
    # definitions use no setting, so all numbers in one answer share one version.
    current = site.db.execute(
        "SELECT id FROM pw.settings_version WHERE site_id = %s ORDER BY version_no DESC LIMIT 1", (site.site,)
    ).fetchone()
    assert current and result.evidence.settings_version_id == current[0]
    text = " ".join(result.evidence.how_computed)
    assert "SKU-102" in text and "(Asia/Colombo)" in text and not any(d in text for d in DASHES)


def test_product_found_by_name(site: Site) -> None:
    for query in ("Wireless Mouse", "wireless mouse", "  wireless   MOUSE "):
        result = call(site, locations.product_locations, product_query=query)
        assert result.status is ResultStatus.OK and result.normalized_request["sku"] == "SKU-102", query


def test_several_matches_are_listed_not_guessed(site: Site) -> None:
    word = site.db.execute(
        """
        SELECT w FROM pw.product p, regexp_split_to_table(lower(p.name), ' ') w
         WHERE p.tenant_id = %s AND length(w) > 3
         GROUP BY w HAVING count(*) BETWEEN 2 AND 6 ORDER BY w LIMIT 1
        """,
        (site.tenant,),
    ).fetchone()
    assert word is not None
    result = call(site, locations.product_locations, product_query=word[0])
    assert result.status is ResultStatus.AMBIGUOUS and 2 <= result.row_count <= 6
    assert not result.highlights and result.message and "Which one" in result.message


@pytest.mark.parametrize("query", ["%", "_ouse", "' OR 1=1 --", "x'; DROP TABLE pw.product; --"])
def test_query_text_is_a_literal_never_a_pattern_or_sql(site: Site, query: str) -> None:
    result = call(site, locations.product_locations, product_query=query)
    assert result.status is ResultStatus.NOT_FOUND
    assert site.db.execute(
        "SELECT count(*) FROM pw.product WHERE tenant_id = %s", (site.tenant,)
    ).fetchone() == (300,)


def test_unknown_sku_is_not_found_not_an_empty_table(site: Site) -> None:
    result = call(site, locations.product_locations, sku="SKU-99999")
    assert result.status is ResultStatus.NOT_FOUND and result.message == "No such SKU: SKU-99999."
    assert not result.rows and not result.highlights


def test_product_without_stock_is_no_results(site: Site) -> None:
    row = site.db.execute(
        """
        SELECT p.sku FROM pw.product p WHERE p.tenant_id = %s AND NOT EXISTS (
          SELECT 1 FROM pw.inventory_balance ib
           WHERE ib.product_id = p.id AND (ib.on_hand > 0 OR ib.allocated > 0))
        ORDER BY p.sku LIMIT 1
        """,
        (site.tenant,),
    ).fetchone()
    if row is None:
        pytest.skip("every product has stock in this dataset")
    result = call(site, locations.product_locations, sku=row[0])
    assert result.status is ResultStatus.NO_RESULTS and not result.highlights


# ---------------------------------------------------------------- get_bin_contents


def test_bin_contents_match_the_balances(site: Site) -> None:
    code = busiest_bin(site)
    result = call(site, locations.bin_contents, code.lower())
    expected = site.db.execute(
        """
        SELECT p.sku, ib.on_hand, ib.allocated, ib.on_hand - ib.allocated
          FROM pw.inventory_balance ib
          JOIN pw.product p ON p.id = ib.product_id
          JOIN pw.bin b ON b.id = ib.bin_id
         WHERE ib.tenant_id = %s AND b.code = %s AND (ib.on_hand > 0 OR ib.allocated > 0)
         ORDER BY ib.on_hand DESC, p.sku
        """,
        (site.tenant, code),
    ).fetchall()
    assert result.status is ResultStatus.OK and result.normalized_request["bin_code"] == code
    assert [(r["sku"], r["on_hand"], r["allocated"], r["available"]) for r in result.rows] == expected
    [highlight] = result.highlights
    assert highlight.target_id == result.entities[0].id and highlight.reason == "location"


def test_empty_bin_is_no_results_but_still_highlighted(site: Site) -> None:
    row = site.db.execute(
        """
        SELECT b.code FROM pw.bin b WHERE b.tenant_id = %s AND NOT EXISTS (
          SELECT 1 FROM pw.inventory_balance ib
           WHERE ib.bin_id = b.id AND (ib.on_hand > 0 OR ib.allocated > 0))
        ORDER BY b.code LIMIT 1
        """,
        (site.tenant,),
    ).fetchone()
    assert row is not None
    result = call(site, locations.bin_contents, row[0])
    assert result.status is ResultStatus.NO_RESULTS and len(result.highlights) == 1


def test_unknown_bin_is_not_found(site: Site) -> None:
    result = call(site, locations.bin_contents, "Z9-99-99")
    assert result.status is ResultStatus.NOT_FOUND and result.message == "No such location: Z9-99-99."


# ---------------------------------------------------------------- data availability and time


def test_site_never_synced_is_unavailable(connect_session: Connect) -> None:
    tenant, site_id = provision_site(connect_session)
    unsynced = Site(tenant, site_id, connect_session("postgres"))
    result = call(unsynced, locations.product_locations, sku="SKU-102")  # clock set, streams never synced
    assert result.status is ResultStatus.UNAVAILABLE and not result.rows
    assert result.message == "This data has not been loaded yet."


def test_stock_functions_refuse_a_stale_as_of(site: Site, connect_session: Connect) -> None:
    conn = connect_session("pw_agent_read")
    with conn.transaction():
        conn.execute("SELECT set_config('app.tenant_id', %s, true)", (str(site.tenant),))
        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState):
            conn.execute(
                "SELECT * FROM sem.product_locations(%s,"
                " (SELECT as_of - interval '1 minute' FROM sem.site_now WHERE site_id = %s),"
                " (SELECT product_id FROM sem.product WHERE sku = 'SKU-102'))",
                (site.site, site.site),
            )


# ---------------------------------------------------------------- roles and tenants


def test_reader_uses_sem_only(site: Site, connect_session: Connect) -> None:
    conn = connect_session("pw_agent_read")
    conn.autocommit = True
    conn.execute("SELECT set_config('app.tenant_id', %s, false)", (str(site.tenant),))
    assert conn.execute("SELECT count(*) FROM sem.product").fetchone() == (300,)
    for table in ("pw.product", "pw.inventory_balance", "pw.audit_event", "pw.web_session"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(f"SELECT 1 FROM {table}")
    conn.execute("RESET app.tenant_id")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):  # no tenant context: fails closed
        conn.execute("SELECT count(*) FROM sem.product")


def test_another_tenant_sees_nothing(site: Site, connect_session: Connect) -> None:
    other, other_site = provision_site(connect_session)
    product = site.db.execute(
        "SELECT id FROM pw.product WHERE tenant_id = %s AND sku = 'SKU-102'", (site.tenant,)
    ).fetchone()
    assert product is not None
    with tenant_session(role_pool("PW_AGENT_READ"), other) as conn:
        assert conn.execute("SELECT count(*) FROM sem.product").fetchone() == (0,)
        assert conn.execute(
            "SELECT count(*) FROM sem.stock WHERE product_id = %s", (product[0],)
        ).fetchone() == (0,)
        # Even with the right site id, another tenant's rows are invisible.
        assert conn.execute(
            "SELECT count(*) FROM sem.location WHERE site_id = %s", (site.site,)
        ).fetchone() == (0,)
    del other_site


# ---------------------------------------------------------------- through tools.executor


@pytest.fixture(scope="module")
def executor(opa_url: str) -> Callable[[ToolRegistry], ToolExecutor]:
    def make(tools: ToolRegistry) -> ToolExecutor:
        app = role_pool("PW_APP")
        return ToolExecutor(
            tools,
            AgentRegistry(app),
            PolicyClient(PolicySettings(url=opa_url)),
            AuditLog(app),
            role_pool("PW_AGENT_READ"),
        )

    return make


def run_for(site: Site) -> RunContext:
    return RunContext(
        run_id=uuid4(),
        trace_id="ab" * 16,
        subject="user-1",
        role="warehouse_manager",
        tenant=site.tenant,
        sites=(site.site,),
        site=site.site,
        model="gemini-flash",
    )


def test_tools_run_through_the_executor(site: Site, executor: Callable[[ToolRegistry], ToolExecutor]) -> None:
    run = run_for(site)
    result = executor(v1_tools()).execute(run, "find_product_locations", {"sku": "SKU-102"})
    assert result.outcome is Outcome.OK and result.output.status is ResultStatus.OK
    contents = executor(v1_tools()).execute(
        run, "get_bin_contents", {"bin_code": result.output.rows[0]["bin_code"]}
    )
    assert contents.outcome is Outcome.OK and "SKU-102" in {r["sku"] for r in contents.output.rows}
    audited = site.db.execute(
        "SELECT tool, parameters, row_count FROM pw.audit_event"
        " WHERE tenant_id = %s AND run_id = %s ORDER BY seq",
        (site.tenant, run.run_id),
    ).fetchall()
    assert audited[0] == (
        "find_product_locations",
        {"sku": "SKU-102", "product_query": None},
        result.output.row_count,
    )


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("find_product_locations", {}),
        ("find_product_locations", {"sku": "SKU-102", "product_query": "mouse"}),
        ("find_product_locations", {"sku": "SKU-102'; DROP TABLE pw.product; --"}),
        ("find_product_locations", {"product_query": "x"}),
        ("find_product_locations", {"sku": "SKU-102", "limit": 5}),
        ("get_bin_contents", {"bin_code": "A1 OR 1=1"}),
        ("get_bin_contents", {}),
    ],
)
def test_invalid_tool_arguments_are_rejected(
    site: Site, executor: Callable[[ToolRegistry], ToolExecutor], tool: str, arguments: dict[str, Any]
) -> None:
    result = executor(v1_tools()).execute(run_for(site), tool, arguments)
    assert result.outcome is Outcome.VALIDATION_FAILED


def test_tool_transactions_are_read_only_snapshots(
    site: Site, executor: Callable[[ToolRegistry], ToolExecutor]
) -> None:
    from pydantic import BaseModel, ConfigDict

    class NoInput(BaseModel):
        model_config = ConfigDict(extra="forbid")

    @dataclass(frozen=True)
    class Seen:
        isolation: str
        read_only: str
        timeout: str
        row_count: int = 0

    def inspect(ctx: ToolContext, _: NoInput) -> Seen:
        row = ctx.conn.execute(
            "SELECT current_setting('transaction_isolation'), current_setting('transaction_read_only'),"
            " current_setting('statement_timeout')"
        ).fetchone()
        assert row is not None
        return Seen(*row)

    tools = ToolRegistry([ToolSpec("inspect", "test", NoInput, "inventory.read", "read", inspect)])
    result = executor(tools).execute(run_for(site), "inspect", {})
    assert (result.output.isolation, result.output.read_only, result.output.timeout) == (
        "repeatable read",
        "on",
        "3s",
    )
