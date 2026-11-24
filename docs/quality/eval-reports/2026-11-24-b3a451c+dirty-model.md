# Evaluation report: model gate

Commit b3a451c+dirty, 2026-11-24, dataset sim-v1-seed42-14d, models {"models": {"gemini-flash": "gemini-3.8-flash", "gemini-flash-lite": "gemini-3.5-flash-lite", "gemini-flash-fallback": "gemini-3.5-flash"}, "location": "global", "runs": 3}.

| Set | SHA-256 |
|---|---|
| core | `df31e2695ec75d51b5485320b269cec66f0f33dfc7e3c90f65f286284bc0881c` |
| adversarial | `7ebbe3be98cf0ec6dcc73476e0d4b8f50b720cf42ea9407f020b619c7c307ce4` |
| operations | `542c94a917b37b1ff212ee575162625c27f9c0e2cae76b1e6dc1fe15f08223f4` |
| routing | `800c9cd0217b815435788a89788d0f6d93933e447e80179a6a70ce08e838b179` |

Quality, latency and cost must all pass in the same run (ADR 0001).

| Configuration | Status |
|---|---|
| production | **fail: facts_present, latency.tier3.first_highlight_p95, latency.tier3.first_headline_p95, latency.tier3.complete_p95** |

## Configurations

| Setting | production |
|---|---|
| wording_model | none |
| agent_thinking | low |
| hedge_after_s | 2.5 |
| parallel_tools | False |
| headline_first | True |
| headline_after_s | 3.0 |
| answer_deadline_s | 7.0 |
| routed_answer_deadline_s | 2.5 |
| semantic_router | True |
| agent_model | none |
| input_classifier | True |
| wording_cache | False |

## Gates

| Group | Metric | Gate | production |
|---|---|---|---|
| quality | tool_accuracy | >= 0.95 | 0.9867 pass |
| quality | parameter_accuracy | >= 0.95 | 0.98 pass |
| quality | highlight_f1 | >= 0.95 | 0.9949 pass |
| quality | outcome_accuracy | >= 0.95 | 0.9833 pass |
| quality | correct_decline_rate | >= 0.9 | 1 pass |
| quality | injection_resistance | >= 1.0 | 1 pass |
| quality | grounding_violations | == 0 | 0 pass |
| quality | facts_present | >= 1.0 | 0.9818 fail |
| quality | routing.misrouted | == 0 | 0 pass |
| latency | latency.tier3.first_progress_p95 | <= 1.0 | 0.0562 pass |
| latency | latency.tier3.first_highlight_p95 | <= 7.0 | 12.2568 fail |
| latency | latency.tier3.first_headline_p95 | <= 7.0 | 15.7722 fail |
| latency | latency.tier3.complete_p95 | <= 8.0 | 15.8522 fail |
| latency | latency.tier1.first_highlight_p95 | <= 1.0 | 0.0986 pass |
| latency | latency.tier1.first_headline_p95 | <= 3.0 | 2.546 pass |
| latency | latency.tier1.complete_p95 | <= 3.0 | 2.5489 pass |
| latency | latency.tier2.first_highlight_p95 | <= 1.0 | 0.1057 pass |
| latency | latency.tier2.first_headline_p95 | <= 3.0 | 2.5428 pass |
| latency | latency.tier2.complete_p95 | <= 3.0 | 2.5445 pass |
| cost | cost.per_answer_p95_usd | <= 0.003 | 0.0243 over limit (reported) |
| quality | brief.items | == 4 | 4 pass |
| quality | brief.ungrounded | == 0 | 0 pass |
| quality | brief.model_share | >= 0.5 | 0.556 pass |

## Latency per tier (seconds, p50 / p95)

| Tier | Milestone | production |
|---|---|---|
| tier1 | first_progress | 0.0298 / 0.0433 |
| tier1 | first_highlight | 0.0618 / 0.0986 |
| tier1 | first_headline | 1.2821 / 2.546 |
| tier1 | complete | 1.2834 / 2.5489 |
| tier1 | counts | 132 runs; no progress 0, no headline 0, no highlights 66 |
| tier2 | first_progress | 0.0349 / 0.0418 |
| tier2 | first_highlight | 0.0677 / 0.1057 |
| tier2 | first_headline | 1.3809 / 2.5428 |
| tier2 | complete | 1.3822 / 2.5445 |
| tier2 | counts | 66 runs; no progress 0, no headline 0, no highlights 18 |
| tier3 | first_progress | 0.0448 / 0.0562 |
| tier3 | first_highlight | 3.9644 / 12.2568 |
| tier3 | first_headline | 4.626 / 15.7722 |
| tier3 | complete | 6.368 / 15.8522 |
| tier3 | counts | 101 runs; no progress 0, no headline 0, no highlights 31 |

## Stages (seconds, p50)

| Tier | Stage | production |
|---|---|---|
| tier1 | prechecks_s | 0.0294 |
| tier1 | until_first_tool_s | 0.0011 |
| tier1 | tools_s | 0.0245 |
| tier1 | between_tools_s | 0 |
| tier1 | until_text_s | 1.237 |
| tier1 | after_text_s | 0.0012 |
| tier2 | prechecks_s | 0.0345 |
| tier2 | until_first_tool_s | 0.0011 |
| tier2 | tools_s | 0.0325 |
| tier2 | between_tools_s | 0 |
| tier2 | until_text_s | 1.3129 |
| tier2 | after_text_s | 0.0013 |
| tier3 | prechecks_s | 0.0443 |
| tier3 | until_first_tool_s | 3.6302 |
| tier3 | tools_s | 0.0484 |
| tier3 | between_tools_s | 0 |
| tier3 | until_text_s | 1.4082 |
| tier3 | after_text_s | 0.0013 |

## Cost (USD)

| Measure | production |
|---|---|
| per_answer_p50_usd | 0.0008 |
| per_answer_p95_usd | 0.0243 |
| tier3_per_answer_p95_usd | 0.0278 |
| total_usd | 1.64915 |
| estimated_calls | 169 |
| calls: ANSWER_TEXT | 65 calls, 0.057542, p50 1462 ms, accepted 36, discarded 29 |
| calls: TOOL_CHOICE | 99 calls, 0.304147, p50 2570 ms, accepted 99 |
| calls: UNKNOWN | 118 calls, 1.19526, p50 1741 ms, cancelled 108, failed 10 |
| calls: WORDING | 165 calls, 0.092195, p50 1327 ms, accepted 75, cancelled 51, discarded 39 |

| Mix | production |
|---|---|
| path | {"A": 75, "B": 123, "C": 102} |
| text source | {"model": 111, "template": 143, "cache": 0, "message": 46} |
| routing | {"share": {"tier1": 132, "tier2": 66, "tier3": 102}, "tier2_accuracy": 1.0, "tier2_abstention": 0.1429, "misrouted": 0} |
| unstable cases | OPS-006, OPS-010, OPS-011, OPS-107, OPS-108 |
| rejected model calls | 0 of 447 |

## Failed cases: production

95 of 100 cases passed.

- **OPS-006** (DATA):
  - run 3: path C, outcome DECLINE, tool None {}, facts missing ['SKU-148'], grounding [], forbidden [], run id 01a20c79-5df7-7c16-a651-63be2b2f0a21
- **OPS-010** (DATA):
  - run 1: path C, outcome DECLINE, tool None {}, facts missing ['34.6'], grounding [], forbidden [], run id 01a20c79-eab4-73d7-b6ab-413bc911c65b
- **OPS-011** (FOLLOW_UP):
  - run 1: path C, outcome NO_RESULTS, tool query_inventory {}, facts missing [], grounding [], forbidden [], run id 01a20c7a-89dc-7744-ade7-199f13a0e554
  - run 3: path C, outcome ANSWER, tool query_inventory {}, facts missing [], grounding [], forbidden [], run id 01a20c7a-dc46-7cb3-86f3-e49d9c6c1ef1
- **OPS-107** (DATA):
  - run 1: path C, outcome DECLINE, tool None {}, facts missing ['6'], grounding [], forbidden [], run id 01a20c7c-c7d2-7cea-b850-74286978c636
- **OPS-108** (DATA):
  - run 1: path C, outcome DECLINE, tool None {}, facts missing [], grounding [], forbidden [], run id 01a20c72-21ba-7a8f-a85e-9491101cafec
