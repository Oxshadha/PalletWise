"""Exception engine (Slice 3 step 1): semantic spec 6 and 6.1, CWM 9.2, slice-3-acceptance 2.3, 2.4.

Expected numbers are worked out by hand. The fixture clock is 06:00 site time. A data change
moves the site's watermark the way a completed load does (the sync cursor advances), so the
engine sees a new evaluation key.
"""

import threading
from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg
import pytest
from fixture_site import FixtureSite

from palletwise.db.connection import role_pool, tenant_session

pytestmark = pytest.mark.integration

Connect = Callable[[str], psycopg.Connection]
H = timedelta(hours=1)


class Engine:
    def __init__(self, f: FixtureSite) -> None:
        self.f = f
        f.db.execute("UPDATE pw.site SET data_status = 'READY' WHERE id = %s", (f.site,))

    def load(self) -> None:
        """A load completed: every stream's cursor moves on."""
        self.f.db.execute(
            "UPDATE pw.sync_state SET cursor = coalesce(cursor, '') || '+' WHERE site_id = %s", (self.f.site,)
        )

    def evaluate(self) -> tuple[str, int, int, int]:
        with tenant_session(role_pool("PW_EVALUATOR"), self.f.tenant) as conn:
            row = conn.execute("SELECT * FROM pw.evaluate_site_exceptions(%s)", (self.f.site,)).fetchone()
        assert row is not None
        return row

    def records(self, *, current_only: bool = True) -> list[dict[str, Any]]:
        cursor = self.f.db.execute(
            "SELECT * FROM pw.exception_record WHERE site_id = %s"
            + (" AND status <> 'RESOLVED'" if current_only else "")
            + " ORDER BY exception_type, subject_key, first_detected_at",
            (self.f.site,),
        )
        names = [c.name for c in cursor.description or ()]
        return [dict(zip(names, r, strict=True)) for r in cursor.fetchall()]

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [r for r in self.records() if r["exception_type"] == kind]

    def events(self, exception_id: UUID) -> list[str]:
        return [
            r[0]
            for r in self.f.db.execute(
                "SELECT event FROM pw.exception_evaluation WHERE exception_id = %s ORDER BY id",
                (exception_id,),
            ).fetchall()
        ]

    def bins(self, exception_id: UUID) -> set[UUID]:
        rows = self.f.db.execute(
            "SELECT bin_id FROM pw.exception_bin WHERE exception_id = %s", (exception_id,)
        ).fetchall()
        return {r[0] for r in rows}


@pytest.fixture
def site(connect_as: Connect) -> FixtureSite:
    return FixtureSite(connect_as)


# ---------------------------------------------------------------- impact score (6.1)


def test_the_impact_score_matches_the_spec_fixture(site: FixtureSite) -> None:
    """Semantic 6.1: 5 lines, due in 12 hours, 20,000 at risk, default settings: 57.2."""
    with tenant_session(role_pool("PW_EVALUATOR"), site.tenant) as conn:
        row = conn.execute(
            "SELECT score, components FROM sem.impact_score(%s, %s, 5, %s, 20000)",
            (site.settings(), site.as_of(), site.as_of() + 12 * H),
        ).fetchone()
    assert row is not None
    score, components = row
    assert score == Decimal("57.2")
    assert components["lines_normalized"] == 0.25 and components["urgency_normalized"] == 0.75
    assert components["value_normalized"] == 0.7168  # log10(20001) / log10(1000001) = 0.71684
    assert components["weight_lines"] == components["weight_urgency"] == components["weight_value"] == 1


def test_overdue_is_full_urgency_and_no_order_is_none(site: FixtureSite) -> None:
    with tenant_session(role_pool("PW_EVALUATOR"), site.tenant) as conn:
        overdue = conn.execute(
            "SELECT components FROM sem.impact_score(%s, %s, 1, %s, 0)",
            (site.settings(), site.as_of(), site.as_of() - H),
        ).fetchone()
        none = conn.execute(
            "SELECT score, components FROM sem.impact_score(%s, %s, 0, NULL, 0)",
            (site.settings(), site.as_of()),
        ).fetchone()
    assert overdue is not None and overdue[0]["urgency_normalized"] == 1
    assert none is not None and none[0] == 0 and none[1]["urgency_normalized"] == 0


# ---------------------------------------------------------------- one fixture per type


def test_each_rule_opens_its_exception_with_its_bins(site: FixtureSite) -> None:
    f, engine = site, Engine(site)
    now = f.as_of()
    prime, reserve = f.zone("P", zone_type="FAST_PICK", prime=True), f.zone("R")
    tiny = f.zone("T")
    # STOCKOUT_RISK and LOW_STOCK: 10 due in 2 hours, 4 in the pick face, 20 in reserve (fixable).
    a = f.product("A", value=50)
    pick_a, reserve_a = f.bin(prime, "P-A"), f.bin(reserve, "R-A", role="RESERVE")
    f.stock(pick_a, a, 4)
    f.stock(reserve_a, a, 20)
    f.policy(a, pick_a, 5, 30)
    f.line(f.order(deadline=now + 2 * H), a, 10)
    staging = f.bin(reserve, "S-1", role="STAGING")
    f.pick(a, 1, now - 24 * H, pick_a, staging)
    # DELAYED_ORDER: open, 3 hours past its deadline, 2 lines.
    late = f.order(deadline=now - 3 * H)
    f.line(late, a, 1, allocated=1)
    f.line(late, a, 2, allocated=2)
    # DEAD_STOCK and SLOW_IN_PRIME: never picked, received 60 days ago, in a prime bin.
    d = f.product("D", value=10)
    pick_d = f.bin(prime, "P-D")
    f.stock(pick_d, d, 7)
    f.movement("RECEIPT", d, 7, now - 60 * 24 * H, to_bin=pick_d)
    # CAPACITY_HOTSPOT: one bin far over capacity.
    c = f.product("C", volume_m3=1.0, value=1)
    over = f.bin(tiny, "T-1", role="RESERVE", volume_m3=1.0)
    f.stock(over, c, 3)

    assert engine.evaluate()[0] == "evaluated"
    kinds = {r["exception_type"] for r in engine.records()}
    assert kinds == {"STOCKOUT_RISK", "LOW_STOCK", "DELAYED_ORDER", "DEAD_STOCK", "SLOW_IN_PRIME",
                     "CAPACITY_HOTSPOT"}  # fmt: skip

    (risk,) = engine.of("STOCKOUT_RISK")
    assert risk["subject_key"] == f"product:{a}" and risk["critical"]  # due within 4 hours
    assert risk["score_components"]["lines"] == 1 and risk["score_components"]["value_at_risk"] == 300
    assert engine.bins(risk["id"]) == {pick_a}
    (low,) = engine.of("LOW_STOCK")
    assert low["subject_key"] == f"product:{a}:bin:{pick_a}" and engine.bins(low["id"]) == {pick_a}
    assert low["score_components"]["value_at_risk"] == 50  # (5 - 4) x 50
    (delayed,) = engine.of("DELAYED_ORDER")
    assert delayed["critical"] and delayed["score_components"]["urgency_normalized"] == 1
    assert delayed["score_components"]["lines"] == 2
    (slow,) = engine.of("SLOW_IN_PRIME")
    assert engine.bins(slow["id"]) == {pick_d} and slow["score_components"]["value_at_risk"] == 70
    (hot,) = engine.of("CAPACITY_HOTSPOT")
    assert hot["critical"] and engine.bins(hot["id"]) == {over}
    for record in engine.records():
        assert record["status"] == "OPEN" and engine.events(record["id"]) == ["OPENED"]


# ---------------------------------------------------------------- lifecycle (2.4)


@pytest.fixture
def low_stock(site: FixtureSite) -> tuple[Engine, UUID, UUID]:
    """One product below its pick minimum: 2 available against a minimum of 5."""
    engine = Engine(site)
    zone = site.zone("P", zone_type="FAST_PICK")
    product = site.product("L", value=10)
    pick = site.bin(zone, "P-L")
    site.stock(pick, product, 2)
    site.policy(product, pick, 5, 20)
    # Picked yesterday, so it is not dead stock as well.
    site.pick(product, 1, site.as_of() - 24 * H, pick, site.bin(zone, "S-1", role="STAGING"))
    return engine, product, pick


def set_stock(f: FixtureSite, bin_id: UUID, product: UUID, on_hand: int) -> None:
    f.db.execute(
        "UPDATE pw.inventory_balance SET on_hand = %s WHERE bin_id = %s AND product_id = %s",
        (on_hand, bin_id, product),
    )


def test_the_same_state_is_evaluated_once(low_stock: tuple[Engine, UUID, UUID]) -> None:
    engine, _, _ = low_stock
    assert engine.evaluate() == ("evaluated", 1, 0, 0)
    assert engine.evaluate() == ("unchanged", 0, 0, 0)
    engine.load()  # new data, same condition and score: no new record, no evaluation row
    assert engine.evaluate() == ("evaluated", 0, 0, 0)
    (record,) = engine.records()
    assert engine.events(record["id"]) == ["OPENED"]


def test_a_cleared_condition_resolves_with_evidence(low_stock: tuple[Engine, UUID, UUID]) -> None:
    engine, product, pick = low_stock
    engine.evaluate()
    set_stock(engine.f, pick, product, 9)
    assert engine.evaluate() == ("unchanged", 0, 0, 0), "the key has not moved: nothing re-evaluated"
    engine.load()
    assert engine.evaluate() == ("evaluated", 0, 0, 1)
    (record,) = engine.records(current_only=False)
    assert record["status"] == "RESOLVED" and record["resolution_reason"] == "CONDITION_CLEARED"
    assert engine.events(record["id"]) == ["OPENED", "RESOLVED"]


def test_acknowledged_stays_listed_and_keeps_its_status(low_stock: tuple[Engine, UUID, UUID]) -> None:
    engine, product, pick = low_stock
    engine.evaluate()
    engine.f.db.execute(  # acknowledge_exception() arrives in step 3
        "UPDATE pw.exception_record SET status = 'ACKNOWLEDGED', acknowledged_by = 'm',"
        " acknowledged_at = last_evaluated_at WHERE site_id = %s",
        (engine.f.site,),
    )
    set_stock(engine.f, pick, product, 1)  # worse: rescored
    engine.load()
    assert engine.evaluate() == ("evaluated", 0, 1, 0)
    (record,) = engine.records()
    assert record["status"] == "ACKNOWLEDGED"
    assert engine.events(record["id"]) == ["OPENED", "RESCORED"]


def test_a_returning_condition_opens_a_new_occurrence(low_stock: tuple[Engine, UUID, UUID]) -> None:
    engine, product, pick = low_stock
    engine.evaluate()
    set_stock(engine.f, pick, product, 9)
    engine.load()
    engine.evaluate()
    set_stock(engine.f, pick, product, 2)
    engine.load()
    assert engine.evaluate() == ("evaluated", 1, 0, 0)
    history = engine.records(current_only=False)
    assert sorted(r["status"] for r in history) == ["OPEN", "RESOLVED"]
    assert len({r["id"] for r in history}) == 2


def test_the_clock_boundary_is_part_of_the_key(low_stock: tuple[Engine, UUID, UUID]) -> None:
    engine, _, _ = low_stock
    engine.evaluate()
    engine.f.db.execute(
        "UPDATE pw.site_clock SET sim_time = sim_time + interval '5 minutes' WHERE site_id = %s",
        (engine.f.site,),
    )
    assert engine.evaluate()[0] == "unchanged"  # same 15-minute boundary
    engine.f.db.execute(
        "UPDATE pw.site_clock SET sim_time = sim_time + interval '15 minutes' WHERE site_id = %s",
        (engine.f.site,),
    )
    assert engine.evaluate()[0] == "evaluated"


def test_concurrent_triggers_evaluate_once(low_stock: tuple[Engine, UUID, UUID]) -> None:
    engine, _, _ = low_stock
    outcomes: list[str] = []

    def run() -> None:
        outcomes.append(engine.evaluate()[0])

    threads = [threading.Thread(target=run) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == ["evaluated", "unchanged", "unchanged", "unchanged"]
    assert len(engine.records()) == 1


def test_a_site_that_is_not_ready_is_not_evaluated(low_stock: tuple[Engine, UUID, UUID]) -> None:
    engine, _, _ = low_stock
    engine.f.db.execute("UPDATE pw.site SET data_status = 'REBUILDING' WHERE id = %s", (engine.f.site,))
    assert engine.evaluate() == ("not_ready", 0, 0, 0)
    assert engine.records() == []


# ---------------------------------------------------------------- isolation and roles


def test_another_tenant_sees_no_exceptions(low_stock: tuple[Engine, UUID, UUID], connect_as: Connect) -> None:
    engine, _, _ = low_stock
    engine.evaluate()
    other = FixtureSite(connect_as)
    with tenant_session(role_pool("PW_EVALUATOR"), other.tenant) as conn:
        assert conn.execute("SELECT count(*) FROM pw.exception_record").fetchone() == (0,)
        assert conn.execute("SELECT * FROM pw.evaluate_site_exceptions(%s)", (engine.f.site,)).fetchone() == (
            "not_ready",
            0,
            0,
            0,
        )  # the site is invisible to that tenant


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE pw.inventory_balance SET on_hand = 0",
        "DELETE FROM pw.exception_record",
        "UPDATE pw.exception_evaluation SET score = 0",
        'INSERT INTO pw.settings_version (tenant_id, site_id, version_no, schema_version, "values",'
        " created_by) VALUES (gen_random_uuid(), gen_random_uuid(), 9, 2, '{}', 'x')",
    ],
)
def test_the_evaluator_writes_only_the_exception_tables(site: FixtureSite, statement: str) -> None:
    with (
        pytest.raises(psycopg.errors.InsufficientPrivilege),
        tenant_session(role_pool("PW_EVALUATOR"), site.tenant) as conn,
    ):
        conn.execute(statement)


def test_the_agent_reader_cannot_write_exceptions(site: FixtureSite) -> None:
    with (
        pytest.raises(psycopg.errors.InsufficientPrivilege),
        tenant_session(role_pool("PW_AGENT_READ"), site.tenant) as conn,
    ):
        conn.execute("SELECT * FROM pw.evaluate_site_exceptions(%s)", (site.site,))


# ---------------------------------------------------------------- simulation reset


def test_a_new_simulation_run_resolves_the_old_runs_exceptions(low_stock: tuple[Engine, UUID, UUID]) -> None:
    engine, _, _ = low_stock
    f = engine.f
    run = f.db.execute(  # the fixture's run, active as after a completed bootstrap
        "UPDATE pw.simulation_run SET status = 'ACTIVE' WHERE site_id = %s RETURNING id", (f.site,)
    ).fetchone()
    assert run is not None
    engine.evaluate()
    f.db.execute("UPDATE pw.simulation_run SET status = 'ENDED', ended_at = now() WHERE id = %s", (run[0],))
    assert engine.evaluate() == ("evaluated", 1, 0, 1)  # the same condition, now under no run
    by_status = {r["status"]: r for r in engine.records(current_only=False)}
    assert by_status["RESOLVED"]["resolution_reason"] == "SIMULATION_RESET"
    assert by_status["OPEN"]["simulation_run_id"] is None


# ---------------------------------------------------------------- the worker


def test_the_worker_evaluates_each_site_once_per_state(low_stock: tuple[Engine, UUID, UUID]) -> None:
    from palletwise.evaluator.main import EvaluatorWorker

    engine, _, _ = low_stock
    worker = EvaluatorWorker(engine.f.tenant)
    assert worker.run_once() == {engine.f.site: "evaluated"}
    assert worker.run_once() == {engine.f.site: "unchanged"}
    assert worker.status() == {"ready": True, "rules": 6, "evaluations": 1}


def test_a_failed_evaluation_keeps_the_last_state_and_is_retried(
    low_stock: tuple[Engine, UUID, UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    from palletwise.evaluator import main

    engine, product, pick = low_stock
    worker = main.EvaluatorWorker(engine.f.tenant)
    worker.run_once()
    before = engine.records()
    set_stock(engine.f, pick, product, 9)
    engine.load()
    engine.f.db.execute("ALTER FUNCTION sem.low_stock(uuid, timestamptz, uuid) RENAME TO low_stock_gone")
    try:
        assert worker.run_once() == {engine.f.site: "failed"}
        assert engine.records() == before, "the failed evaluation rolled back"
        assert worker.status()["ready"] is False
        assert worker.run_once() == {engine.f.site: "waiting"}  # backing off
    finally:
        engine.f.db.execute("ALTER FUNCTION sem.low_stock_gone(uuid, timestamptz, uuid) RENAME TO low_stock")
    now = main.clock.monotonic()
    monkeypatch.setattr(main.clock, "monotonic", lambda: now + 3)
    assert worker.run_once() == {engine.f.site: "evaluated"}
    assert engine.records() == [] and worker.status()["ready"] is True
