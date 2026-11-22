"""WMS connectors (ADR 0003): one contract, one adapter per kind of source.

connector = build({"kind": "rest", "mapping": "acme-wms"})
batch = connector.pull(cursor=None, limit=500)
"""

from collections.abc import Callable
from typing import Any

from palletwise.connectors.base import Batch, CanonicalRecord, Health, WmsConnector
from palletwise.connectors.mapping import load
from palletwise.connectors.rest import RestConnector

Factory = Callable[[dict[str, Any]], WmsConnector]
_KINDS: dict[str, Factory] = {}


def register(kind: str, factory: Factory) -> None:
    """Adds a kind of connector (REST, change data capture, file drop, ...)."""
    _KINDS[kind] = factory


def build(config: dict[str, Any]) -> WmsConnector:
    """A connector from its configuration: {"kind": ..., plus that kind's settings}."""
    kind = config.get("kind", "rest")
    if kind not in _KINDS:
        raise ValueError(f"unknown connector kind {kind!r}; known: {', '.join(sorted(_KINDS))}")
    return _KINDS[kind](config)


register("rest", lambda config: RestConnector(load(config["mapping"])))

__all__ = ["Batch", "CanonicalRecord", "Health", "WmsConnector", "build", "register"]
