"""Regressions for the Slice 1 step 8 code review findings that need no database."""

import asyncio
import base64
import os
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest

from palletwise.auth.crypto import KeyRing, KeyRingError
from palletwise.db.agent_repo import AgentRecord
from palletwise.tools.catalog import v1_tools
from palletwise.tools.executor import Outcome, RunContext, RunStopped, ToolExecutor


@dataclass
class Audit:
    entries: list[Any] = field(default_factory=lambda: [])

    def append(self, tenant: UUID, entry: Any) -> None:
        self.entries.append(entry)


class BrokenRegistry:
    def load(self, tenant: UUID, agent_id: str) -> AgentRecord | None:
        raise ConnectionError("registry down")


class ActiveRegistry:
    def load(self, tenant: UUID, agent_id: str) -> AgentRecord | None:
        return AgentRecord(
            "copilot", "1", "ACTIVE", ("inventory.read",), "gemini-flash", None, "system.v1", 5, 1000
        )


class BrokenLimiter:
    def allow(self, tenant: UUID, subject: str, key: str, limit: int) -> bool:
        raise ConnectionError("limiter down")


def context() -> RunContext:
    site = uuid4()
    return RunContext(uuid4(), "ab" * 16, "user-1", "warehouse_manager", uuid4(), (site,), site)


def executor(agents: Any, audit: Audit, limiter: Any = None) -> ToolExecutor:
    return ToolExecutor(v1_tools(), agents, None, audit, None, limiter)  # type: ignore[arg-type]


def test_an_unreachable_registry_stops_the_run_and_is_still_audited() -> None:
    audit = Audit()
    with pytest.raises(RunStopped) as stopped:
        executor(BrokenRegistry(), audit).execute(context(), "find_product_locations", {"sku": "SKU-102"})
    assert stopped.value.reason == "registry_unavailable"
    assert [(e.action, e.outcome) for e in audit.entries] == [("tool.call", "ERROR")]


def test_an_unreachable_rate_limiter_fails_closed_and_is_audited() -> None:
    audit = Audit()
    result = executor(ActiveRegistry(), audit, BrokenLimiter()).execute(
        context(), "find_product_locations", {"sku": "SKU-102"}
    )
    assert result.outcome is Outcome.ERROR and result.output is None
    assert [(e.action, e.outcome) for e in audit.entries] == [("tool.call", "ERROR")]


def test_a_value_sealed_under_a_removed_or_wrong_key_raises_a_key_ring_error() -> None:
    def key() -> str:
        return base64.b64encode(os.urandom(32)).decode()

    old = KeyRing(f"k1:{key()}")
    sealed = old.seal("refresh-token", b"session")
    with pytest.raises(KeyRingError):
        KeyRing(f"k2:{key()}").open(sealed, b"session")  # key id removed
    with pytest.raises(KeyRingError):
        KeyRing(f"k1:{key()}").open(sealed, b"session")  # same id, different key
    with pytest.raises(KeyRingError):
        old.open(sealed, b"another-session")  # rebound to another session


def test_shutdown_waits_for_run_records_before_closing_pools() -> None:
    from palletwise.api.copilot import CopilotServices

    async def scenario() -> list[str]:
        done: list[str] = []
        services = CopilotServices(None, None, None, None, None, None, None)  # type: ignore[arg-type]

        async def run() -> None:
            await asyncio.sleep(60)

        async def write_record() -> None:
            await asyncio.sleep(0.2)
            done.append("record")

        task = asyncio.create_task(run())
        record = asyncio.ensure_future(write_record())
        services.active[uuid4()] = task
        services.finishing.add(record)
        record.add_done_callback(services.finishing.discard)
        await services.drain(timeout=5)
        done.append(f"task cancelled={task.cancelled()}")
        return done

    assert asyncio.run(scenario()) == ["record", "task cancelled=True"]
