"""Shared fixtures. Integration tests use a real PostgreSQL 17 with the real roles and RLS."""

import os
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

import psycopg
import pytest

from palletwise.db.bootstrap import LOGIN_ROLES
from palletwise.db.connection import dsn

ROLE_PASSWORDS = {role: f"test-{role}" for role in LOGIN_ROLES}

ROOT = Path(__file__).resolve().parents[1]
POSTGRES_IMAGE = os.environ.get("PW_TEST_POSTGRES_IMAGE", "postgres:17-alpine")


@pytest.fixture(scope="session")
def database() -> Generator[dict[str, Any]]:
    """Start PostgreSQL, bootstrap roles, apply migrations and load the calendar."""
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer(
        POSTGRES_IMAGE, username="postgres", password="postgres", dbname="palletwise"
    ) as pg:
        host = pg.get_container_host_ip()
        port = int(pg.get_exposed_port(5432))
        os.environ.update({"PW_DB_HOST": host, "PW_DB_PORT": str(port), "PW_DB_NAME": "palletwise"})
        os.environ.update({"PW_BOOTSTRAP_USER": "postgres", "PW_BOOTSTRAP_PASSWORD": "postgres"})
        for role, password in ROLE_PASSWORDS.items():
            os.environ[f"{role.upper()}_USER"] = role
            os.environ[f"{role.upper()}_PASSWORD"] = password

        from palletwise.db import migrate
        from palletwise.settings import DatabaseSettings

        db = DatabaseSettings()
        migrate.run_bootstrap(db)
        migrate.upgrade(db)
        migrate.run_calendar(db)
        yield {"host": host, "port": port, "settings": db}


@pytest.fixture
def connect_as(database: dict[str, Any]) -> Generator[Callable[[str], psycopg.Connection]]:
    """Open a connection as a given role; all are closed after the test."""
    opened: list[psycopg.Connection] = []

    def _connect(role: str) -> psycopg.Connection:
        password = "postgres" if role == "postgres" else ROLE_PASSWORDS[role]
        conn = psycopg.connect(dsn(database["settings"], role, password))
        opened.append(conn)
        return conn

    yield _connect
    for conn in opened:
        conn.close()


def pinned_image(name: str) -> str:
    """An image reference from infra/images.env, so tests use what the stack uses."""
    for line in (ROOT / "infra/images.env").read_text().splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip()
    raise AssertionError(f"{name} missing from infra/images.env")


@pytest.fixture(scope="session")
def opa_url() -> Generator[str]:
    """The real OPA serving policy/ (the same policy and data the stack loads)."""
    import time

    import httpx
    from testcontainers.core.container import DockerContainer

    container = (
        DockerContainer(pinned_image("OPA_IMAGE"))
        .with_command("run --server --addr=0.0.0.0:8181 /policy")
        .with_exposed_ports(8181)
        .with_volume_mapping(str(ROOT / "policy"), "/policy", "ro")
    )
    with container:
        url = f"http://{container.get_container_host_ip()}:{container.get_exposed_port(8181)}"
        deadline = time.monotonic() + 60
        while True:
            try:
                if httpx.get(f"{url}/health").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            assert time.monotonic() < deadline, "OPA did not start"
            time.sleep(0.5)
        yield url


@pytest.fixture(scope="session")
def connect_session(database: dict[str, Any]) -> Generator[Callable[[str], psycopg.Connection]]:
    """Like connect_as, for session- and module-scoped fixtures; closed at the end of the run."""
    opened: list[psycopg.Connection] = []

    def _connect(role: str) -> psycopg.Connection:
        password = "postgres" if role == "postgres" else ROLE_PASSWORDS[role]
        conn = psycopg.connect(dsn(database["settings"], role, password))
        opened.append(conn)
        return conn

    yield _connect
    for conn in opened:
        conn.close()
