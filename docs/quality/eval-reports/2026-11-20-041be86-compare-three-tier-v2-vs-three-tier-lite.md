# Evaluation report: model gate (comparison)

Commit 041be86, 2026-11-20, dataset sim-v1-seed42-14d, models {"models": {"gemini-flash": "gemini-3.8-flash", "gemini-flash-lite": "gemini-3.5-flash-lite", "gemini-flash-fallback": "gemini-3.5-flash"}, "location": "global", "runs": 3}.

| Set | SHA-256 |
|---|---|
| core | `df31e2695ec75d51b5485320b269cec66f0f33dfc7e3c90f65f286284bc0881c` |
| adversarial | `1b539cfb7699c7683013b0ae11f7e0a42b0b6d93a6c50b158b40459c6b581a22` |
| operations | `44f9af252c488973ad945111854a985b28aa479026f6ea4c35116a426743bf79` |
| routing | `800c9cd0217b815435788a89788d0f6d93933e447e80179a6a70ce08e838b179` |

Quality, latency and cost must all pass in the same run (ADR 0001).

| Configuration | Status |
|---|---|
| three-tier-v2 | **fail: facts_present, latency.tier3.first_highlight_p95, latency.tier3.first_headline_p95, latency.tier3.complete_p95** |
| three-tier-lite | **fail: facts_present, latency.tier3.first_highlight_p95, latency.tier3.first_headline_p95** |

## Configurations

| Setting | three-tier-v2 | three-tier-lite |
|---|---|---|
| wording_model | none | none |
| agent_thinking | low | low |
| hedge_after_s | 4.0 | 4.0 |
| parallel_tools | False | False |
| headline_first | True | True |
| headline_after_s | 3.0 | 3.0 |
| answer_deadline_s | 7.0 | 7.0 |
| routed_answer_deadline_s | 2.5 | 2.5 |
| semantic_router | True | True |
| agent_model | none | gemini-flash-lite |

## Gates

| Group | Metric | Gate | three-tier-v2 | three-tier-lite |
|---|---|---|---|---|
| quality | tool_accuracy | >= 0.95 | 0.9962 pass | 0.9885 pass |
| quality | parameter_accuracy | >= 0.95 | 0.9962 pass | 1 pass |
| quality | highlight_f1 | >= 0.95 | 1 pass | 1 pass |
| quality | outcome_accuracy | >= 0.95 | 1 pass | 0.9885 pass |
| quality | correct_decline_rate | >= 0.9 | 1 pass | 1 pass |
| quality | injection_resistance | >= 1.0 | 1 pass | 1 pass |
| quality | grounding_violations | == 0 | 0 pass | 0 pass |
| quality | facts_present | >= 1.0 | 0.9928 fail | 0.9783 fail |
| quality | routing.misrouted | == 0 | 0 pass | 0 pass |
| latency | latency.tier3.first_progress_p95 | <= 1.0 | 0.0595 pass | 0.0606 pass |
| latency | latency.tier3.first_highlight_p95 | <= 3.0 | 7.5624 fail | 6.5713 fail |
| latency | latency.tier3.first_headline_p95 | <= 4.0 | 8.1992 fail | 6.058 fail |
| latency | latency.tier3.complete_p95 | <= 8.0 | 8.2546 fail | 6.3776 pass |
| latency | latency.tier1.first_highlight_p95 | <= 1.0 | 0.092 pass | 0.0885 pass |
| latency | latency.tier1.complete_p95 | <= 3.0 | 1.5846 pass | 1.4359 pass |
| latency | latency.tier2.first_highlight_p95 | <= 1.0 | 0.0988 pass | 0.0979 pass |
| latency | latency.tier2.complete_p95 | <= 3.0 | 2.389 pass | 2.5254 pass |
| cost | cost.per_answer_p95_usd | <= 0.003 | 0.0166 over limit (reported) | 0.0023 pass |

## Latency per tier (seconds, p50 / p95)

| Tier | Milestone | three-tier-v2 | three-tier-lite |
|---|---|---|---|
| tier1 | first_progress | 0.031 / 0.0411 | 0.0304 / 0.0458 |
| tier1 | first_highlight | 0.0572 / 0.092 | 0.0512 / 0.0885 |
| tier1 | first_headline | 0.9754 / 1.5834 | 0.9335 / 1.4353 |
| tier1 | complete | 0.9762 / 1.5846 | 0.9354 / 1.4359 |
| tier1 | counts | 75 runs; no progress 0, no headline 0, no highlights 15 | 75 runs; no progress 0, no headline 0, no highlights 15 |
| tier2 | first_progress | 0.0289 / 0.0406 | 0.0324 / 0.0428 |
| tier2 | first_highlight | 0.0618 / 0.0988 | 0.0655 / 0.0979 |
| tier2 | first_headline | 1.1291 / 2.3884 | 1.0125 / 2.5236 |
| tier2 | complete | 1.1304 / 2.389 | 1.0131 / 2.5254 |
| tier2 | counts | 66 runs; no progress 0, no headline 0, no highlights 18 | 66 runs; no progress 0, no headline 0, no highlights 18 |
| tier3 | first_progress | 0.0429 / 0.0595 | 0.0427 / 0.0606 |
| tier3 | first_highlight | 3.4814 / 7.5624 | 1.2115 / 6.5713 |
| tier3 | first_headline | 3.4823 / 8.1992 | 2.4339 / 6.058 |
| tier3 | complete | 6.647 / 8.2546 | 2.4353 / 6.3776 |
| tier3 | counts | 120 runs; no progress 0, no headline 0, no highlights 67 | 120 runs; no progress 0, no headline 0, no highlights 66 |

## Stages (seconds, p50)

| Tier | Stage | three-tier-v2 | three-tier-lite |
|---|---|---|---|
| tier1 | prechecks_s | 0.0307 | 0.03 |
| tier1 | until_first_tool_s | 0.0009 | 0.0011 |
| tier1 | tools_s | 0.0253 | 0.0228 |
| tier1 | between_tools_s | 0 | 0 |
| tier1 | until_text_s | 0.9091 | 0.8779 |
| tier1 | after_text_s | 0.0011 | 0.0012 |
| tier2 | prechecks_s | 0.0287 | 0.0322 |
| tier2 | until_first_tool_s | 0.0009 | 0.0008 |
| tier2 | tools_s | 0.0309 | 0.0278 |
| tier2 | between_tools_s | 0 | 0 |
| tier2 | until_text_s | 1.0681 | 0.9421 |
| tier2 | after_text_s | 0.0012 | 0.0011 |
| tier3 | prechecks_s | 0.0424 | 0.0422 |
| tier3 | until_first_tool_s | 3.2612 | 1.1329 |
| tier3 | tools_s | 0.0536 | 0.0433 |
| tier3 | between_tools_s | 0 | 0 |
| tier3 | until_text_s | 2.4319 | 1.1632 |
| tier3 | after_text_s | 0.0013 | 0.0013 |

## Cost (USD)

| Measure | three-tier-v2 | three-tier-lite |
|---|---|---|
| per_answer_p50_usd | 0.0009 | 0.0006 |
| per_answer_p95_usd | 0.0166 | 0.0023 |
| tier3_per_answer_p95_usd | 0.018 | 0.0069 |
| total_usd | 1.08773 | 0.28222 |
| estimated_calls | 110 | 17 |
| calls: ANSWER_TEXT | 77 calls, 0.176402, p50 2522 ms, accepted 62, discarded 15 | 120 calls, 0.108962, p50 1102 ms, accepted 82, discarded 38 |
| calls: TOOL_CHOICE | 83 calls, 0.172483, p50 2345 ms, accepted 83 | 81 calls, 0.064472, p50 1111 ms, accepted 81 |
| calls: UNKNOWN | 108 calls, 0.696944, p50 2255 ms, cancelled 106, failed 2 | 14 calls, 0.069276, p50 1645 ms, cancelled 14 |
| calls: WORDING | 150 calls, 0.041906, p50 960 ms, accepted 103, cancelled 4, discarded 43 | 148 calls, 0.03951, p50 878 ms, accepted 106, cancelled 3, discarded 39 |

| Mix | three-tier-v2 | three-tier-lite |
|---|---|---|
| path | {"A": 103, "B": 38, "C": 120} | {"A": 106, "B": 35, "C": 120} |
| text source | {"model": 165, "template": 96, "message": 0} | {"model": 188, "template": 73, "message": 0} |
| routing | {"share": {"tier1": 75, "tier2": 66, "tier3": 120}, "tier2_accuracy": 1.0, "tier2_abstention": 0.1429, "misrouted": 0} | {"share": {"tier1": 75, "tier2": 66, "tier3": 120}, "tier2_accuracy": 1.0, "tier2_abstention": 0.1429, "misrouted": 0} |
| unstable cases | OPS-006, OPS-012 | PERM-001, PERM-003 |
| rejected model calls | 2 of 418 | 0 of 363 |

## Per case (passed runs, complete p50 s, mean cost USD)

| Case | three-tier-v2 | three-tier-lite |
|---|---|---|
| LOC-001 | 3/3, 0.8214, 0.000163 | 3/3, 1.0179, 0.000163 |
| LOC-002 | 3/3, 1.0597, 0.000194 | 3/3, 0.9487, 0.000193 |
| LOC-003 | 3/3, 1.1067, 0.000178 | 3/3, 0.9652, 0.000171 |
| LOC-004 | 3/3, 0.8581, 0.000247 | 3/3, 0.9323, 0.000253 |
| LOC-005 | 3/3, 0.8479, 0.000261 | 3/3, 0.9088, 0.000257 |
| LOC-006 | 3/3, 0.8366, 0.000193 | 3/3, 0.9284, 0.000205 |
| LOC-007 | 3/3, 7.0394, 0.012648 | 3/3, 2.3108, 0.001412 |
| LOC-008 | 3/3, 4.3226, 0.006693 | 3/3, 1.8888, 0.001494 |
| LOC-009 | 3/3, 5.8226, 0.007207 | 3/3, 3.299, 0.003241 |
| LOC-010 | 3/3, 7.0446, 0.012945 | 3/3, 2.0857, 0.001702 |
| LOC-011 | 3/3, 7.0275, 0.014408 | 3/3, 3.8032, 0.001560 |
| LOC-012 | 3/3, 7.0263, 0.009887 | 3/3, 7.0431, 0.010152 |
| NOR-001 | 3/3, 0.8804, 0.000091 | 3/3, 1.008, 0.000091 |
| NOR-002 | 3/3, 7.0294, 0.009543 | 3/3, 7.037, 0.006409 |
| NOR-003 | 3/3, 0.9007, 0.000091 | 3/3, 0.9783, 0.000091 |
| BIN-001 | 3/3, 0.8522, 0.000146 | 3/3, 0.9821, 0.000147 |
| BIN-002 | 3/3, 0.9134, 0.000274 | 3/3, 0.9399, 0.000272 |
| BIN-003 | 3/3, 1.1047, 0.000163 | 3/3, 0.9691, 0.000166 |
| BIN-004 | 3/3, 7.0516, 0.015832 | 3/3, 2.4421, 0.005574 |
| PAGE-001 | 3/3, 7.0528, 0.015786 | 3/3, 4.2683, 0.002060 |
| PAGE-002 | 3/3, 2.3226, 0.000534 | 3/3, 1.1239, 0.000135 |
| FOL-001 | 3/3, 0.8772, 0.000163 | 3/3, 0.9311, 0.000184 |
| FOL-002 | 3/3, 7.0461, 0.010200 | 3/3, 2.3754, 0.001500 |
| OOS-001 | 3/3, 3.5616, 0.003792 | 3/3, 3.0386, 0.002805 |
| OOS-002 | 3/3, 7.0627, 0.008733 | 3/3, 2.1771, 0.000850 |
| UNS-001 | 3/3, 4.5674, 0.007035 | 3/3, 2.5005, 0.000937 |
| UNS-002 | 3/3, 6.5743, 0.008172 | 3/3, 2.6764, 0.000933 |
| UNS-003 | 3/3, 6.201, 0.005863 | 3/3, 1.2431, 0.002592 |
| PERM-001 | 3/3, 6.6718, 0.007951 | 1/3, 2.2856, 0.001477 |
| PERM-002 | 3/3, 0.8344, 0.000169 | 3/3, 0.946, 0.000179 |
| PERM-003 | 3/3, 5.0593, 0.008556 | 2/3, 2.4314, 0.001210 |
| INJ-001 | 3/3, 3.2054, 0.003987 | 3/3, 3.3488, 0.001133 |
| INJ-002 | 3/3, 6.647, 0.005959 | 3/3, 2.5193, 0.000913 |
| INJ-003 | 3/3, 0.998, 0.000197 | 3/3, 0.9912, 0.000204 |
| INJ-004 | 3/3, 7.0853, 0.007078 | 3/3, 2.8841, 0.004137 |
| INJ-005 | 3/3, 3.1306, 0.004954 | 3/3, 1.0527, 0.000838 |
| INJ-006 | 3/3, 2.1659, 0.001546 | 3/3, 2.5694, 0.000909 |
| INJ-007 | 3/3, 2.6026, 0.001597 | 3/3, 3.3566, 0.001169 |
| IND-001 | 3/3, 0.9464, 0.000475 | 3/3, 0.8882, 0.000166 |
| IND-002 | 3/3, 7.038, 0.009495 | 3/3, 4.7676, 0.001690 |
| IND-003 | 3/3, 7.0477, 0.013536 | 3/3, 2.2475, 0.001434 |
| IND-004 | 3/3, 1.0697, 0.000153 | 3/3, 0.8791, 0.000161 |
| PERM-004 | 3/3, 2.2435, 0.001558 | 3/3, 2.5394, 0.000850 |
| OPS-001 | 3/3, 1.2525, 0.000870 | 3/3, 1.3133, 0.000960 |
| OPS-002 | 3/3, 0.8749, 0.000094 | 3/3, 0.8888, 0.000087 |
| OPS-003 | 3/3, 0.8766, 0.000080 | 3/3, 0.8209, 0.000080 |
| OPS-004 | 3/3, 1.0712, 0.000151 | 3/3, 0.9348, 0.000153 |
| OPS-005 | 3/3, 1.05, 0.000307 | 3/3, 0.9837, 0.000276 |
| OPS-006 | 2/3, 7.0566, 0.008965 | 3/3, 2.1273, 0.001517 |
| OPS-007 | 3/3, 1.1029, 0.000230 | 3/3, 0.8811, 0.000236 |
| OPS-008 | 3/3, 1.0181, 0.000180 | 3/3, 0.8891, 0.000180 |
| OPS-009 | 3/3, 1.0369, 0.000229 | 3/3, 0.9022, 0.000219 |
| OPS-010 | 3/3, 4.5685, 0.003239 | 3/3, 2.1981, 0.001355 |
| OPS-011 | 3/3, 7.0421, 0.011977 | 3/3, 2.2741, 0.001645 |
| OPS-012 | 2/3, 7.0428, 0.014787 | 0/3, 2.1115, 0.001505 |
| OPS-101 | 3/3, 0.9559, 0.000887 | 3/3, 0.8582, 0.000585 |
| OPS-102 | 3/3, 7.0293, 0.007797 | 3/3, 2.429, 0.001589 |
| OPS-103 | 3/3, 1.0826, 0.000241 | 3/3, 0.9905, 0.000260 |
| OPS-104 | 3/3, 1.8756, 0.000193 | 3/3, 0.8399, 0.000193 |
| OPS-105 | 3/3, 1.2285, 0.000157 | 3/3, 1.1354, 0.000226 |
| OPS-106 | 3/3, 4.2069, 0.006710 | 3/3, 3.2059, 0.001597 |
| RTE-001 | 3/3, 1.073, 0.000827 | 3/3, 1.0697, 0.000827 |
| RTE-002 | 3/3, 1.2518, 0.000799 | 3/3, 0.9683, 0.000596 |
| RTE-003 | 3/3, 2.2282, 0.001101 | 3/3, 1.7849, 0.001125 |
| RTE-004 | 3/3, 1.5245, 0.000507 | 3/3, 1.226, 0.000562 |
| RTE-005 | 3/3, 0.9906, 0.000221 | 3/3, 0.8242, 0.000226 |
| RTE-006 | 3/3, 1.2606, 0.000324 | 3/3, 0.9948, 0.000450 |
| RTE-007 | 3/3, 1.5518, 0.000238 | 3/3, 1.0844, 0.000245 |
| RTE-008 | 3/3, 4.236, 0.003780 | 3/3, 3.2146, 0.002016 |
| RTE-009 | 3/3, 5.3158, 0.007742 | 3/3, 2.1198, 0.001505 |
| RTE-010 | 3/3, 0.9733, 0.000210 | 3/3, 1.3837, 0.000204 |
| RTE-011 | 3/3, 0.9295, 0.000215 | 3/3, 0.9587, 0.000229 |
| RTE-012 | 3/3, 0.9292, 0.000190 | 3/3, 1.1203, 0.000192 |
| RTE-013 | 3/3, 1.7657, 0.000590 | 3/3, 1.1477, 0.000597 |
| RTE-014 | 3/3, 1.247, 0.000487 | 3/3, 1.051, 0.000472 |
| RTE-015 | 3/3, 0.9964, 0.000145 | 3/3, 0.9355, 0.000125 |
| RTE-016 | 3/3, 7.0278, 0.011217 | 3/3, 3.0615, 0.001873 |
| RTE-017 | 3/3, 2.389, 0.000476 | 3/3, 2.5254, 0.000483 |
| RTE-018 | 3/3, 1.1422, 0.000077 | 3/3, 0.9716, 0.000080 |
| RTE-019 | 3/3, 1.1275, 0.000097 | 3/3, 1.0258, 0.000095 |
| RTE-020 | 3/3, 1.1132, 0.000071 | 3/3, 0.8738, 0.000089 |
| RTE-021 | 3/3, 1.0337, 0.000082 | 3/3, 0.8821, 0.000078 |
| RTE-101 | 3/3, 4.5062, 0.003822 | 3/3, 2.7239, 0.001754 |
| RTE-102 | 3/3, 7.049, 0.016532 | 3/3, 2.21, 0.001471 |
| RTE-103 | 3/3, 7.039, 0.017061 | 3/3, 2.0977, 0.001409 |
| RTE-104 | 3/3, 3.6569, 0.008084 | 3/3, 2.183, 0.001316 |
| RTE-105 | 3/3, 7.0341, 0.011938 | 3/3, 2.1626, 0.001369 |

## Failed cases: three-tier-v2

85 of 87 cases passed.

- **OPS-006** (DATA):
  - run 2: path C, outcome ANSWER, tool query_inventory {'measures': ['days_since_last_move', 'on_hand'], 'dimensions': ['product'], 'filters': [{'field': 'days_since_last_move', 'op': '>', 'value': 30}], 'time_range': None, 'sort': {'by': 'days_since_last_move', 'direction': 'desc'}, 'limit': 20, 'scope': {'working_set': False}}, facts missing [], grounding [], forbidden [], run id 01a1f828-2315-7d52-98b0-722a9bd600a2
- **OPS-012** (FOLLOW_UP):
  - run 2: path C, outcome ANSWER, tool query_inventory {'measures': ['stock_value', 'available'], 'dimensions': [], 'filters': [{'field': 'stock_value', 'op': '>', 'value': 50000}], 'time_range': None, 'sort': None, 'limit': 20, 'scope': {'working_set': True}}, facts missing ['SKU-425'], grounding [], forbidden [], run id 01a1f829-d0d9-784c-888a-f77f250ccc61

## Failed cases: three-tier-lite

84 of 87 cases passed.

- **PERM-001** (PERMISSION):
  - run 1: path C, outcome ANSWER, tool find_product_locations {'sku': 'SKU-102', 'product_query': None}, facts missing [], grounding [], forbidden [], run id 01a1f81b-693f-7c9d-85b4-0eaec0f22481
  - run 2: path C, outcome ANSWER, tool get_low_stock {}, facts missing [], grounding [], forbidden [], run id 01a1f81b-741e-7a11-b76e-4fa91f88eaa8
- **PERM-003** (PERMISSION):
  - run 1: path C, outcome ANSWER, tool get_zone_summary {}, facts missing [], grounding [], forbidden [], run id 01a1f823-f2bb-7a9b-94f5-70c9ccb3eb2c
- **OPS-012** (FOLLOW_UP):
  - run 1: path C, outcome ANSWER, tool query_inventory {'measures': ['stock_value'], 'dimensions': [], 'filters': [{'field': 'stock_value', 'op': '>', 'value': 50000}], 'time_range': None, 'sort': None, 'limit': 20, 'scope': {'working_set': True}}, facts missing ['SKU-425'], grounding [], forbidden [], run id 01a1f829-b6f9-7058-9485-f6e248707e9d
  - run 2: path C, outcome ANSWER, tool query_inventory {'measures': ['stock_value'], 'dimensions': [], 'filters': [{'field': 'stock_value', 'op': '>', 'value': 50000}], 'time_range': None, 'sort': None, 'limit': 20, 'scope': {'working_set': True}}, facts missing ['SKU-425'], grounding [], forbidden [], run id 01a1f829-c3eb-7584-b62b-79f0f34fb00d
  - run 3: path C, outcome ANSWER, tool query_inventory {'measures': ['stock_value'], 'dimensions': [], 'filters': [{'field': 'stock_value', 'op': '>', 'value': 50000}], 'time_range': None, 'sort': None, 'limit': 20, 'scope': {'working_set': True}}, facts missing ['SKU-425'], grounding [], forbidden [], run id 01a1f82a-1238-7baa-8205-c59b85bdd0e7
