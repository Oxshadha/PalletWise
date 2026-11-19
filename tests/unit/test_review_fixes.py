"""Regression tests for code-review findings on Slice 0 (no database needed)."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict

from palletwise.db.connection import dsn
from palletwise.settings import DatabaseSettings


def test_dsn_escapes_awkward_passwords() -> None:
    password = "my pass'with=odd\\chars"
    parsed = conninfo_to_dict(dsn(DatabaseSettings(host="db", port=5432, name="pw"), "pw_app", password))
    assert parsed["password"] == password
    assert parsed["user"] == "pw_app"
    assert parsed["host"] == "db"


class _FakePool:
    @contextmanager
    def connection(self, timeout: float | None = None) -> Iterator[object]:
        del timeout
        yield object()


def test_site_status_is_404_before_the_site_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    from palletwise.api import app as api

    monkeypatch.setattr(api, "role_pool", lambda _prefix: _FakePool())
    monkeypatch.setattr(api.status_repo, "site_status", lambda *_args, **_kw: None)  # pyright: ignore[reportUnknownLambdaType]
    response = TestClient(api.app).get("/api/sites/current/status")
    assert response.status_code == 404


@pytest.fixture
def building_admin(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any]:
    """A SimAdmin whose run never finishes loading, without a database."""
    from palletwise.db.site_repo import RunState
    from palletwise.simadmin import main as simadmin

    run = RunState(
        run_id=uuid.uuid4(),
        status="BUILDING",
        history_loaded=False,
        dataset_hash=None,
        seed=42,
        dataset_version="sim-v1",
    )
    admin: Any = object.__new__(simadmin.SimAdmin)
    admin._sim = simadmin.SimulationSettings()
    monkeypatch.setattr(simadmin.SimAdmin, "latest_run", lambda _self: run)
    monkeypatch.setattr(simadmin.clock, "sleep", lambda _s: None)
    return admin, run


def test_simadmin_wait_fails_fast_on_simulator_error(
    monkeypatch: pytest.MonkeyPatch, building_admin: tuple[Any, Any]
) -> None:
    from palletwise.simadmin import main as simadmin

    admin, run = building_admin
    monkeypatch.setattr(simadmin, "simulator_error", lambda _sim: "out of memory")
    with pytest.raises(RuntimeError, match="out of memory"):
        simadmin.SimAdmin.wait_for_load(admin, run)


def test_simadmin_wait_times_out(monkeypatch: pytest.MonkeyPatch, building_admin: tuple[Any, Any]) -> None:
    from palletwise.simadmin import main as simadmin

    admin, run = building_admin
    ticks = iter([0.0, 10_000.0])
    monkeypatch.setattr(simadmin, "simulator_error", lambda _sim: None)
    monkeypatch.setattr(simadmin.clock, "monotonic", lambda: next(ticks))
    with pytest.raises(TimeoutError):
        simadmin.SimAdmin.wait_for_load(admin, run)
