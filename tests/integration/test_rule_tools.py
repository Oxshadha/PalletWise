"""The Slice 2 fixed tools through the real executor (Slice 2 acceptance step 2).

Every call goes through tools.executor with the real OPA policy, the agent registry, the audit
log and pw_agent_read, exactly as the copilot calls them. Sites are hand-built (fixture_site).
"""

from collections.abc import Callable
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fixture_site import FixtureSite

from palletwise.contracts.results import HighlightReason, ResultEnvelope, ResultStatus, Severity
from palletwise.db.agent_repo import AgentRegistry
from palletwise.db.audit_repo import AuditLog
from palletwise.db.connection import role_pool
from palletwise.policy import PolicyClient
from palletwise.settings import PolicySettings
from palletwise.tools.catalog import v1_tools
from palletwise.tools.executor import Outcome, RunContext, ToolExecutor

pytestmark = pytest.mark.integration

Connect = Callable[[str], psycopg.Connection]
H = timedelta(hours=1)
DASHES = (chr(0x2013), chr(0x2014))  # en and em dash (FR-COP-13)
DAY = timedelta(days=1)


@pytest.fixture
def run(opa_url: str) -> Callable[..., Any]:
    app = role_pool("PW_APP")
    executor = ToolExecutor(
        v1_tools(), AgentRegistry(app), PolicyClient(PolicySettings(url=opa_url)), AuditLog(app),
        role_pool("PW_AGENT_READ"),
    )  # fmt: skip

    def call(f: FixtureSite, tool: str, run_id: UUID | None = None, **arguments: Any) -> Any:
        ctx = RunContext(
            run_id=run_id or uuid4(), trace_id="cd" * 16, subject="manager-1", role="warehouse_manager",
            tenant=f.tenant, sites=(f.site,), site=f.site, model="gemini-flash",
        )  # fmt: skip
        return executor.execute(ctx, tool, arguments)

    return call


def ok(result: Any) -> ResultEnvelope:
    assert result.outcome is Outcome.OK, (result.outcome, result.message)
    return result.output


def codes(f: FixtureSite, envelope: ResultEnvelope) -> list[tuple[str, str, str, str]]:
    """Highlights as (type, code, reason, severity), resolving ids to codes independently."""
    out = []
    for h in envelope.highlights:
        table = "bin" if h.target_type == "bin" else "zone"
        row = f.db.execute(f"SELECT code FROM pw.{table} WHERE id = %s", (h.target_id,)).fetchone()
        assert row is not None
        out.append((h.target_type, row[0], h.reason.value, h.severity.value))
    return out


# ---------------------------------------------------------------- envelope contract


def test_every_rule_tool_answers_an_empty_site_with_no_results(run: Any, connect_as: Connect) -> None:
    f = FixtureSite(connect_as)
    for tool in ("get_low_stock", "get_top_movers", "get_dead_stock", "get_slow_in_prime",
                 "get_capacity_hotspots", "get_delayed_orders", "get_order_stockout_risks"):  # fmt: skip
        envelope = ok(run(f, tool))
        assert envelope.status is ResultStatus.NO_RESULTS, tool
        assert envelope.message and not any(d in envelope.message for d in DASHES)
        assert envelope.evidence.settings_version_id == f.settings(), tool
        assert envelope.normalized_request["tool"] == tool


def test_unknown_or_out_of_range_arguments_are_refused_and_audited(run: Any, connect_as: Connect) -> None:
    f = FixtureSite(connect_as)
    run_id = uuid4()
    for tool, arguments in (
        ("get_low_stock", {"zone": "A"}),  # no arguments allowed
        ("get_top_movers", {"days": 0}),
        ("get_top_movers", {"limit": 500}),
        ("get_order_stockout_risks", {"view": "everything"}),
        ("get_order_stockout_risks", {"hours": 1000}),
        ("get_delayed_orders", {"since_days": -1}),
    ):
        assert run(f, tool, run_id, **arguments).outcome is Outcome.VALIDATION_FAILED, (tool, arguments)
    audited = f.db.execute(
        "SELECT count(*) FROM pw.audit_event WHERE run_id = %s AND outcome = 'VALIDATION_FAILED'", (run_id,)
    ).fetchone()
    assert audited == (6,)


def test_order_tools_need_orders_read_on_the_agent(run: Any, connect_as: Connect) -> None:
    f = FixtureSite(connect_as)
    f.db.execute(
        "UPDATE pw.agent SET allowed_scopes = ARRAY['inventory.read'] WHERE tenant_id = %s", (f.tenant,)
    )
    assert run(f, "get_delayed_orders").outcome is Outcome.DENIED
    assert run(f, "get_order_stockout_risks").outcome is Outcome.DENIED
    assert run(f, "get_low_stock").outcome is Outcome.OK  # inventory.read still allowed


# ---------------------------------------------------------------- stock tools


def test_low_stock_highlights_the_pick_locations_with_the_rule_reason(run: Any, connect_as: Connect) -> None:
    f = FixtureSite(connect_as)
    zone = f.zone("P", zone_type="FAST_PICK", prime=True)
    low, fine = f.product("LOW"), f.product("FINE")
    low_bin, fine_bin = f.bin(zone, "P-01"), f.bin(zone, "P-02")
    f.stock(low_bin, low, 4)
    f.stock(fine_bin, fine, 40)
    f.policy(low, low_bin, 10, 50)
    f.policy(fine, fine_bin, 10, 50)
    envelope = ok(run(f, "get_low_stock"))
    assert [(r["sku"], r["available"], r["min_qty"]) for r in envelope.rows] == [("LOW", 4, 10)]
    assert codes(f, envelope) == [("bin", "P-01", "low_stock", "warning")]
    assert {(e.type, e.label) for e in envelope.entities} == {("product", "LOW"), ("bin", "P-01")}
    assert "at or below the policy minimum" in envelope.evidence.how_computed[0]


def test_top_movers_highlight_storable_bins_largest_first(run: Any, connect_as: Connect) -> None:
    f = FixtureSite(connect_as)
    pick, reserve, staging = f.zone("P"), f.zone("R"), f.zone("S", zone_type="STAGING")
    stage = f.bin(staging, "S-01", role="STAGING")
    fast, slow = f.product("FAST"), f.product("SLOW")
    small, large = f.bin(pick, "P-01"), f.bin(reserve, "R-01", role="RESERVE")
    f.stock(small, fast, 5)
    f.stock(large, fast, 80)
    f.stock(stage, fast, 500)  # staging is not storable: never highlighted
    f.pick(fast, 9, f.as_of() - DAY, small, stage)
    f.pick(slow, 2, f.as_of() - DAY, small, stage)
    envelope = ok(run(f, "get_top_movers", days=7, limit=5))
    assert [(r["sku"], r["demand_units"]) for r in envelope.rows] == [("FAST", 9), ("SLOW", 2)]
    assert codes(f, envelope) == [
        ("bin", "R-01", "fast_mover", "info"),
        ("bin", "P-01", "fast_mover", "info"),
    ]
    assert "in the last 7 days" in envelope.evidence.how_computed[0]
    assert envelope.normalized_request == {"tool": "get_top_movers", "days": 7, "limit": 5}


def test_capacity_hotspots_highlight_zones_and_their_over_capacity_bins(
    run: Any, connect_as: Connect
) -> None:
    f = FixtureSite(connect_as)
    a, b = f.zone("A"), f.zone("B")
    bulky = f.product("BULKY", volume_m3=0.05, weight_kg=0.5)
    f.stock(f.bin(a, "A-01"), bulky, 25)  # 125%: over capacity
    f.stock(f.bin(a, "A-02"), bulky, 1)
    f.stock(f.bin(b, "B-01"), bulky, 2)  # 10%: not a hotspot
    envelope = ok(run(f, "get_capacity_hotspots"))
    assert [r["zone_code"] for r in envelope.rows] == ["A"]
    assert codes(f, envelope) == [
        ("bin", "A-01", "capacity", "critical"),
        ("zone", "A", "capacity", "warning"),
    ]
    assert "90 percent" in envelope.evidence.how_computed[0]


def test_dead_stock_and_slow_in_prime_name_their_settings_periods(run: Any, connect_as: Connect) -> None:
    f = FixtureSite(connect_as)
    prime = f.zone("P", zone_type="FAST_PICK", prime=True)
    idle = f.product("IDLE", value=10)
    f.stock(f.bin(prime, "P-01"), idle, 7)  # never picked, no receipt: dead and slow in prime
    dead = ok(run(f, "get_dead_stock"))
    slow = ok(run(f, "get_slow_in_prime"))
    assert [r["sku"] for r in dead.rows] == ["IDLE"] and "more than 30 days" in dead.evidence.how_computed[0]
    assert codes(f, slow) == [("bin", "P-01", "slow_in_prime", "warning")]
    assert "more than 14 days" in slow.evidence.how_computed[0]


# ---------------------------------------------------------------- order tools


def test_stockout_risks_by_product_order_and_replenishment(run: Any, connect_as: Connect) -> None:
    f = FixtureSite(connect_as)
    now = f.as_of()
    pick, reserve = f.zone("P", zone_type="FAST_PICK"), f.zone("R")
    fixable, short = f.product("FIX"), f.product("SHORT")
    fix_pick, short_pick = f.bin(pick, "P-01"), f.bin(pick, "P-02")
    f.stock(fix_pick, fixable, 2)
    f.stock(f.bin(reserve, "R-01", role="RESERVE"), fixable, 40)
    f.stock(short_pick, short, 1)
    order = f.order(deadline=now + 2 * H)
    f.line(order, fixable, 10)
    f.line(order, short, 5)

    products = ok(run(f, "get_order_stockout_risks"))
    assert [(r["sku"], r["fixable"]) for r in products.rows] == [("SHORT", False), ("FIX", True)]
    assert codes(f, products) == [
        ("bin", "P-02", "stockout_risk", "critical"),  # a true shortage
        ("bin", "P-01", "stockout_risk", "warning"),  # fixed by replenishment
    ]
    orders = ok(run(f, "get_order_stockout_risks", view="orders"))
    assert [
        (r["order_no"].startswith("SO-"), r["products_at_risk"], r["any_true_shortage"]) for r in orders.rows
    ] == [(True, 2, True)]
    assert {e.type for e in orders.entities} == {"order", "product"}
    replenish = ok(run(f, "get_order_stockout_risks", view="replenish_before_first_dispatch"))
    assert [(r["sku"], r["shortfall"]) for r in replenish.rows] == [("FIX", 8)]
    assert "within 24 hours" in products.evidence.how_computed[0]
    narrow = ok(run(f, "get_order_stockout_risks", hours=1))  # due in 2 hours: outside a 1 hour window
    assert narrow.status is ResultStatus.NO_RESULTS and "within 1 hours" in narrow.evidence.how_computed[0]


def test_delayed_orders_are_not_location_based(run: Any, connect_as: Connect) -> None:
    f = FixtureSite(connect_as)
    late = f.order(deadline=f.as_of() - 3 * H)
    f.line(late, f.product("X"), 1)
    envelope = ok(run(f, "get_delayed_orders"))
    assert [(r["status"], float(r["hours_late"])) for r in envelope.rows] == [("OPEN", 3.0)]
    assert envelope.highlights == () and [e.type for e in envelope.entities] == ["order"]


# ---------------------------------------------------------------- one moment, one settings version


def test_a_settings_change_reaches_the_next_answer_and_names_its_version(
    run: Any, connect_as: Connect
) -> None:
    f = FixtureSite(connect_as)
    a = f.zone("A")
    light = f.product("LIGHT", volume_m3=0.01, weight_kg=2.0)
    f.stock(f.bin(a, "A-01"), light, 46)  # 92%
    before = ok(run(f, "get_capacity_hotspots"))
    new_version = f.new_settings(capacity_threshold_pct=95)
    after = ok(run(f, "get_capacity_hotspots"))
    assert [r["zone_code"] for r in before.rows] == ["A"] and after.status is ResultStatus.NO_RESULTS
    assert after.evidence.settings_version_id == new_version != before.evidence.settings_version_id
    assert "95 percent" in (after.message or "")
    assert HighlightReason.CAPACITY in {h.reason for h in before.highlights}
    assert Severity.WARNING in {h.severity for h in before.highlights}


def test_a_site_that_never_synced_is_unavailable_not_empty(run: Any, connect_as: Connect) -> None:
    f = FixtureSite(connect_as)
    f.db.execute(
        "UPDATE pw.sync_state SET data_as_of = NULL WHERE site_id = %s AND stream = 'ORDERS'", (f.site,)
    )
    risks = ok(run(f, "get_order_stockout_risks"))
    assert risks.status is ResultStatus.UNAVAILABLE and risks.rows == ()
    assert ok(run(f, "get_low_stock")).status is ResultStatus.NO_RESULTS  # low stock does not read orders
