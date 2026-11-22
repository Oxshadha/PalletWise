"""The WMS connector contract and the REST connector (ADR 0003), against a fake Acme WMS API."""

import json
from typing import Any

import httpx
import pytest

from palletwise.connectors import build
from palletwise.connectors.mapping import load
from palletwise.connectors.rest import RestConnector

ITEMS_PAGE_1: dict[str, Any] = {
    "results": [
        {
            "itemId": "A-1", "itemCode": "sku-102", "description": "Basmati Rice 5kg", "brandName": "Harvest",
            "group": {"code": "RICE"}, "price": {"amount": "1850.5"}, "dims": {"volumeCm3": 4675},
            "weightGrams": 5100, "storage": "amb", "modifiedAt": "2026-04-01T05:00:00Z",
        }
    ],
    "paging": {"nextToken": "p2"},
}  # fmt: skip
ITEMS_PAGE_2: dict[str, Any] = {
    "results": [
        {
            "itemId": "A-2", "itemCode": "sku-201", "description": "Yoghurt 1l", "brandName": "Dairy Co",
            "group": {"code": "DAIRY"}, "price": {"amount": 420}, "dims": {"volumeCm3": 1100},
            "weightGrams": 1050, "storage": "chl", "unitOfMeasure": "EA",
            "modifiedAt": "2026-04-01T05:10:00Z",
        }
    ],
    "paging": {"nextToken": None},
}  # fmt: skip
ORDERS: dict[str, Any] = {
    "results": [
        {
            "orderNumber": "SO-20260401-OUT022", "shipTo": {"code": "OUT022"},
            "requestedDelivery": "2026-04-02",
            "cutoffTime": "2026-04-01T20:00:00+05:30", "updatedAt": "2026-04-01T05:05:00Z",
        }
    ],
    "paging": {},
}  # fmt: skip
LINES: dict[str, Any] = {
    "results": [
        {
            "lineId": "SO-20260401-OUT022-01", "orderNumber": "SO-20260401-OUT022", "lineNumber": "1",
            "itemCode": "sku-102", "quantity": {"ordered": 12}, "updatedAt": "2026-04-01T05:06:00Z",
        }
    ],
    "paging": {},
}  # fmt: skip


class FakeAcme:
    """An in-memory Acme WMS: two pages of items, one order, one line; one 429 on first contact."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.throttled = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.throttled:
            self.throttled = True
            return httpx.Response(429, headers={"Retry-After": "2"})
        path, params = request.url.path, request.url.params
        if path.endswith("/items"):
            if params.get("modifiedSince"):
                return httpx.Response(200, json={"results": [], "paging": {}})
            return httpx.Response(200, json=ITEMS_PAGE_2 if params.get("pageToken") == "p2" else ITEMS_PAGE_1)
        if path.endswith("/outbound-orders"):
            return httpx.Response(
                200, json={"results": [], "paging": {}} if params.get("updatedSince") else ORDERS
            )
        if path.endswith("/outbound-order-lines"):
            return httpx.Response(
                200, json={"results": [], "paging": {}} if params.get("updatedSince") else LINES
            )
        return httpx.Response(404)


@pytest.fixture
def acme(monkeypatch: pytest.MonkeyPatch) -> tuple[RestConnector, FakeAcme, list[float]]:
    monkeypatch.setenv("ACME_WMS_TOKEN", "secret-token")
    fake = FakeAcme()
    waits: list[float] = []
    return (
        RestConnector(load("acme-wms"), transport=httpx.MockTransport(fake), sleep=waits.append),
        fake,
        waits,
    )


def test_a_different_api_becomes_the_same_canonical_records(
    acme: tuple[RestConnector, FakeAcme, list[float]],
) -> None:
    connector, _, _ = acme
    batch = connector.pull(None, limit=100)
    by_type: dict[str, list[dict[str, Any]]] = {}
    for r in batch.records:
        by_type.setdefault(r.record_type, []).append(r.payload)
        assert r.source_system == "acme-wms"
    assert by_type["PRODUCT"][0] == {
        "sku": "SKU-102", "name": "Basmati Rice 5kg", "brand": "Harvest", "category": "RICE", "uom": "EA",
        "unit_value": 1850.5, "unit_volume_m3": pytest.approx(0.004675), "unit_weight_kg": pytest.approx(5.1),
        "temperature_class": "AMBIENT",
    }  # fmt: skip
    assert by_type["PRODUCT"][1]["temperature_class"] == "CHILLED"
    assert by_type["ORDER"] == [
        {"order_no": "SO-20260401-OUT022", "customer": "OUT022", "delivery_date": "2026-04-02",
         "dispatch_deadline": "2026-04-01T20:00:00+05:30"}
    ]  # fmt: skip
    assert by_type["ORDER_LINE"][0] == {
        "order_no": "SO-20260401-OUT022", "line_no": 1, "sku": "SKU-102", "qty_ordered": 12,
        "external_id": "SO-20260401-OUT022-01",
    }  # fmt: skip
    # Oldest first across streams: item, order, line, then the later item.
    assert [r.external_id for r in batch.records] == [
        "A-1",
        "SO-20260401-OUT022",
        "SO-20260401-OUT022-01",
        "A-2",
    ]


def test_paging_retries_and_the_credential(acme: tuple[RestConnector, FakeAcme, list[float]]) -> None:
    connector, fake, waits = acme
    connector.pull(None, limit=100)
    assert waits == [2.0]  # the 429's Retry-After was honoured, then the read continued
    assert all(r.headers["Authorization"] == "Bearer secret-token" for r in fake.requests)
    item_pages = [r.url.params.get("pageToken") for r in fake.requests if r.url.path.endswith("/items")]
    assert item_pages[-2:] == [None, "p2"]


def test_the_cursor_resumes_from_the_last_change(acme: tuple[RestConnector, FakeAcme, list[float]]) -> None:
    connector, fake, _ = acme
    first = connector.pull(None, limit=100)
    positions = json.loads(first.cursor)
    assert positions["PRODUCT"]["since"].startswith("2026-04-01T05:10")
    fake.requests.clear()
    second = connector.pull(first.cursor, limit=100)
    assert second.records == []
    assert fake.requests[0].url.params["modifiedSince"] == positions["PRODUCT"]["since"]


def test_health_and_the_registry(acme: tuple[RestConnector, FakeAcme, list[float]]) -> None:
    connector, _, _ = acme
    health = connector.health()
    assert health.ok and set(health.streams) == {"PRODUCT", "ORDER", "ORDER_LINE"}
    with pytest.raises(ValueError, match="unknown connector kind"):
        build({"kind": "carrier-pigeon"})
    assert build({"kind": "rest", "mapping": "acme-wms"}).source_system == "acme-wms"
