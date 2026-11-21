"""Scenarios through the real path (slice-4-acceptance step 3, simulator spec 6).

A scenario stages its events in the simulator's input log; the period reaches PalletWise through
the outbox and the connector; the exception engine recalculates as for any data. Expectations are
checked on the recalculated exceptions, never on a direct effect.
"""

from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from support import CLOCK_START, HISTORY_DAYS, load_site

from palletwise.db import dataset_hash, ingest_repo, sim_source, site_repo
from palletwise.db.connection import role_pool, tenant_session
from pw_simulator import scenarios, store
from pw_simulator.history import RunParams, generate

pytestmark = pytest.mark.integration

Connect = Callable[[str], psycopg.Connection]


class Site:
    def __init__(self, connect_as: Connect) -> None:
        self.connect_as = connect_as
        self.tenant = uuid4()
        self.site, self.connector, self.run = load_site(connect_as, self.tenant)
        admin = connect_as("pw_sim_admin")
        digest, _ = dataset_hash.compute(admin, self.tenant, self.site)
        site_repo.complete_load(admin, self.tenant, self.site, self.run, digest)
        # Autocommit, as the simulator service: each store operation is its own transaction.
        self.sim = connect_as("sim_owner")
        self.sim.autocommit = True

    def ingest(self) -> None:
        source, dest = self.connect_as("sim_outbox_reader"), self.connect_as("pw_ingest")
        while batch := sim_source.pull(
            source, self.run, ingest_repo.read_cursor(dest, self.tenant, self.connector, self.run), 5000
        ):
            ingest_repo.apply_batch(dest, self.tenant, self.site, self.connector, batch)

    def evaluate(self) -> None:
        with tenant_session(role_pool("PW_EVALUATOR"), self.tenant) as conn:
            conn.execute("SELECT * FROM pw.evaluate_site_exceptions(%s)", (self.site,)).fetchone()

    def scenario(self, scenario_id: str) -> dict[str, object]:
        result = store.run_scenario(self.sim, str(self.run), scenario_id, f"scn-{uuid4().hex}")
        self.ingest()
        self.evaluate()
        return result

    def shift(self, by: timedelta) -> None:
        now = datetime.fromisoformat(store.run_state(self.sim, str(self.run))["emitted_until"])
        store.shift(self.sim, str(self.run), now + by, f"shift-{uuid4().hex}")
        self.ingest()
        self.evaluate()

    def exceptions(self, kind: str) -> list[tuple[str, bool]]:
        with tenant_session(role_pool("PW_AGENT_READ"), self.tenant, read_snapshot=True) as conn:
            rows = conn.execute(
                "SELECT p.sku, x.critical FROM sem.exception x"
                " LEFT JOIN sem.product p ON p.product_id = x.product_id"
                " WHERE x.site_id = %s AND x.exception_type = %s AND x.status <> 'RESOLVED'",
                (self.site, kind),
            ).fetchall()
        return [(r[0], r[1]) for r in rows]


def test_a_large_outlet_order_opens_a_fixable_stockout_risk(connect_as: Connect) -> None:
    site = Site(connect_as)
    result = site.scenario("outlet_window_at_risk")
    assert result["events"] == 2 and result["records"]
    with tenant_session(role_pool("PW_APP"), site.tenant) as conn:
        order = conn.execute(
            "SELECT o.order_no FROM pw.outbound_order o JOIN pw.customer c ON c.id = o.customer_id"
            " WHERE o.site_id = %s AND c.code = 'OUT003' AND o.order_no LIKE '%%-S%%'",
            (site.site,),
        ).fetchone()
    assert order is not None, "the staged order reached PalletWise through the connector"
    risks = dict(site.exceptions("STOCKOUT_RISK"))
    # Fixable (reserve covers it) and due in 6 hours, so not critical (agent spec 10).
    assert risks.get("SKU-258") is False


def test_the_dead_stock_anchors_show_in_prime(connect_as: Connect) -> None:
    site = Site(connect_as)
    site.scenario("dead_stock_in_prime")
    assert len(site.exceptions("DEAD_STOCK")) == 6
    assert site.exceptions("SLOW_IN_PRIME")


def storable(site: Site) -> int:
    with tenant_session(role_pool("PW_AGENT_READ"), site.tenant, read_snapshot=True) as conn:
        row = conn.execute(
            "SELECT coalesce(sum(s.on_hand), 0) FROM sem.stock s JOIN sem.location l ON l.bin_id = s.bin_id"
            " WHERE s.site_id = %s AND l.storable",
            (site.site,),
        ).fetchone()
    return int(row[0]) if row else 0


def test_a_monsoon_slowdown_lowers_stock_against_the_same_run_without_it(connect_as: Connect) -> None:
    calm, monsoon = Site(connect_as), Site(connect_as)
    monsoon.scenario("monsoon_slowdown")
    calm.shift(timedelta(minutes=15))  # the same first tick as the scenario step
    for site in (calm, monsoon):
        site.shift(timedelta(days=3))
    assert storable(monsoon) < storable(calm)
    assert len(monsoon.exceptions("LOW_STOCK")) >= len(calm.exceptions("LOW_STOCK"))


def test_a_scenario_survives_later_shifts_and_steps_equal_one_shift(connect_as: Connect) -> None:
    """The input log is replayed on every regeneration: two runs with the same seed and the same
    scenario, shifted in different steps, write the same outbox."""
    sim = connect_as("sim_owner")
    store.ensure_schema(sim)
    outboxes: list[list[tuple[object, ...]]] = []
    for steps in ((48,), (5, 19, 24)):
        run = str(uuid4())
        params = RunParams(run, 42, "test", CLOCK_START, history_days=HISTORY_DAYS)
        store.write_run(sim, params, generate(params))
        store.run_scenario(sim, run, "outlet_window_at_risk", f"s-{run[:8]}")
        edge = datetime.fromisoformat(store.run_state(sim, run)["emitted_until"])
        for i, hours in enumerate(steps):
            edge += timedelta(hours=hours)
            store.shift(sim, run, edge, f"t-{i}-{run[:8]}")
        outboxes.append(
            sim.execute(
                "SELECT seq, record_type, sim_time, payload FROM sim.outbox WHERE run_id = %s ORDER BY seq",
                (run,),
            ).fetchall()
        )
    assert outboxes[0] == outboxes[1]
    staged = [r for r in outboxes[0] if r[1] == "ORDER" and str(r[3]["order_no"]).endswith("-S2")]
    assert staged, "the staged order is in the outbox"


def test_unknown_scenarios_are_refused_and_the_same_key_runs_once(connect_as: Connect) -> None:
    sim = connect_as("sim_owner")
    store.ensure_schema(sim)
    run = str(uuid4())
    params = RunParams(run, 42, "test", CLOCK_START, history_days=HISTORY_DAYS)
    store.write_run(sim, params, generate(params))
    with pytest.raises(scenarios.ScenarioRefused):
        store.run_scenario(sim, run, "no_such_scenario", "unknown-scn-key")
    first = store.run_scenario(sim, run, "festival_rush", "same-scn-key")
    assert store.run_scenario(sim, run, "festival_rush", "same-scn-key") == first
    logged = sim.execute("SELECT count(*) FROM sim.input_log WHERE run_id = %s", (run,)).fetchone()
    assert logged == (1,)
