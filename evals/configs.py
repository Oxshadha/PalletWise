"""Named runtime configurations for the model gate and comparisons (ADR 0001 section 3).

The baseline is today's runtime. A candidate is added here only once the runtime implements its
choices: RuntimeConfig refuses choices that are not built, so a comparison never reports a
candidate that silently ran as the baseline.
"""

from palletwise.agent.runtime import RuntimeConfig

CONFIGS: dict[str, RuntimeConfig] = {
    "baseline": RuntimeConfig(name="baseline"),
    # ADR 0001 change 2: the template headline at 3 s when the answer is late, and model wording
    # shown only with every key fact of its result.
    "headline-first": RuntimeConfig(name="headline-first", headline_first=True),
    # ADR 0001 change 5 on top: the fallback model starts alongside a primary that has not answered
    # in 4 s (about the healthy p95 of a model call), and the first good response wins.
    "headline-hedged": RuntimeConfig(name="headline-hedged", headline_first=True, hedge_after_s=4.0),
}


def named(names: list[str]) -> list[RuntimeConfig]:
    unknown = [n for n in names if n not in CONFIGS]
    if unknown:
        raise SystemExit(f"unknown configuration(s): {', '.join(unknown)}; known: {', '.join(CONFIGS)}")
    return [CONFIGS[n] for n in names]
