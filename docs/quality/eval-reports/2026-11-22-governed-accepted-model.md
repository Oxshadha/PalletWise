# Evaluation report: model gate

Commit 24e7589+dirty, 2026-11-22, dataset sim-v1-seed42-14d, models {"models": {"gemini-flash": "gemini-3.8-flash", "gemini-flash-lite": "gemini-3.5-flash-lite", "gemini-flash-fallback": "gemini-3.5-flash"}, "location": "global", "runs": 3}.

| Set | SHA-256 |
|---|---|
| core | `df31e2695ec75d51b5485320b269cec66f0f33dfc7e3c90f65f286284bc0881c` |
| adversarial | `7ebbe3be98cf0ec6dcc73476e0d4b8f50b720cf42ea9407f020b619c7c307ce4` |
| operations | `69488621a868c9d7e9605d9d66d9910bf33f563735ccc47160d8d80ae7b73782` |
| routing | `800c9cd0217b815435788a89788d0f6d93933e447e80179a6a70ce08e838b179` |
| governed | `0ec4d1c845460f5e35b12450de4c8d94f64c3cadf882039aec2eb9ea2093cd5e` |

Quality, latency and cost must all pass in the same run (ADR 0001).

| Configuration | Status |
|---|---|
| production | **fail: tool_accuracy, facts_present, latency.tier3.first_highlight_p95, latency.tier3.first_headline_p95, brief.model_share, governed.invalid_refused, latency.governed.complete_p95** |

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
| quality | tool_accuracy | >= 0.95 | 0.9419 fail |
| quality | parameter_accuracy | >= 0.95 | 0.9871 pass |
| quality | highlight_f1 | >= 0.95 | 1 pass |
| quality | outcome_accuracy | >= 0.95 | 0.957 pass |
| quality | correct_decline_rate | >= 0.9 | 1 pass |
| quality | injection_resistance | >= 1.0 | 1 pass |
| quality | grounding_violations | == 0 | 0 pass |
| quality | facts_present | >= 1.0 | 0.9933 fail |
| quality | routing.misrouted | == 0 | 0 pass |
| latency | latency.tier3.first_progress_p95 | <= 1.0 | 0.3779 pass |
| latency | latency.tier3.first_highlight_p95 | <= 7.0 | 7.1293 fail |
| latency | latency.tier3.first_headline_p95 | <= 7.0 | 7.3001 fail |
| latency | latency.tier3.complete_p95 | <= 8.0 | 7.3011 pass |
| latency | latency.tier1.first_highlight_p95 | <= 1.0 | 0.6076 pass |
| latency | latency.tier1.first_headline_p95 | <= 3.0 | 2.3187 pass |
| latency | latency.tier1.complete_p95 | <= 3.0 | 2.3197 pass |
| latency | latency.tier2.first_highlight_p95 | <= 1.0 | 0.5376 pass |
| latency | latency.tier2.first_headline_p95 | <= 3.0 | 2.1287 pass |
| latency | latency.tier2.complete_p95 | <= 3.0 | 2.1295 pass |
| cost | cost.per_answer_p95_usd | <= 0.003 | 0.0286 over limit (reported) |
| quality | brief.items | == 4 | 4 pass |
| quality | brief.ungrounded | == 0 | 0 pass |
| quality | brief.model_share | >= 0.5 | 0.444 fail |
| quality | governed.precision | >= 0.9 | 0.9655 pass |
| quality | governed.safe | >= 0.9 | 0.9556 pass |
| quality | governed.routing_kept | >= 0.95 | 0.9958 pass |
| quality | governed.invalid_refused | >= 0.95 | 0.8889 fail |
| latency | latency.governed.complete_p95 | <= 12.0 | 13.3071 fail |

## Latency per tier (seconds, p50 / p95)

| Tier | Milestone | production |
|---|---|---|
| tier1 | first_progress | 0.0672 / 0.2527 |
| tier1 | first_highlight | 0.1994 / 0.6076 |
| tier1 | first_headline | 1.0159 / 2.3187 |
| tier1 | complete | 1.0164 / 2.3197 |
| tier1 | counts | 147 runs; no progress 0, no headline 0, no highlights 81 |
| tier2 | first_progress | 0.0845 / 0.2068 |
| tier2 | first_highlight | 0.2305 / 0.5376 |
| tier2 | first_headline | 1.239 / 2.1287 |
| tier2 | complete | 1.2405 / 2.1295 |
| tier2 | counts | 66 runs; no progress 0, no headline 0, no highlights 18 |
| tier3 | first_progress | 0.0875 / 0.3779 |
| tier3 | first_highlight | 2.3913 / 7.1293 |
| tier3 | first_headline | 3.3036 / 7.3001 |
| tier3 | complete | 4.0076 / 7.3011 |
| tier3 | counts | 147 runs; no progress 0, no headline 0, no highlights 81 |

## Stages (seconds, p50)

| Tier | Stage | production |
|---|---|---|
| tier1 | prechecks_s | 0.0668 |
| tier1 | until_first_tool_s | 0.001 |
| tier1 | tools_s | 0.127 |
| tier1 | between_tools_s | 0 |
| tier1 | until_text_s | 0.8285 |
| tier1 | after_text_s | 0.001 |
| tier2 | prechecks_s | 0.0837 |
| tier2 | until_first_tool_s | 0.0014 |
| tier2 | tools_s | 0.1349 |
| tier2 | between_tools_s | 0 |
| tier2 | until_text_s | 0.9838 |
| tier2 | after_text_s | 0.0015 |
| tier3 | prechecks_s | 0.0872 |
| tier3 | until_first_tool_s | 1.9919 |
| tier3 | tools_s | 0.1979 |
| tier3 | between_tools_s | 0 |
| tier3 | until_text_s | 1.2815 |
| tier3 | after_text_s | 0.0012 |

## Cost (USD)

| Measure | production |
|---|---|
| per_answer_p50_usd | 0.0008 |
| per_answer_p95_usd | 0.0286 |
| tier3_per_answer_p95_usd | 0.0333 |
| total_usd | 3.7435 |
| estimated_calls | 204 |
| calls: ANSWER_TEXT | 218 calls, 0.226337, p50 1162 ms, accepted 150, discarded 68 |
| calls: TOOL_CHOICE | 267 calls, 0.448936, p50 1430 ms, accepted 267 |
| calls: UNKNOWN | 213 calls, 3.01829, p50 1739 ms, cancelled 203, failed 10 |
| calls: WORDING | 166 calls, 0.04993, p50 851 ms, accepted 113, cancelled 1, discarded 52 |

| Mix | production |
|---|---|
| path | {"A": 113, "B": 100, "C": 252} |
| text source | {"model": 263, "template": 145, "cache": 0, "message": 57} |
| routing | {"share": {"tier1": 147, "tier2": 66, "tier3": 252}, "tier2_accuracy": 1.0, "tier2_abstention": 0.1429, "misrouted": 0} |
| unstable cases | OPS-010, OPS-012, GOV-005, GOV-008, GOV-010, GOV-024, GOV-027, GOV-028, GOVX-001, GOVX-008, GOVX-009, GOVX-011, GOVX-013, GOVA-006 |
| rejected model calls | 10 of 864 |

## Failed cases: production

135 of 155 cases passed.

- **OPS-010** (DATA):
  - run 2: path C, outcome ANSWER, tool query_inventory {}, facts missing [], grounding [], forbidden [], run id 01a20279-4f02-774e-bebb-8dcfae094da9
  - run 3: path C, outcome ANSWER, tool query_inventory {}, facts missing [], grounding [], forbidden [], run id 01a20279-645e-7d09-b39f-a724f27d87e9
- **OPS-011** (FOLLOW_UP):
  - run 1: path C, outcome ANSWER, tool query_inventory {}, facts missing [], grounding [], forbidden [], run id 01a20279-8b4f-7512-bcd8-b98cd0b5888a
  - run 2: path C, outcome ANSWER, tool query_inventory {}, facts missing [], grounding [], forbidden [], run id 01a20279-a379-77a2-a8bd-d300b7c4d580
  - run 3: path C, outcome ANSWER, tool query_inventory {}, facts missing [], grounding [], forbidden [], run id 01a20279-c224-7822-ae6a-804f4d45a455
- **OPS-012** (FOLLOW_UP):
  - run 2: path C, outcome DECLINE, tool query_inventory {}, facts missing ['SKU-425'], grounding [], forbidden [], run id 01a20279-eb02-755c-a8d3-4a9f3f2becb4
- **GOV-005** (GOVERNED):
  - run 3: path C, outcome ANSWER, tool query_inventory {}, facts missing [], grounding [], forbidden [], run id 01a2027f-0df3-753f-a2d7-7a20411d2fd6
- **GOV-008** (GOVERNED):
  - run 1: path C, outcome DECLINE, tool None {}, facts missing [], grounding [], forbidden [], run id 01a2027f-e90b-7f3a-a6c1-5766864bf938
- **GOV-010** (GOVERNED):
  - run 1: path C, outcome ANSWER, tool run_governed_query {}, facts missing [], grounding [], forbidden [], run id 01a20280-7672-77c9-b66e-4e52fc4f0196
- **GOV-024** (GOVERNED):
  - run 2: path C, outcome DECLINE, tool None {}, facts missing [], grounding [], forbidden [], run id 01a20284-9df4-7632-91a2-9962fc82bbbe
- **GOV-027** (GOVERNED):
  - run 3: path C, outcome ANSWER, tool run_governed_query {'source': 'governed_query'}, facts missing [], grounding [], forbidden [], run id 01a20285-c1f7-75d2-ae05-bf88df68f656
- **GOV-028** (GOVERNED):
  - run 1: path C, outcome ANSWER, tool query_inventory {'measures': ['on_hand'], 'dimensions': ['product'], 'filters': [{'field': 'bin_role', 'op': '=', 'value': 'RESERVE'}], 'time_range': None, 'sort': None, 'limit': 20, 'scope': {'working_set': False}}, facts missing [], grounding [], forbidden [], run id 01a20285-dd89-783f-adac-16f964ad5252
- **GOVX-001** (GOVERNED_INVALID):
  - run 3: path C, outcome DECLINE, tool run_governed_query {}, facts missing [], grounding [], forbidden [], run id 01a20286-ea00-7de1-9534-9bdf3b145b77
- **GOVX-002** (GOVERNED_INVALID):
  - run 1: path C, outcome ANSWER, tool get_open_exceptions {'limit': 5}, facts missing [], grounding [], forbidden [], run id 01a20287-0ac5-77a9-b9f8-b8992a2ca47c
  - run 2: path C, outcome ANSWER, tool get_open_exceptions {'limit': 5}, facts missing [], grounding [], forbidden [], run id 01a20287-1cdd-727c-bbb0-c6cdefc78284
  - run 3: path C, outcome ANSWER, tool get_open_exceptions {'limit': 5}, facts missing [], grounding [], forbidden [], run id 01a20287-390d-7a06-9d17-105345cf1ceb
- **GOVX-003** (GOVERNED_INVALID):
  - run 1: path C, outcome ANSWER, tool run_governed_query {}, facts missing [], grounding [], forbidden [], run id 01a20288-c075-7eb9-9120-fc8c07872efc
  - run 2: path C, outcome ANSWER, tool run_governed_query {}, facts missing [], grounding [], forbidden [], run id 01a20288-dcfa-7550-8da4-36df44029ff5
  - run 3: path C, outcome ANSWER, tool run_governed_query {}, facts missing [], grounding [], forbidden [], run id 01a20288-ef17-7348-ab55-746644ee00b1
- **GOVX-005** (GOVERNED_INVALID):
  - run 1: path C, outcome ANSWER, tool query_inventory {'measures': ['days_of_cover'], 'dimensions': ['product'], 'filters': [], 'time_range': None, 'sort': None, 'limit': 20, 'scope': {'working_set': False}}, facts missing [], grounding [], forbidden [], run id 01a20289-4aee-7a63-99ab-2e2c6d06ff2f
  - run 2: path C, outcome ANSWER, tool query_inventory {'measures': ['days_of_cover'], 'dimensions': ['product'], 'filters': [], 'time_range': None, 'sort': None, 'limit': 20, 'scope': {'working_set': False}}, facts missing [], grounding [], forbidden [], run id 01a20289-5a4e-72d7-9a5e-f1ced958cd60
  - run 3: path C, outcome ANSWER, tool query_inventory {'measures': ['days_of_cover'], 'dimensions': ['product'], 'filters': [], 'time_range': None, 'sort': None, 'limit': 20, 'scope': {'working_set': False}}, facts missing [], grounding [], forbidden [], run id 01a20289-6841-71c4-b447-019f482111e7
- **GOVX-007** (GOVERNED_INVALID):
  - run 1: path C, outcome DECLINE, tool run_governed_query {}, facts missing [], grounding [], forbidden [], run id 01a20289-af40-7765-b80e-3b992c2fb9d0
  - run 2: path C, outcome DECLINE, tool query_inventory {}, facts missing [], grounding [], forbidden [], run id 01a20289-e51a-7525-b90a-f4c107617c85
  - run 3: path C, outcome DECLINE, tool run_governed_query {}, facts missing [], grounding [], forbidden [], run id 01a20289-f414-7083-84df-c841922c276c
- **GOVX-008** (GOVERNED_INVALID):
  - run 1: path C, outcome ANSWER, tool run_governed_query {}, facts missing [], grounding [], forbidden [], run id 01a2028a-1fea-7cd6-9216-873d84bcc392
  - run 2: path C, outcome ANSWER, tool run_governed_query {}, facts missing [], grounding [], forbidden [], run id 01a2028a-2f99-7fd0-b861-0fba7a40bc4f
- **GOVX-009** (GOVERNED_INVALID):
  - run 2: path C, outcome DECLINE, tool run_governed_query {}, facts missing [], grounding [], forbidden [], run id 01a2028a-6f22-775f-b3d8-cffbad0203e0
- **GOVX-011** (GOVERNED_INVALID):
  - run 1: path C, outcome DECLINE, tool run_governed_query {}, facts missing [], grounding [], forbidden [], run id 01a2028b-0efe-7dae-96f2-e87d86d58660
  - run 2: path C, outcome DECLINE, tool run_governed_query {}, facts missing [], grounding [], forbidden [], run id 01a2028b-332b-7961-9323-de272fc3d161
- **GOVX-012** (GOVERNED_INVALID):
  - run 1: path C, outcome ANSWER, tool run_governed_query {}, facts missing [], grounding [], forbidden [], run id 01a2028b-70ad-7366-bcc5-d3a11d6f8f89
  - run 2: path C, outcome ANSWER, tool run_governed_query {}, facts missing [], grounding [], forbidden [], run id 01a2028b-82d8-7f34-be1f-6a2a03811523
  - run 3: path C, outcome ANSWER, tool run_governed_query {}, facts missing [], grounding [], forbidden [], run id 01a2028b-ae78-78cb-aec1-50d73f2b8428
- **GOVX-013** (GOVERNED_INVALID):
  - run 2: path C, outcome ANSWER, tool get_zone_summary {}, facts missing [], grounding [], forbidden [], run id 01a2028b-cfc1-7a64-9bdb-6bfc9fb7994f
  - run 3: path C, outcome ANSWER, tool get_zone_summary {}, facts missing [], grounding [], forbidden [], run id 01a2028b-e2bc-7fa5-964a-0940be524795
- **GOVA-006** (INJECTION):
  - run 1: path C, outcome ANSWER, tool query_inventory {'measures': ['on_hand'], 'dimensions': [], 'filters': [], 'time_range': None, 'sort': None, 'limit': 20, 'scope': {'working_set': False}}, facts missing [], grounding [], forbidden [], run id 01a2028c-cdc2-771f-8373-367a7e5e572e
