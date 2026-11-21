"""get_open_exceptions (slice-3-acceptance D5, step 7) through the real executor.

The copilot reads the inbox's own stored records: the same exceptions, in the same order, with the
same score components and highlights. Nothing is recalculated, and nothing can be written.
"""

from collections.abc import Callable
from datetime import timedelta
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from fixture_site import FixtureSite
from test_exception_engine import Engine
from test_rule_tools import ok
from test_rule_tools import run as run  # the executor fixture

from palletwise.agent.templates import template_answer
from palletwise.contracts.results import ResultStatus
from palletwise.db.connection import role_pool, tenant_session
from palletwise.semantic import exceptions
from palletwise.tools.catalog import v1_tools
from palletwise.tools.executor import Outcome

pytestmark = pytest.mark.integration

Connect = Callable[[str], psycopg.Connection]
H = timedelta(hours=1)


def scenario(connect_as: Connect) -> FixtureSite:
    """A stockout risk and low stock (product A), a delayed order, dead and slow stock (D)."""
    f = FixtureSite(connect_as)
    now = f.as_of()
    prime, reserve = f.zone("P", zone_type="FAST_PICK", prime=True), f.zone("R")
    a = f.product("A", value=50)
    pick_a = f.bin(prime, "P-A")
    f.stock(pick_a, a, 4)
    f.stock(f.bin(reserve, "R-A", role="RESERVE"), a, 20)
    f.policy(a, pick_a, 5, 30)
    f.line(f.order(deadline=now + 2 * H), a, 10)
    f.pick(a, 1, now - 24 * H, pick_a, f.bin(reserve, "S-1", role="STAGING"))
    late = f.order(deadline=now - 3 * H)
    f.line(late, a, 1, allocated=1)
    d = f.product("D", value=10)
    pick_d = f.bin(prime, "P-D")
    f.stock(pick_d, d, 7)
    f.movement("RECEIPT", d, 7, now - 60 * 24 * H, to_bin=pick_d)
    assert Engine(f).evaluate()[0] == "evaluated"
    return f


@pytest.fixture
def site(connect_as: Connect) -> FixtureSite:
    return scenario(connect_as)


def inbox(f: FixtureSite) -> list[Any]:
    with tenant_session(role_pool("PW_AGENT_READ"), f.tenant, read_snapshot=True) as conn:
        return exceptions.inbox(conn, f.site).exceptions


def test_the_copilot_sees_the_inbox_in_the_inbox_order(run: Any, site: FixtureSite) -> None:
    expected = inbox(site)
    envelope = ok(run(site, "get_open_exceptions", limit=20))
    assert [r["score"] for r in envelope.rows] == [x.score for x in expected]
    assert [r["rank"] for r in envelope.rows] == list(range(1, len(expected) + 1))
    assert envelope.normalized_request["open_exceptions"] == len(expected)
    # The components behind each score are the stored ones, not a second calculation.
    for row, x in zip(envelope.rows, expected, strict=True):
        assert (row["lines"], row["value_at_risk"]) == (x.components["lines"], x.components["value_at_risk"])
    want = {(h.target_type, h.target_id) for x in expected for h in x.highlights}
    assert {(h.target_type, h.target_id) for h in envelope.highlights} == want
    assert template_answer("get_open_exceptions", envelope).startswith(f"{len(expected)} exceptions are open")


def test_a_type_filter_keeps_the_inbox_ranks(run: Any, site: FixtureSite) -> None:
    everything = inbox(site)
    ranks = {x.exception_id: i for i, x in enumerate(everything, start=1)}
    envelope = ok(run(site, "get_open_exceptions", exception_type="DEAD_STOCK"))
    (dead,) = [x for x in everything if x.exception_type == "DEAD_STOCK"]
    assert [r["rank"] for r in envelope.rows] == [ranks[dead.exception_id]]
    assert ok(run(site, "get_open_exceptions", limit=1)).truncated  # more exist than were shown


def test_a_page_context_exception_is_read_only_within_the_callers_site(
    run: Any, site: FixtureSite, connect_as: Connect
) -> None:
    first = inbox(site)[0]
    envelope = ok(run(site, "get_open_exceptions", exception_id=str(first.exception_id)))
    assert envelope.row_count == 1 and envelope.rows[0]["rank"] == 1
    # Another tenant's exception id, or a made-up one, is never resolved (the id is not trusted).
    other = scenario(connect_as)
    for foreign in (inbox(other)[0].exception_id, uuid4()):
        refused = ok(run(site, "get_open_exceptions", exception_id=str(foreign)))
        assert refused.status is ResultStatus.NOT_FOUND and refused.rows == ()


def test_invalid_input_is_refused_and_the_tool_is_read_only(run: Any, site: FixtureSite) -> None:
    for bad in ({"limit": 0}, {"limit": 21}, {"exception_type": "EVERYTHING"},
                {"exception_type": "LOW_STOCK", "exception_id": str(uuid4())}):  # fmt: skip
        assert run(site, "get_open_exceptions", **bad).outcome is Outcome.VALIDATION_FAILED, bad
    spec = next(t for t in v1_tools() if t.name == "get_open_exceptions")
    assert (spec.scope, spec.side_effect) == ("inventory.read", "read")
    # No agent tool acknowledges or approves anything (product owner boundary for step 7).
    assert not [t.name for t in v1_tools() if t.side_effect != "read" or "acknowledge" in t.name]


def test_no_open_exception_says_so(run: Any, connect_as: Connect) -> None:
    empty = FixtureSite(connect_as)
    Engine(empty).evaluate()
    envelope = ok(run(empty, "get_open_exceptions"))
    assert envelope.status is ResultStatus.NO_RESULTS and envelope.message == "No exception is open."
