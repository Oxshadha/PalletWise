# Evaluation report: model gate (comparison)

Commit eb9d674, 2026-11-20, dataset sim-v1-seed42-14d, models {"models": {"gemini-flash": "gemini-3.8-flash", "gemini-flash-lite": "gemini-3.5-flash-lite", "gemini-flash-fallback": "gemini-3.5-flash"}, "location": "global", "runs": 3}.

| Set | SHA-256 |
|---|---|
| core | `df31e2695ec75d51b5485320b269cec66f0f33dfc7e3c90f65f286284bc0881c` |
| adversarial | `1b539cfb7699c7683013b0ae11f7e0a42b0b6d93a6c50b158b40459c6b581a22` |
| operations | `44f9af252c488973ad945111854a985b28aa479026f6ea4c35116a426743bf79` |

Quality, latency and cost must all pass in the same run (ADR 0001).

| Configuration | Status |
|---|---|
| baseline | **fail: facts_present, latency.model.first_highlight_p95, latency.model.first_headline_p95, latency.model.complete_p95** |
| fast-answer | **fail: latency.model.first_highlight_p95, latency.model.first_headline_p95** |

## Configurations

| Setting | baseline | fast-answer |
|---|---|---|
| wording_model | none | none |
| agent_thinking | low | low |
| hedge_after_s | none | 4.0 |
| parallel_tools | False | False |
| headline_first | False | True |
| headline_after_s | none | 3.0 |
| answer_deadline_s | none | 7.0 |

## Gates

| Group | Metric | Gate | baseline | fast-answer |
|---|---|---|---|---|
| quality | tool_accuracy | >= 0.95 | 1 pass | 1 pass |
| quality | parameter_accuracy | >= 0.95 | 1 pass | 0.9945 pass |
| quality | highlight_f1 | >= 0.95 | 1 pass | 1 pass |
| quality | outcome_accuracy | >= 0.95 | 1 pass | 0.9945 pass |
| quality | correct_decline_rate | >= 0.9 | 1 pass | 1 pass |
| quality | injection_resistance | >= 1.0 | 1 pass | 1 pass |
| quality | grounding_violations | == 0 | 0 pass | 0 pass |
| quality | facts_present | >= 1.0 | 0.869 fail | 1 pass |
| latency | latency.model.first_progress_p95 | <= 1.0 | 0.0851 pass | 0.1081 pass |
| latency | latency.model.first_highlight_p95 | <= 3.0 | 18.8905 fail | 8.2325 fail |
| latency | latency.model.first_headline_p95 | <= 4.0 | 40.7496 fail | 7.8792 fail |
| latency | latency.model.complete_p95 | <= 8.0 | 40.7501 fail | 7.8946 pass |
| latency | latency.fast.first_highlight_p95 | <= 1.0 | 0.1192 pass | 0.0713 pass |
| latency | latency.fast.complete_p95 | <= 3.0 | 1.5025 pass | 2.3356 pass |
| cost | cost.per_answer_p95_usd | <= 0.003 | 0.018 over limit (reported) | 0.0175 over limit (reported) |

## Latency (seconds, p50 / p95)

| Path | Milestone | baseline | fast-answer |
|---|---|---|---|
| model | first_progress | 0.0425 / 0.0851 | 0.0429 / 0.1081 |
| model | first_highlight | 6.0593 / 18.8905 | 6.1277 / 8.2325 |
| model | first_headline | 11.7688 / 40.7496 | 5.8603 / 7.8792 |
| model | complete | 11.7701 / 40.7501 | 7.0408 / 7.8946 |
| model | counts | 108 runs; no progress 0, no headline 0, no highlights 60 | 108 runs; no progress 0, no headline 0, no highlights 61 |
| fast | first_progress | 0.0323 / 0.0441 | 0.0288 / 0.0384 |
| fast | first_highlight | 0.06 / 0.1192 | 0.049 / 0.0713 |
| fast | first_headline | 1.0031 / 1.5013 | 1.0973 / 2.3341 |
| fast | complete | 1.0042 / 1.5025 | 1.0991 / 2.3356 |
| fast | counts | 75 runs; no progress 0, no headline 0, no highlights 15 | 75 runs; no progress 0, no headline 0, no highlights 15 |

## Stages (seconds, p50)

| Path | Stage | baseline | fast-answer |
|---|---|---|---|
| model | prechecks_s | 0.0423 | 0.0425 |
| model | until_first_tool_s | 5.8642 | 5.9051 |
| model | tools_s | 0.0578 | 0.0516 |
| model | between_tools_s | 0 | 0 |
| model | until_text_s | 7.5821 | 2.6347 |
| model | after_text_s | 0.001 | 0.0012 |
| fast | prechecks_s | 0.0318 | 0.0285 |
| fast | until_first_tool_s | 0.001 | 0.0009 |
| fast | tools_s | 0.0254 | 0.0214 |
| fast | between_tools_s | 0 | 0 |
| fast | until_text_s | 0.9364 | 1.0395 |
| fast | after_text_s | 0.0011 | 0.0011 |

## Cost (USD)

| Measure | baseline | fast-answer |
|---|---|---|
| per_answer_p50_usd | 0.0016 | 0.0035 |
| per_answer_p95_usd | 0.018 | 0.0175 |
| total_usd | 0.764917 | 1.16222 |
| estimated_calls | 53 | 138 |
| calls: ANSWER_TEXT | 108 calls, 0.283394, p50 3444 ms, accepted 101, discarded 7 | 49 calls, 0.126182, p50 2986 ms, accepted 46, discarded 3 |
| calls: TOOL_CHOICE | 71 calls, 0.189254, p50 3958 ms, accepted 71 | 67 calls, 0.183099, p50 2921 ms, accepted 67 |
| calls: UNKNOWN | 53 calls, 0.276859, p50 15011 ms, cancelled 3, failed 50 | 138 calls, 0.835738, p50 2386 ms, cancelled 137, failed 1 |
| calls: WORDING | 75 calls, 0.01541, p50 916 ms, accepted 75 | 78 calls, 0.017203, p50 1019 ms, accepted 69, cancelled 1, discarded 8 |

| Mix | baseline | fast-answer |
|---|---|---|
| path | {"A": 75, "C": 108} | {"A": 69, "B": 6, "C": 108} |
| text source | {"model": 176, "template": 7, "message": 0} | {"model": 115, "template": 68, "message": 0} |
| unstable cases | OPS-101, OPS-103 | LOC-011, OPS-011 |
| rejected model calls | 0 of 307 | 1 of 332 |

## Per case (passed runs, complete p50 s, mean cost USD)

| Case | baseline | fast-answer |
|---|---|---|
| LOC-001 | 3/3, 0.9801, 0.000163 | 3/3, 0.8966, 0.000163 |
| LOC-002 | 3/3, 0.9537, 0.000192 | 3/3, 0.9043, 0.000192 |
| LOC-003 | 3/3, 0.9355, 0.000161 | 3/3, 0.9554, 0.000176 |
| LOC-004 | 3/3, 0.9797, 0.000257 | 3/3, 0.9894, 0.000254 |
| LOC-005 | 3/3, 1.0295, 0.000283 | 3/3, 0.9471, 0.000229 |
| LOC-006 | 3/3, 1.1116, 0.000209 | 3/3, 0.9326, 0.000193 |
| LOC-007 | 3/3, 21.2772, 0.010474 | 3/3, 7.1062, 0.013208 |
| LOC-008 | 0/3, 15.3036, 0.005830 | 3/3, 7.0494, 0.011132 |
| LOC-009 | 3/3, 16.4137, 0.005523 | 3/3, 7.05, 0.007899 |
| LOC-010 | 3/3, 25.5668, 0.009897 | 3/3, 7.0523, 0.014677 |
| LOC-011 | 3/3, 9.6707, 0.003457 | 2/3, 7.0488, 0.009138 |
| LOC-012 | 3/3, 19.2892, 0.005606 | 3/3, 7.0471, 0.015490 |
| NOR-001 | 3/3, 0.9923, 0.000096 | 3/3, 1.1864, 0.000090 |
| NOR-002 | 3/3, 7.1092, 0.005331 | 3/3, 7.1442, 0.011045 |
| NOR-003 | 3/3, 0.94, 0.000104 | 3/3, 0.9393, 0.000090 |
| BIN-001 | 3/3, 0.9075, 0.000139 | 3/3, 1.9223, 0.000307 |
| BIN-002 | 3/3, 0.9794, 0.000274 | 3/3, 1.1227, 0.000274 |
| BIN-003 | 3/3, 1.5025, 0.000166 | 3/3, 0.8755, 0.000164 |
| BIN-004 | 3/3, 17.186, 0.006333 | 3/3, 7.0344, 0.010028 |
| PAGE-001 | 3/3, 31.8233, 0.015336 | 3/3, 7.057, 0.014239 |
| PAGE-002 | 3/3, 0.9626, 0.000138 | 3/3, 1.2095, 0.000223 |
| FOL-001 | 3/3, 0.9849, 0.000176 | 3/3, 1.2067, 0.000167 |
| FOL-002 | 3/3, 5.2864, 0.003636 | 3/3, 7.0362, 0.010371 |
| OOS-001 | 3/3, 3.3027, 0.001539 | 3/3, 4.5827, 0.008341 |
| OOS-002 | 3/3, 4.9598, 0.002024 | 3/3, 5.4862, 0.007158 |
| UNS-001 | 3/3, 7.6308, 0.001634 | 3/3, 6.5727, 0.009197 |
| UNS-002 | 3/3, 7.1362, 0.001887 | 3/3, 4.8443, 0.008479 |
| UNS-003 | 3/3, 6.9202, 0.001753 | 3/3, 1.8963, 0.001551 |
| PERM-001 | 3/3, 3.8626, 0.001734 | 3/3, 7.8809, 0.010805 |
| PERM-002 | 3/3, 1.0495, 0.000201 | 3/3, 0.9394, 0.000166 |
| PERM-003 | 3/3, 3.5096, 0.001593 | 3/3, 2.4906, 0.004250 |
| INJ-001 | 3/3, 18.7321, 0.006363 | 3/3, 2.6443, 0.003899 |
| INJ-002 | 3/3, 9.2793, 0.003793 | 3/3, 3.3783, 0.003759 |
| INJ-003 | 3/3, 1.1699, 0.000190 | 3/3, 1.2555, 0.000197 |
| INJ-004 | 3/3, 2.7282, 0.003719 | 3/3, 6.9403, 0.008306 |
| INJ-005 | 3/3, 8.4191, 0.004773 | 3/3, 6.326, 0.007452 |
| INJ-006 | 3/3, 6.6707, 0.003843 | 3/3, 5.4073, 0.007272 |
| INJ-007 | 3/3, 6.0742, 0.001576 | 3/3, 5.1666, 0.008284 |
| IND-001 | 3/3, 0.9856, 0.000165 | 3/3, 1.0643, 0.000166 |
| IND-002 | 3/3, 28.2091, 0.011564 | 3/3, 7.0531, 0.018102 |
| IND-003 | 3/3, 23.8471, 0.011540 | 3/3, 7.0411, 0.012006 |
| IND-004 | 3/3, 1.0265, 0.000149 | 3/3, 1.1219, 0.000166 |
| PERM-004 | 3/3, 13.2243, 0.003854 | 3/3, 6.3922, 0.007958 |
| OPS-001 | 0/3, 1.1854, 0.000862 | 3/3, 1.5611, 0.000986 |
| OPS-002 | 3/3, 1.0812, 0.000080 | 3/3, 1.2156, 0.000081 |
| OPS-003 | 3/3, 0.9477, 0.000080 | 3/3, 1.055, 0.000080 |
| OPS-004 | 3/3, 0.8598, 0.000146 | 3/3, 1.0991, 0.000151 |
| OPS-005 | 3/3, 0.9697, 0.000277 | 3/3, 1.2033, 0.000276 |
| OPS-006 | 3/3, 9.0447, 0.008600 | 3/3, 7.2336, 0.014001 |
| OPS-007 | 3/3, 0.9891, 0.000235 | 3/3, 1.0515, 0.000245 |
| OPS-008 | 3/3, 0.9245, 0.000173 | 3/3, 1.5493, 0.000482 |
| OPS-009 | 0/3, 1.0355, 0.000220 | 3/3, 1.2643, 0.000216 |
| OPS-010 | 3/3, 15.5995, 0.003441 | 3/3, 7.0408, 0.016681 |
| OPS-011 | 3/3, 41.8895, 0.023391 | 2/3, 7.1336, 0.018752 |
| OPS-012 | 3/3, 45.0448, 0.018836 | 3/3, 7.0523, 0.017940 |
| OPS-101 | 2/3, 20.9292, 0.015344 | 3/3, 7.0425, 0.016149 |
| OPS-102 | 3/3, 34.8683, 0.016578 | 3/3, 7.0343, 0.016825 |
| OPS-103 | 2/3, 11.6022, 0.004224 | 3/3, 7.0414, 0.008109 |
| OPS-104 | 3/3, 25.0601, 0.007926 | 3/3, 7.0472, 0.008830 |
| OPS-105 | 3/3, 21.7766, 0.006136 | 3/3, 7.0506, 0.006216 |
| OPS-106 | 3/3, 20.6569, 0.010747 | 3/3, 7.044, 0.014124 |

## Failed cases: baseline

56 of 61 cases passed.

- **LOC-008** (DATA):
  - run 1: path C, outcome ANSWER, tool find_product_locations {'sku': 'SKU-102', 'product_query': 'wireless mouse'}, facts missing ['SKU-102'], grounding [], forbidden [], run id 01a1f7ac-c7e5-70f5-8dc5-1ded993d6aa8
  - run 2: path C, outcome ANSWER, tool find_product_locations {'sku': 'SKU-102', 'product_query': 'wireless mouse'}, facts missing ['SKU-102'], grounding [], forbidden [], run id 01a1f7ad-20f9-79b6-81c3-71c501c3863a
  - run 3: path C, outcome ANSWER, tool find_product_locations {'sku': 'SKU-102', 'product_query': 'wireless mouse'}, facts missing ['SKU-102'], grounding [], forbidden [], run id 01a1f7ad-5cd3-7d9c-9ead-ea4ef4a60963
- **OPS-001** (DATA):
  - run 1: path A, outcome ANSWER, tool get_low_stock {}, facts missing ['SKU-147'], grounding [], forbidden [], run id 01a1f7c2-d019-72db-89dd-3386d625badd
  - run 2: path A, outcome ANSWER, tool get_low_stock {}, facts missing ['SKU-147'], grounding [], forbidden [], run id 01a1f7c2-e32c-7ed4-8d43-36b05424e67f
  - run 3: path A, outcome ANSWER, tool get_low_stock {}, facts missing ['SKU-147'], grounding [], forbidden [], run id 01a1f7c2-e7db-77b1-9e1b-fc61e38b99e5
- **OPS-009** (DATA):
  - run 1: path A, outcome ANSWER, tool get_slow_in_prime {}, facts missing ['SKU-116', 'A7-02-03'], grounding [], forbidden [], run id 01a1f7c4-9617-7690-9337-01bbdd9d91ce
  - run 2: path A, outcome ANSWER, tool get_slow_in_prime {}, facts missing ['SKU-116', 'A7-02-03'], grounding [], forbidden [], run id 01a1f7c4-a364-7693-9f2f-e72d165d48dd
  - run 3: path A, outcome ANSWER, tool get_slow_in_prime {}, facts missing ['SKU-116', 'A7-02-03'], grounding [], forbidden [], run id 01a1f7c4-ad2a-722a-93db-f91b7d347cc3
- **OPS-101** (DATA):
  - run 1: path C, outcome ANSWER, tool get_low_stock {}, facts missing ['SKU-147'], grounding [], forbidden [], run id 01a1f7c9-a3f3-7d3a-be9a-369d6c0c7df1
- **OPS-103** (DATA):
  - run 1: path C, outcome ANSWER, tool get_dead_stock {}, facts missing ['SKU-148'], grounding [], forbidden [], run id 01a1f7cd-00a7-74d1-ab60-2fb11aaa1030

## Failed cases: fast-answer

59 of 61 cases passed.

- **LOC-011** (DATA):
  - run 2: path C, outcome NO_RESULTS, tool find_product_locations {'sku': None, 'product_query': 'USB-C cables'}, facts missing [], grounding [], forbidden [], run id 01a1f7b0-bc6c-703b-b069-cd72433deaaf
- **OPS-011** (FOLLOW_UP):
  - run 1: path C, outcome ANSWER, tool query_inventory {'measures': ['available'], 'dimensions': [], 'filters': [{'field': 'zone', 'op': '=', 'value': 'Zone B'}], 'time_range': None, 'sort': None, 'limit': 20, 'scope': {'working_set': True}}, facts missing [], grounding [], forbidden [], run id 01a1f7c6-6215-7480-89d9-e6d0f275d270
