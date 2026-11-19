"""Runs the model-level comparison end to end with a scripted model (no cloud): the real API,
executor, policy, database and usage records, so the ADR 0001 measurement is checked without cost.

Usage: python tests/integration/eval_scripted.py <report dir>. Run in its own process, because the
standalone environment starts its own containers and sets the database settings.
"""

import re
import sys
from pathlib import Path
from typing import ClassVar

from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from evals.cases import load_set
from evals.environment import standalone
from evals.runner import ConfigRun, compare, write_model_report
from palletwise.agent.runtime import RuntimeConfig
from palletwise.llm import ResolvedModel

CASES = ("LOC-001", "LOC-007", "OOS-001")  # path A, path C, a decline


def primary(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """Chooses the location tool for a SKU, then answers without numbers; declines otherwise."""
    if any(isinstance(p, ToolReturnPart) for m in messages for p in getattr(m, "parts", [])):
        return ModelResponse(parts=[TextPart("SKU-102 is stored in the locations shown.")])
    found = re.search(r"SKU-\d+", str(messages[-1]))
    if found:
        return ModelResponse(parts=[ToolCallPart("find_product_locations", {"sku": found.group(0)})])
    return ModelResponse(parts=[TextPart("I can only help with warehouse questions.")])


def lite(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    return ModelResponse(parts=[TextPart("SKU-102 is stored in the locations shown.")])


class ScriptedProvider:
    """Logical names map to priced model ids, so costs are computed as for the real models."""

    models: ClassVar[dict[str, ResolvedModel]] = {
        "gemini-flash": ResolvedModel(FunctionModel(primary), "gemini-3.8-flash", "test"),
        "gemini-flash-lite": ResolvedModel(FunctionModel(lite), "gemini-3.5-flash-lite", "test"),
    }

    def model(self, logical: str) -> ResolvedModel | None:
        return self.models.get(logical)


def main(directory: Path) -> None:
    cases = [c for c in load_set("core")[0] if c.id in CASES]
    configs = [RuntimeConfig(), RuntimeConfig()]  # an A/A run: the comparison path itself
    with standalone() as env:
        compared = compare(env, cases, ScriptedProvider, configs, runs=2)
        entries = [
            ConfigRun(name, c.as_json(), results)
            for (name, results), c in zip(compared, configs, strict=True)
        ]
        write_model_report(
            {"core": "scripted"}, env, entries, model_info={"models": "scripted"}, directory=directory
        )


if __name__ == "__main__":
    main(Path(sys.argv[1]))
