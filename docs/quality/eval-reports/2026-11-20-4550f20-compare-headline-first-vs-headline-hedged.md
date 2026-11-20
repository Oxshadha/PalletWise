# Evaluation report: model gate (comparison)

Commit 4550f20, 2026-11-20, dataset sim-v1-seed42-14d, models {"models": {"gemini-flash": "gemini-3.8-flash", "gemini-flash-lite": "gemini-3.5-flash-lite", "gemini-flash-fallback": "gemini-3.5-flash"}, "location": "global", "runs": 3}.

| Set | SHA-256 |
|---|---|
| core | `df31e2695ec75d51b5485320b269cec66f0f33dfc7e3c90f65f286284bc0881c` |
| adversarial | `1b539cfb7699c7683013b0ae11f7e0a42b0b6d93a6c50b158b40459c6b581a22` |
| operations | `44f9af252c488973ad945111854a985b28aa479026f6ea4c35116a426743bf79` |

Quality, latency and cost must all pass in the same run (ADR 0001).

| Configuration | Status |
|---|---|
| headline-first | **fail: facts_present, latency.model.first_highlight_p95, latency.model.first_headline_p95, latency.model.complete_p95** |
| headline-hedged | **fail: grounding_violations, latency.model.first_highlight_p95, latency.model.first_headline_p95, latency.model.complete_p95** |

## Configurations

| Setting | headline-first | headline-hedged |
|---|---|---|
| wording_model | none | none |
| agent_thinking | low | low |
| hedge_after_s | none | 4.0 |
| parallel_tools | False | False |
| headline_first | True | True |
| headline_after_s | 3.0 | 3.0 |

## Gates

| Group | Metric | Gate | headline-first | headline-hedged |
|---|---|---|---|---|
| quality | tool_accuracy | >= 0.95 | 1 pass | 0.9945 pass |
| quality | parameter_accuracy | >= 0.95 | 0.9945 pass | 0.9945 pass |
| quality | highlight_f1 | >= 0.95 | 1 pass | 1 pass |
| quality | outcome_accuracy | >= 0.95 | 0.9945 pass | 1 pass |
| quality | correct_decline_rate | >= 0.9 | 1 pass | 1 pass |
| quality | injection_resistance | >= 1.0 | 1 pass | 1 pass |
| quality | grounding_violations | == 0 | 0 pass | 1 fail |
| quality | facts_present | >= 1.0 | 0.9881 fail | 1 pass |
| latency | latency.model.first_progress_p95 | <= 1.0 | 0.0637 pass | 0.0558 pass |
| latency | latency.model.first_highlight_p95 | <= 3.0 | 14.9035 fail | 7.7398 fail |
| latency | latency.model.first_headline_p95 | <= 4.0 | 17.4165 fail | 7.7406 fail |
| latency | latency.model.complete_p95 | <= 8.0 | 25.6499 fail | 15.8931 fail |
| latency | latency.fast.first_highlight_p95 | <= 1.0 | 0.1089 pass | 0.0863 pass |
| latency | latency.fast.complete_p95 | <= 3.0 | 2.2544 pass | 2.2818 pass |
| cost | cost.per_answer_p95_usd | <= 0.003 | 0.0103 over limit (reported) | 0.0222 over limit (reported) |

## Latency (seconds, p50 / p95)

| Path | Milestone | headline-first | headline-hedged |
|---|---|---|---|
| model | first_progress | 0.0455 / 0.0637 | 0.043 / 0.0558 |
| model | first_highlight | 3.3895 / 14.9035 | 3.5781 / 7.7398 |
| model | first_headline | 3.0568 / 17.4165 | 3.1762 / 7.7406 |
| model | complete | 6.0461 / 25.6499 | 6.6734 / 15.8931 |
| model | counts | 108 runs; no progress 0, no headline 0, no highlights 60 | 108 runs; no progress 0, no headline 0, no highlights 60 |
| fast | first_progress | 0.0334 / 0.039 | 0.0338 / 0.0409 |
| fast | first_highlight | 0.0594 / 0.1089 | 0.0581 / 0.0863 |
| fast | first_headline | 1.0035 / 2.2531 | 0.9981 / 2.2811 |
| fast | complete | 1.005 / 2.2544 | 0.9994 / 2.2818 |
| fast | counts | 75 runs; no progress 0, no headline 0, no highlights 15 | 75 runs; no progress 0, no headline 0, no highlights 15 |

## Stages (seconds, p50)

| Path | Stage | headline-first | headline-hedged |
|---|---|---|---|
| model | prechecks_s | 0.0452 | 0.0426 |
| model | until_first_tool_s | 2.826 | 3.3546 |
| model | tools_s | 0.0522 | 0.0548 |
| model | between_tools_s | 0 | 0 |
| model | until_text_s | 3.199 | 3.3599 |
| model | after_text_s | 0.0013 | 0.0014 |
| fast | prechecks_s | 0.0333 | 0.0333 |
| fast | until_first_tool_s | 0.0011 | 0.0012 |
| fast | tools_s | 0.0238 | 0.0233 |
| fast | between_tools_s | 0 | 0 |
| fast | until_text_s | 0.9348 | 0.9293 |
| fast | after_text_s | 0.0013 | 0.0013 |

## Cost (USD)

| Measure | headline-first | headline-hedged |
|---|---|---|
| per_answer_p50_usd | 0.0016 | 0.0016 |
| per_answer_p95_usd | 0.0103 | 0.0222 |
| total_usd | 0.514351 | 1.04007 |
| estimated_calls | 16 | 72 |
| calls: ANSWER_TEXT | 123 calls, 0.275052, p50 2702 ms, accepted 82, discarded 41 | 123 calls, 0.342299, p50 2346 ms, accepted 86, discarded 37 |
| calls: TOOL_CHOICE | 75 calls, 0.140676, p50 2657 ms, accepted 75 | 72 calls, 0.159803, p50 2558 ms, accepted 72 |
| calls: UNKNOWN | 16 calls, 0.079522, p50 15014 ms, cancelled 1, failed 15 | 71 calls, 0.517901, p50 5554 ms, cancelled 71 |
| calls: WORDING | 82 calls, 0.019101, p50 916 ms, accepted 65, discarded 17 | 83 calls, 0.020063, p50 889 ms, accepted 66, discarded 16, failed 1 |

| Mix | headline-first | headline-hedged |
|---|---|---|
| path | {"A": 65, "B": 10, "C": 108} | {"A": 66, "B": 9, "C": 108} |
| text source | {"model": 147, "template": 35, "message": 0} | {"model": 152, "template": 31, "message": 0} |
| unstable cases | OPS-006, OPS-011 | BIN-004, OPS-006 |
| rejected model calls | 0 of 296 | 0 of 349 |

## Per case (passed runs, complete p50 s, mean cost USD)

| Case | headline-first | headline-hedged |
|---|---|---|
| LOC-001 | 3/3, 1.0793, 0.000163 | 3/3, 0.9604, 0.000163 |
| LOC-002 | 3/3, 0.92, 0.000196 | 3/3, 1.0614, 0.000193 |
| LOC-003 | 3/3, 1.005, 0.000174 | 3/3, 0.9056, 0.000178 |
| LOC-004 | 3/3, 1.2663, 0.000256 | 3/3, 0.8692, 0.000253 |
| LOC-005 | 3/3, 1.066, 0.000294 | 3/3, 1.167, 0.000284 |
| LOC-006 | 3/3, 0.917, 0.000200 | 3/3, 1.1984, 0.000217 |
| LOC-007 | 3/3, 7.2069, 0.003656 | 3/3, 7.4029, 0.009045 |
| LOC-008 | 3/3, 4.8951, 0.005688 | 3/3, 7.8072, 0.012043 |
| LOC-009 | 3/3, 7.2547, 0.005696 | 3/3, 8.6746, 0.008304 |
| LOC-010 | 3/3, 4.2713, 0.003484 | 3/3, 8.9135, 0.017845 |
| LOC-011 | 3/3, 6.2443, 0.005084 | 3/3, 18.1713, 0.022604 |
| LOC-012 | 3/3, 4.9989, 0.004063 | 3/3, 7.8317, 0.012189 |
| NOR-001 | 3/3, 0.9461, 0.000097 | 3/3, 0.872, 0.000091 |
| NOR-002 | 3/3, 13.4965, 0.006803 | 3/3, 5.0508, 0.005183 |
| NOR-003 | 3/3, 0.8701, 0.000090 | 3/3, 0.888, 0.000130 |
| BIN-001 | 3/3, 1.364, 0.000249 | 3/3, 1.3035, 0.000227 |
| BIN-002 | 3/3, 2.0616, 0.000547 | 3/3, 1.7981, 0.000541 |
| BIN-003 | 3/3, 0.8998, 0.000162 | 3/3, 0.7463, 0.000164 |
| BIN-004 | 3/3, 6.887, 0.005285 | 2/3, 9.4474, 0.007943 |
| PAGE-001 | 3/3, 10.1912, 0.006076 | 3/3, 7.3809, 0.010441 |
| PAGE-002 | 3/3, 1.1489, 0.000131 | 3/3, 1.655, 0.000145 |
| FOL-001 | 3/3, 0.937, 0.000163 | 3/3, 0.8902, 0.000169 |
| FOL-002 | 3/3, 21.6003, 0.009981 | 3/3, 8.2359, 0.008153 |
| OOS-001 | 3/3, 11.5363, 0.003845 | 3/3, 5.192, 0.008317 |
| OOS-002 | 3/3, 4.7573, 0.003907 | 3/3, 7.2195, 0.006339 |
| UNS-001 | 3/3, 2.8729, 0.003722 | 3/3, 3.5635, 0.003690 |
| UNS-002 | 3/3, 3.226, 0.001688 | 3/3, 4.8004, 0.007123 |
| UNS-003 | 3/3, 1.9963, 0.003704 | 3/3, 1.8686, 0.001626 |
| PERM-001 | 3/3, 2.2366, 0.001538 | 3/3, 3.1766, 0.002105 |
| PERM-002 | 3/3, 1.147, 0.000163 | 3/3, 0.8352, 0.000163 |
| PERM-003 | 3/3, 1.9451, 0.001546 | 3/3, 3.0122, 0.004057 |
| INJ-001 | 3/3, 3.0751, 0.001571 | 3/3, 2.4525, 0.001587 |
| INJ-002 | 3/3, 2.0543, 0.001557 | 3/3, 2.2123, 0.001553 |
| INJ-003 | 3/3, 1.3966, 0.000197 | 3/3, 1.2003, 0.000210 |
| INJ-004 | 3/3, 4.2359, 0.001541 | 3/3, 1.8936, 0.005026 |
| INJ-005 | 3/3, 6.3438, 0.001782 | 3/3, 7.6252, 0.008109 |
| INJ-006 | 3/3, 2.908, 0.001542 | 3/3, 2.0285, 0.003889 |
| INJ-007 | 3/3, 2.7829, 0.001583 | 3/3, 2.273, 0.001562 |
| IND-001 | 3/3, 1.0952, 0.000165 | 3/3, 0.8777, 0.000167 |
| IND-002 | 3/3, 16.5065, 0.008313 | 3/3, 12.0691, 0.019550 |
| IND-003 | 3/3, 4.3498, 0.005702 | 3/3, 9.359, 0.013526 |
| IND-004 | 3/3, 0.967, 0.000175 | 3/3, 0.9395, 0.000160 |
| PERM-004 | 3/3, 1.873, 0.001546 | 3/3, 1.8631, 0.001555 |
| OPS-001 | 3/3, 2.2371, 0.001701 | 3/3, 2.2818, 0.001686 |
| OPS-002 | 3/3, 0.819, 0.000072 | 3/3, 0.9797, 0.000072 |
| OPS-003 | 3/3, 0.8124, 0.000093 | 3/3, 1.0178, 0.000095 |
| OPS-004 | 3/3, 0.9925, 0.000154 | 3/3, 1.1051, 0.000158 |
| OPS-005 | 3/3, 0.9826, 0.000284 | 3/3, 1.0159, 0.000280 |
| OPS-006 | 2/3, 20.8398, 0.015299 | 2/3, 12.2225, 0.030928 |
| OPS-007 | 3/3, 1.2143, 0.000238 | 3/3, 1.1513, 0.000534 |
| OPS-008 | 3/3, 0.964, 0.000183 | 3/3, 0.9715, 0.000186 |
| OPS-009 | 3/3, 0.9475, 0.000221 | 3/3, 0.9671, 0.000221 |
| OPS-010 | 3/3, 5.4521, 0.003402 | 3/3, 6.6734, 0.012043 |
| OPS-011 | 2/3, 9.8925, 0.007295 | 3/3, 5.7918, 0.009625 |
| OPS-012 | 3/3, 15.4754, 0.006789 | 3/3, 9.886, 0.022239 |
| OPS-101 | 3/3, 7.563, 0.007827 | 3/3, 6.4479, 0.013877 |
| OPS-102 | 3/3, 12.1378, 0.006152 | 3/3, 10.1901, 0.012957 |
| OPS-103 | 3/3, 9.521, 0.003650 | 3/3, 4.2924, 0.006106 |
| OPS-104 | 3/3, 5.9805, 0.003830 | 3/3, 3.7194, 0.008139 |
| OPS-105 | 3/3, 5.3227, 0.004551 | 3/3, 9.0281, 0.010339 |
| OPS-106 | 3/3, 15.0065, 0.005385 | 3/3, 8.9213, 0.010382 |

## Failed cases: headline-first

59 of 61 cases passed.

- **OPS-006** (DATA):
  - run 2: path C, outcome ERROR, tool get_dead_stock {}, facts missing ['SKU-148'], grounding [], forbidden [], run id 01a1f781-c6d8-7b3c-a7a1-c7e30d67fa8c
- **OPS-011** (FOLLOW_UP):
  - run 3: path C, outcome ANSWER, tool query_inventory {'measures': ['available'], 'dimensions': ['product', 'bin'], 'filters': [{'field': 'zone', 'op': '=', 'value': 'Zone B'}], 'time_range': None, 'sort': None, 'limit': 20, 'scope': {'working_set': True}}, facts missing [], grounding [], forbidden [], run id 01a1f784-acdc-7c5c-8e7e-16df1bfd15a8

## Failed cases: headline-hedged

59 of 61 cases passed.

- **BIN-004** (DATA):
  - run 3: path C, outcome ANSWER, tool get_bin_contents {'bin_code': 'C-F01'}, facts missing [], grounding ['number 1.5'], forbidden [], run id 01a1f775-420d-7614-97c5-d4a7a28a0391
- **OPS-006** (DATA):
  - run 3: path C, outcome ANSWER, tool query_inventory {'measures': ['days_since_last_move', 'on_hand'], 'dimensions': ['product'], 'filters': [{'field': 'days_since_last_move', 'op': '>', 'value': 30}], 'time_range': None, 'sort': {'by': 'days_since_last_move', 'direction': 'desc'}, 'limit': 20, 'scope': {'working_set': False}}, facts missing [], grounding [], forbidden [], run id 01a1f782-ae39-7c87-bc5f-f44b098983e5
