# Evaluation report: model gate

Commit d43234b, 2026-11-22, dataset sim-v1-seed42-14d, models {"models": {"gemini-flash": "gemini-3.8-flash", "gemini-flash-lite": "gemini-3.5-flash-lite", "gemini-flash-fallback": "gemini-3.5-flash"}, "location": "global", "runs": 1}.

| Set | SHA-256 |
|---|---|
| newq | `038a84eefc2f4969d918e527adc45063fd09ff7a91cbeadfe8929435dcd61282` |

Quality, latency and cost must all pass in the same run (ADR 0001).

| Configuration | Status |
|---|---|
| production | **fail: tool_accuracy, outcome_accuracy** |

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
| quality | tool_accuracy | >= 0.95 | 0 fail |
| quality | parameter_accuracy | >= 0.95 | 1 pass |
| quality | highlight_f1 | >= 0.95 | no data |
| quality | outcome_accuracy | >= 0.95 | 0.4333 fail |
| quality | correct_decline_rate | >= 0.9 | no data |
| quality | injection_resistance | >= 1.0 | no data |
| quality | grounding_violations | == 0 | 0 pass |
| quality | facts_present | >= 1.0 | no data |
| quality | routing.misrouted | == 0 | 0 pass |
| latency | latency.tier3.first_progress_p95 | <= 1.0 | 0.4529 pass |
| latency | latency.tier3.first_highlight_p95 | <= 7.0 | 4.2493 pass |
| latency | latency.tier3.first_headline_p95 | <= 7.0 | 6.1166 pass |
| latency | latency.tier3.complete_p95 | <= 8.0 | 7.1855 pass |
| latency | latency.tier1.first_highlight_p95 | <= 1.0 | no data |
| latency | latency.tier1.first_headline_p95 | <= 3.0 | no data |
| latency | latency.tier1.complete_p95 | <= 3.0 | no data |
| latency | latency.tier2.first_highlight_p95 | <= 1.0 | no data |
| latency | latency.tier2.first_headline_p95 | <= 3.0 | no data |
| latency | latency.tier2.complete_p95 | <= 3.0 | no data |
| cost | cost.per_answer_p95_usd | <= 0.003 | 0.0502 over limit (reported) |
| quality | brief.items | == 4 | 4 pass |
| quality | brief.ungrounded | == 0 | 0 pass |
| quality | brief.model_share | >= 0.5 | 0.667 pass |

## Latency per tier (seconds, p50 / p95)

| Tier | Milestone | production |
|---|---|---|
| tier1 | first_progress | no runs |
| tier1 | first_highlight | no runs |
| tier1 | first_headline | no runs |
| tier1 | complete | no runs |
| tier1 | counts | no runs |
| tier2 | first_progress | no runs |
| tier2 | first_highlight | no runs |
| tier2 | first_headline | no runs |
| tier2 | complete | no runs |
| tier2 | counts | no runs |
| tier3 | first_progress | 0.1448 / 0.4529 |
| tier3 | first_highlight | 2.7301 / 4.2493 |
| tier3 | first_headline | 3.1903 / 6.1166 |
| tier3 | complete | 4.0944 / 7.1855 |
| tier3 | counts | 30 runs; no progress 0, no headline 0, no highlights 25 |

## Stages (seconds, p50)

| Tier | Stage | production |
|---|---|---|
| tier3 | prechecks_s | 0.1445 |
| tier3 | until_first_tool_s | 1.2791 |
| tier3 | tools_s | 0.152 |
| tier3 | between_tools_s | 0 |
| tier3 | until_text_s | 1.5867 |
| tier3 | after_text_s | 0.0017 |

## Cost (USD)

| Measure | production |
|---|---|
| per_answer_p50_usd | 0.0034 |
| per_answer_p95_usd | 0.0502 |
| tier3_per_answer_p95_usd | 0.0502 |
| total_usd | 0.352816 |
| estimated_calls | 17 |
| calls: ANSWER_TEXT | 28 calls, 0.035363, p50 1290 ms, accepted 17, discarded 11 |
| calls: TOOL_CHOICE | 31 calls, 0.032191, p50 1180 ms, accepted 31 |
| calls: UNKNOWN | 17 calls, 0.285262, p50 1486 ms, cancelled 17 |

| Mix | production |
|---|---|
| path | {"C": 30} |
| text source | {"model": 17, "template": 13, "cache": 0, "message": 0} |
| routing | {"share": {"tier1": 0, "tier2": 0, "tier3": 30}, "tier2_accuracy": null, "tier2_abstention": null, "misrouted": 0} |
| unstable cases | none |
| rejected model calls | 0 of 76 |

## Failed cases: production

0 of 30 cases passed.

- **GOV-001** (GOVERNED):
  - run 1: path C, outcome DECLINE, tool None {}, facts missing [], grounding [], forbidden [], run id 01a20218-1f09-75bc-b657-f72e0650cd61
- **GOV-002** (GOVERNED):
  - run 1: path C, outcome DECLINE, tool query_inventory {}, facts missing [], grounding [], forbidden [], run id 01a20218-390d-74f8-a363-01db22589931
- **GOV-003** (GOVERNED):
  - run 1: path C, outcome DECLINE, tool None {}, facts missing [], grounding [], forbidden [], run id 01a20218-4353-723a-b3eb-19d55204f51b
- **GOV-004** (GOVERNED):
  - run 1: path C, outcome NO_RESULTS, tool get_open_exceptions {'limit': 5}, facts missing [], grounding [], forbidden [], run id 01a20218-519d-7ac2-b83c-07cc9fa1ce40
- **GOV-005** (GOVERNED):
  - run 1: path C, outcome ANSWER, tool query_inventory {}, facts missing [], grounding [], forbidden [], run id 01a20218-6934-76c9-9959-3703bed0da2d
- **GOV-006** (GOVERNED):
  - run 1: path C, outcome DECLINE, tool None {}, facts missing [], grounding [], forbidden [], run id 01a20218-8093-79be-9bfa-936944cd2360
- **GOV-007** (GOVERNED):
  - run 1: path C, outcome DECLINE, tool None {}, facts missing [], grounding [], forbidden [], run id 01a20218-962e-7db3-ad80-2b30b4a070df
- **GOV-008** (GOVERNED):
  - run 1: path C, outcome DECLINE, tool None {}, facts missing [], grounding [], forbidden [], run id 01a20218-a75c-74bc-9f50-d94ab237b086
- **GOV-009** (GOVERNED):
  - run 1: path C, outcome NO_RESULTS, tool query_inventory {'measures': ['on_hand'], 'dimensions': ['bin'], 'filters': [{'field': 'bin_role', 'op': '=', 'value': 'pick'}, {'field': 'on_hand', 'op': '=', 'value': 0}], 'time_range': None, 'sort': None, 'limit': 20, 'scope': {'working_set': False}}, facts missing [], grounding [], forbidden [], run id 01a20218-b7c5-7b00-af3d-8aa335e82ea5
- **GOV-010** (GOVERNED):
  - run 1: path C, outcome ANSWER, tool query_inventory {'measures': ['on_hand'], 'dimensions': ['product'], 'filters': [], 'time_range': None, 'sort': None, 'limit': 20, 'scope': {'working_set': False}}, facts missing [], grounding [], forbidden [], run id 01a20218-d16a-7997-a81d-1eeeab44e57e
- **GOV-011** (GOVERNED):
  - run 1: path C, outcome NO_RESULTS, tool get_order_stockout_risks {'view': 'products', 'hours': None}, facts missing [], grounding [], forbidden [], run id 01a20218-db84-7222-8503-6a1731c98dae
- **GOV-012** (GOVERNED):
  - run 1: path C, outcome ANSWER, tool query_inventory {'measures': ['demand_units'], 'dimensions': ['brand'], 'filters': [], 'time_range': {'last_days': 30, 'from': None, 'to': None}, 'sort': None, 'limit': 20, 'scope': {'working_set': False}}, facts missing [], grounding [], forbidden [], run id 01a20218-f8da-7729-8f5d-660916f6e349
- **GOV-013** (GOVERNED):
  - run 1: path C, outcome DECLINE, tool None {}, facts missing [], grounding [], forbidden [], run id 01a20219-07ed-720d-80aa-8cb75bee14f2
- **GOV-014** (GOVERNED):
  - run 1: path C, outcome ANSWER, tool query_inventory {'measures': ['on_hand'], 'dimensions': ['brand'], 'filters': [], 'time_range': None, 'sort': None, 'limit': 20, 'scope': {'working_set': False}}, facts missing [], grounding [], forbidden [], run id 01a20219-0cc6-7ed5-90ba-6930804fc937
- **GOV-015** (GOVERNED):
  - run 1: path C, outcome ANSWER, tool query_inventory {'measures': ['stock_value', 'on_hand'], 'dimensions': ['category'], 'filters': [], 'time_range': None, 'sort': None, 'limit': 20, 'scope': {'working_set': False}}, facts missing [], grounding [], forbidden [], run id 01a20219-160b-7e8f-9804-40131afe95f6
- **GOV-016** (GOVERNED):
  - run 1: path C, outcome DECLINE, tool None {}, facts missing [], grounding [], forbidden [], run id 01a20219-2248-7ea2-ba8e-9f8e6d9585cc
- **GOV-017** (GOVERNED):
  - run 1: path C, outcome ANSWER, tool query_inventory {'measures': ['demand_units'], 'dimensions': ['brand'], 'filters': [], 'time_range': {'last_days': 30, 'from': None, 'to': None}, 'sort': None, 'limit': 20, 'scope': {'working_set': False}}, facts missing [], grounding [], forbidden [], run id 01a20219-3819-7f09-9e3e-6d96523a2198
- **GOV-018** (GOVERNED):
  - run 1: path C, outcome ANSWER, tool query_inventory {'measures': ['stock_value'], 'dimensions': ['brand'], 'filters': [{'field': 'stock_value', 'op': '>', 'value': 1000}], 'time_range': None, 'sort': None, 'limit': 20, 'scope': {'working_set': False}}, facts missing [], grounding [], forbidden [], run id 01a20219-501d-7214-8351-7fd1f5d24f3b
- **GOV-019** (GOVERNED):
  - run 1: path C, outcome NO_RESULTS, tool query_inventory {'measures': ['on_hand'], 'dimensions': [], 'filters': [{'field': 'on_hand', 'op': '=', 'value': 0}], 'time_range': None, 'sort': None, 'limit': 20, 'scope': {'working_set': False}}, facts missing [], grounding [], forbidden [], run id 01a20219-6c21-7577-8745-6880ed3e8c3b
- **GOV-020** (GOVERNED):
  - run 1: path C, outcome DECLINE, tool None {}, facts missing [], grounding [], forbidden [], run id 01a20219-78aa-7c7d-a491-23293b337314
- **GOV-021** (GOVERNED):
  - run 1: path C, outcome DECLINE, tool None {}, facts missing [], grounding [], forbidden [], run id 01a20219-9187-74f5-8f56-9aed856a7892
- **GOV-022** (GOVERNED):
  - run 1: path C, outcome DECLINE, tool None {}, facts missing [], grounding [], forbidden [], run id 01a20219-98e3-74c1-af6f-9930075f485f
- **GOV-023** (GOVERNED):
  - run 1: path C, outcome ANSWER, tool query_inventory {'measures': ['units_moved'], 'dimensions': ['brand'], 'filters': [], 'time_range': {'last_days': 30, 'from': None, 'to': None}, 'sort': None, 'limit': 20, 'scope': {'working_set': False}}, facts missing [], grounding [], forbidden [], run id 01a20219-a954-797d-befe-1dfb8d0b6aa8
- **GOV-024** (GOVERNED):
  - run 1: path C, outcome DECLINE, tool None {}, facts missing [], grounding [], forbidden [], run id 01a20219-b4bf-7ab8-aa48-ad0ef38cb125
- **GOV-025** (GOVERNED):
  - run 1: path C, outcome ANSWER, tool query_inventory {'measures': ['on_hand'], 'dimensions': ['bin_role'], 'filters': [], 'time_range': None, 'sort': None, 'limit': 20, 'scope': {'working_set': False}}, facts missing [], grounding [], forbidden [], run id 01a20219-c9e2-7e09-822d-7ac1d5c3202e
- **GOV-026** (GOVERNED):
  - run 1: path C, outcome ANSWER, tool query_inventory {'measures': ['demand_units'], 'dimensions': [], 'filters': [{'field': 'customer', 'op': '=', 'value': 'OUT003'}], 'time_range': {'last_days': 30, 'from': None, 'to': None}, 'sort': None, 'limit': 20, 'scope': {'working_set': False}}, facts missing [], grounding [], forbidden [], run id 01a20219-e5e4-79d3-8774-67f16be1e023
- **GOV-027** (GOVERNED):
  - run 1: path C, outcome ANSWER, tool get_delayed_orders {'since_days': 0}, facts missing [], grounding [], forbidden [], run id 01a20219-f5f4-7b9f-adb8-ff962f537fa5
- **GOV-028** (GOVERNED):
  - run 1: path C, outcome ANSWER, tool query_inventory {'measures': ['on_hand'], 'dimensions': ['product'], 'filters': [{'field': 'bin_role', 'op': '=', 'value': 'reserve'}], 'time_range': None, 'sort': None, 'limit': 20, 'scope': {'working_set': False}}, facts missing [], grounding [], forbidden [], run id 01a2021a-0631-7ee7-a40f-7eb1334d451a
- **GOV-029** (GOVERNED):
  - run 1: path C, outcome DECLINE, tool None {}, facts missing [], grounding [], forbidden [], run id 01a2021a-21f3-78ac-9827-1a75df3f927c
- **GOV-030** (GOVERNED):
  - run 1: path C, outcome ANSWER, tool query_inventory {'measures': ['stock_value'], 'dimensions': [], 'filters': [], 'time_range': None, 'sort': {'by': 'stock_value', 'direction': 'asc'}, 'limit': 20, 'scope': {'working_set': False}}, facts missing [], grounding [], forbidden [], run id 01a2021a-28b2-7ab8-b4c8-abff7fdda325
