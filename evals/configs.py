"""Named runtime configurations for the model gate and comparisons (ADR 0001 section 3).

The baseline is today's runtime. A candidate is added here only once the runtime implements its
choices: RuntimeConfig refuses choices that are not built, so a comparison never reports a
candidate that silently ran as the baseline.
"""

from palletwise.agent.runtime import RuntimeConfig

CONFIGS: dict[str, RuntimeConfig] = {
    "baseline": RuntimeConfig(name="baseline"),
}


def named(names: list[str]) -> list[RuntimeConfig]:
    unknown = [n for n in names if n not in CONFIGS]
    if unknown:
        raise SystemExit(f"unknown configuration(s): {', '.join(unknown)}; known: {', '.join(CONFIGS)}")
    return [CONFIGS[n] for n in names]
