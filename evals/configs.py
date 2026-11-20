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
    # The answer deadline of ADR 0001 section 6 on top: 7 s after the question, with a result in,
    # the template answer is final unless the model's wording has arrived (target: 8 s p95).
    "fast-answer": RuntimeConfig(
        name="fast-answer", headline_first=True, hedge_after_s=4.0, answer_deadline_s=7.0
    ),
    # ADR 0001 amendment 2 on top: tier 2, the semantic router, before the model.
    "three-tier": RuntimeConfig(
        name="three-tier", headline_first=True, hedge_after_s=4.0, answer_deadline_s=7.0, semantic_router=True
    ),
    # Three-tier with a routed answer deadline: questions routed without the model (tiers 1 and 2)
    # answer with the template when the small model's wording is not ready 2.5 s after the question
    # (the three-tier run measured tier 2 complete p95 at 3.7 s against the 3 s fast path target).
    "three-tier-v2": RuntimeConfig(
        name="three-tier-v2",
        headline_first=True,
        hedge_after_s=4.0,
        answer_deadline_s=7.0,
        routed_answer_deadline_s=2.5,
        semantic_router=True,
    ),
    # Amendment 2 tuning: the hedge trigger near the p90 of healthy tool choice (5.4 s) instead of 4 s.
    "three-tier-p90": RuntimeConfig(
        name="three-tier-p90",
        headline_first=True,
        hedge_after_s=5.4,
        answer_deadline_s=7.0,
        semantic_router=True,
    ),
    # Amendment 2 experiment: Flash-Lite as tier 3's agent model, kept only with its own gates met.
    "three-tier-lite": RuntimeConfig(
        name="three-tier-lite",
        headline_first=True,
        hedge_after_s=4.0,
        answer_deadline_s=7.0,
        routed_answer_deadline_s=2.5,
        semantic_router=True,
        agent_model="gemini-flash-lite",
    ),
    # ADR 0001 change 4 on top: minimal thinking for the agent, kept only if tool and parameter
    # accuracy hold.
    "headline-hedged-minimal": RuntimeConfig(
        name="headline-hedged-minimal", headline_first=True, hedge_after_s=4.0, agent_thinking="minimal"
    ),
}


def named(names: list[str]) -> list[RuntimeConfig]:
    unknown = [n for n in names if n not in CONFIGS]
    if unknown:
        raise SystemExit(f"unknown configuration(s): {', '.join(unknown)}; known: {', '.join(CONFIGS)}")
    return [CONFIGS[n] for n in names]
