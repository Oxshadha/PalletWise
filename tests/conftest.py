"""Shared fixtures. Integration tests use a real PostgreSQL 17 with the real roles and RLS."""

import os
from collections.abc import Callable, Generator
from typing import Any

import psycopg
import pytest

from palletwise.db.bootstrap import LOGIN_ROLES
from palletwise.db.connection import dsn

ROLE_PASSWORDS = {role: f"test-{role}" for role in LOGIN_ROLES}

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
