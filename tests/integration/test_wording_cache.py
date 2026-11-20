"""The answer-wording cache store (agent spec 14b, Slice 2 D4, step 7c) on PostgreSQL."""

import hashlib
from collections.abc import Callable
from datetime import timedelta
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from support import provision_site

from palletwise.api.copilot import WordingCacheAdapter
from palletwise.db.cache_repo import WordingCacheStore
from palletwise.db.connection import role_pool, tenant_session

pytestmark = pytest.mark.integration

Connect = Callable[[str], psycopg.Connection]


def key(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def test_an_entry_is_read_back_in_its_tenant_and_never_in_another(connect_as: Connect) -> None:
    store = WordingCacheStore(role_pool("PW_CACHE"))
    tenant_a, site_a = provision_site(connect_as)
    tenant_b, _ = provision_site(connect_as)
    store.put(tenant_a, site_a, key("q"), "get_low_stock", "37 products are low.")
    assert store.get(tenant_a, key("q")) == "37 products are low."
    assert store.get(tenant_b, key("q")) is None  # forced row-level security
    with tenant_session(role_pool("PW_CACHE"), tenant_b) as conn:
        assert conn.execute("SELECT count(*) FROM pw.wording_cache").fetchone() == (0,)


def test_an_expired_entry_is_never_returned(connect_as: Connect) -> None:
    store = WordingCacheStore(role_pool("PW_CACHE"), ttl=timedelta(seconds=-1))
    tenant, site = provision_site(connect_as)
    store.put(tenant, site, key("old"), "get_low_stock", "stale wording")
    assert store.get(tenant, key("old")) is None


def test_a_tenant_keeps_at_most_its_bound_least_recently_used_first(connect_as: Connect) -> None:
    store = WordingCacheStore(role_pool("PW_CACHE"), max_entries=2)
    tenant, site = provision_site(connect_as)
    for name in ("one", "two"):
        store.put(tenant, site, key(name), "get_low_stock", name)
    assert store.get(tenant, key("one")) == "one"  # used: kept
    store.put(tenant, site, key("three"), "get_low_stock", "three")
    assert store.get(tenant, key("two")) is None  # the least recently used went first
    assert store.get(tenant, key("one")) == "one" and store.get(tenant, key("three")) == "three"


def test_the_cache_role_reaches_only_its_table(connect_as: Connect) -> None:
    tenant, _ = provision_site(connect_as)
    with (
        pytest.raises(psycopg.errors.InsufficientPrivilege),
        tenant_session(role_pool("PW_CACHE"), tenant) as conn,
    ):
        conn.execute("SELECT count(*) FROM pw.product")


def test_the_key_includes_the_semantic_model_version(monkeypatch: pytest.MonkeyPatch) -> None:
    from palletwise.api import copilot

    material: dict[str, Any] = {"tool": "get_low_stock", "settings": str(uuid4())}
    before = WordingCacheAdapter.key(material)

    class Bumped:
        version = 999

    monkeypatch.setattr(copilot, "semantic_model", lambda: Bumped())
    assert WordingCacheAdapter.key(material) != before
