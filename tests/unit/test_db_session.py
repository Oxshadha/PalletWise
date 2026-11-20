"""Tenant sessions leave pooled connections as they found them (code review, Slice 2)."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import uuid4

import pytest

from palletwise.db.connection import tenant_session


class FakeConnection:
    def __init__(self) -> None:
        self.prepare_threshold: int | None = 5
        self.statements: list[str] = []

    def execute(self, sql: str, params: Any = None) -> None:
        self.statements.append(sql)

    @contextmanager
    def transaction(self) -> Iterator[None]:
        yield


class FakePool:
    def __init__(self) -> None:
        self.conn = FakeConnection()

    @contextmanager
    def connection(self) -> Iterator[FakeConnection]:
        yield self.conn


def test_a_read_snapshot_turns_off_prepared_statements_only_for_its_own_session() -> None:
    pool = FakePool()
    with tenant_session(pool, uuid4(), read_snapshot=True) as conn:  # type: ignore[arg-type]
        assert conn.prepare_threshold is None
    assert pool.conn.prepare_threshold == 5


def test_the_setting_is_restored_when_the_session_fails() -> None:
    pool = FakePool()
    with pytest.raises(RuntimeError), tenant_session(pool, uuid4(), read_snapshot=True):  # type: ignore[arg-type]
        raise RuntimeError("tool failed")
    assert pool.conn.prepare_threshold == 5
