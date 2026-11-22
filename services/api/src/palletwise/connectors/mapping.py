"""Declarative mapping of a WMS's REST API onto canonical records (ADR 0003).

One YAML file per WMS: where each kind of record lives, how to page through it, and which source
field becomes which canonical field. A new warehouse system is a new mapping file.
"""

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field

MAPPINGS = Path(__file__).with_name("mappings")


class Auth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["bearer", "header", "none"] = "bearer"
    header: str = "Authorization"
    env: str | None = None  # the credential is read from this environment variable, never stored


class Paging(BaseModel):
    model_config = ConfigDict(extra="forbid")

    style: Literal["cursor", "page"] = "cursor"
    param: str = "cursor"  # the query parameter carrying the cursor or the page number
    size_param: str = "limit"
    size: int = Field(default=200, ge=1, le=5000)
    next: str = "next"  # cursor style: where the next cursor is in the response (dot path)


class FieldRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str  # dot path in the source item, e.g. "item.code" or "lines.0.qty"
    as_: Literal["str", "int", "float", "upper", "date", "datetime", "bool"] = Field("str", alias="as")
    values: dict[str, str] | None = None  # a code table: source value to canonical value
    scale: float | None = None  # a unit change, e.g. 0.001 for grams to kilograms
    default: Any = None


class Stream(BaseModel):
    """One kind of record: one endpoint of the WMS mapped to one canonical record type."""

    model_config = ConfigDict(extra="forbid")

    record_type: str
    path: str  # the endpoint, relative to the base URL
    items: str = "data"  # where the list of items is in the response (dot path; "" for the root)
    id: str  # the source item's id (dot path)
    occurred_at: str  # the source item's change time (dot path)
    since_param: str | None = None  # incremental reads: the parameter taking the last change time
    fields: dict[str, FieldRule | str]


class Mapping(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_system: str
    base_url: str
    auth: Auth = Auth()
    paging: Paging = Paging()
    timeout_seconds: float = 15.0
    streams: list[Stream]


def load(name_or_path: str | Path) -> Mapping:
    """A mapping by name (connectors/mappings/<name>.yaml) or by path."""
    path = Path(name_or_path)
    if not path.suffix:
        path = MAPPINGS / f"{name_or_path}.yaml"
    return Mapping.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def dig(item: Any, path: str) -> Any:
    """The value at a dot path; list positions are numbers ("lines.0.qty"). None when absent."""
    value: Any = item
    for part in path.split(".") if path else []:
        if isinstance(value, dict):
            value = cast(dict[str, Any], value).get(part)
        elif isinstance(value, list) and part.isdigit():
            items = cast(list[Any], value)
            value = items[int(part)] if int(part) < len(items) else None
        else:
            return None
    return value


def timestamp(value: Any) -> datetime:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, UTC)
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def convert(rule: FieldRule | str, item: Any) -> Any:
    """One canonical field from a source item, by its rule."""
    if isinstance(rule, str):
        rule = FieldRule.model_validate({"path": rule})
    value: Any = dig(item, rule.path)
    if value is None:
        return rule.default
    if rule.values is not None:
        value = rule.values.get(str(value), value)
    match rule.as_:
        case "int":
            return int(float(value))
        case "float":
            return float(value) * rule.scale if rule.scale is not None else float(value)
        case "upper":
            return str(value).upper()
        case "bool":
            return value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes", "y")
        case "date":
            return (value if isinstance(value, date) else timestamp(value).date()).isoformat()
        case "datetime":
            return timestamp(value).isoformat()
        case _:
            return str(value)
