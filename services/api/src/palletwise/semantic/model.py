"""Loads semantic/model.yaml once and fills its "how computed" templates."""

from datetime import datetime
from functools import cache
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, ConfigDict, Field

MODEL_FILE = Path(__file__).with_name("model.yaml")


class Measure(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    label: str
    unit: str
    how_computed: str


class ResultDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    measures: tuple[str, ...]
    how_computed: str


class QueryDimension(BaseModel):
    """A dimension the query language accepts (semantic spec 7.2)."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    label: str
    type: Literal["product", "text", "location", "time"]
    values: tuple[str, ...] | None = None  # a closed set, checked without the database


class QueryMeasure(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)
    measure_class: Literal["snapshot", "flow"] = Field(alias="class")
    dimensions: tuple[str, ...]
    requires: tuple[str, ...] = ()  # dimensions that must be present, e.g. product for bin-level picks
    available_from: str | None = None  # listed by the spec, built in a later slice


class QueryCatalogue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    dimensions: dict[str, QueryDimension]
    measures: dict[str, QueryMeasure]


class SemanticModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: int
    measures: dict[str, Measure]
    results: dict[str, ResultDefinition]
    query: QueryCatalogue

    def explain(self, result: str, **values: str) -> tuple[str, ...]:
        """The result's explanation filled with the actual parameters, then each measure's."""
        definition = self.results[result]
        return (
            definition.how_computed.format(**values),
            *(self.measures[m].how_computed for m in definition.measures),
        )


@cache
def semantic_model() -> SemanticModel:
    model = SemanticModel.model_validate(yaml.safe_load(MODEL_FILE.read_text(encoding="utf-8")))
    for name, result in model.results.items():
        unknown = set(result.measures) - set(model.measures)
        if unknown:
            raise ValueError(f"result {name} uses unknown measures {sorted(unknown)}")
    for name, measure in model.query.measures.items():
        if measure.available_from is None and name not in model.measures:
            raise ValueError(f"query measure {name} has no definition in measures")
        for dimension in (*measure.dimensions, *measure.requires):
            if dimension not in model.query.dimensions:
                raise ValueError(f"query measure {name} uses unknown dimension {dimension}")
    return model


def site_time(moment: datetime, time_zone: str) -> str:
    """Site-local time as people read it, e.g. "06:00, 1 Apr 2026 (Asia/Colombo)"."""
    local = moment.astimezone(ZoneInfo(time_zone))
    return f"{local:%H:%M}, {local.day} {local:%b %Y} ({time_zone})"
