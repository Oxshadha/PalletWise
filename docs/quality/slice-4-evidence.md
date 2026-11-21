# Slice 4 evidence record

Slice 4 (slice-4-acceptance): time shift, scenarios, reset on both sides, the time bar, the guided demo and the bell. Measured on 2026-11-21 on the one-laptop stack (Apple silicon, 8 CPU, 8 GB memory; Docker VM 8 CPU, 5.3 GB), with the 180-day reference site (seed 42).

## 1. The storyline test, twice (Definition of done)

`make storyline` (`evals/storyline.py`) signs in as the dev Manager and drives the real screen: a reset through the time bar, then every step of `storyline.json` (its setup through the bar's confirmation, every prompt chip through the copilot), then a separate +1 day shift. Nothing is written except through the product. Results: `docs/quality/storyline/2026-11-21-results.json`.

The two runs agree on everything that must agree: the site time and the inbox count after every step, and every question answered by the copilot (no error, no limited-mode answer).

| Step | Site time | Open exceptions (both runs) |
|---|---|---|
| 1. Start of shift | 06:00, 1 Apr 2026 | 64 |
| 2. Find anything | 06:00, 1 Apr | 64 |
| 3. A big outlet order arrives | 06:15, 1 Apr | 64 (the SKU-258 stockout risk ranks first) |
| 4. Investigate | 06:15, 1 Apr | 64 |
| 5. Time passes (+4 hours) | 10:15, 1 Apr | 62 |
| 6. Next morning | 06:00, 2 Apr | 83 |
| 7. The festival approaches (+1 week) | 06:00, 9 Apr | 106 |
| 8. Ask anything | 06:00, 9 Apr | 106 |

15 questions per run, all answered; the out-of-scope question ("Show me the database password") declined. No em or en dashes on screen.

## 2. Time shift limits (step 6, agreed before measuring)

Total: from confirming in the bar until the bar shows the result and the exception engine has recalculated the new moment ("with the screens updated"). Screen latency: from the API reporting the shift applied until the bar shows it.

| Shift | Run 1 total | Run 2 total | Limit | Screen latency run 1, run 2 | Limit |
|---|---|---|---|---|---|
| +4 hours | 4.5 s | 8.4 s | none | 0.74 s, 0.85 s | 2 s |
| Next morning | 5.8 s | 5.3 s | none | 0.77 s, 0.86 s | 2 s |
| +1 week | 7.5 s | 18.2 s | 180 s | 0.85 s, 0.95 s | 2 s |
| +1 day | 8.9 s | 16.6 s | 30 s | 0.84 s, **3.85 s** | 2 s |
| Reset to READY | 114.5 s | 96.1 s | none | | |

Every total is far inside its limit. One screen latency missed the 2 s limit: run 2's last shift, 3.85 s. At that moment the Mac's swap was full (15.95 of 16 GB) and the whole machine slowed (the same run's week shift took 18 s against 7 s in run 1). Across all runs of the day, 23 other shifts reached the screen in 0.4 to 1.3 s. Recorded as measured, not rerun until green. Breakdown by stage was not needed: no total limit was missed.

## 3. Found during the live checks, and what was done

| Finding | Done |
|---|---|
| The Slice 3 browser check had appended characters to the live connector cursor (to force a re-evaluation) and never reverted it; the live connector failed every batch from 08:04 | Repaired only through the product: a reset as the Manager through the screen, rebuilt to the golden dataset hash. Rule recorded: live checks change data only through time shift, scenarios and reset. Slice 3's live-update timings (1.34 s, 0.86 s) came from such direct writes. |
| The running OPA container still had the policy from before `simulation.control`; the bar did not appear | OPA restarted (it loads the policy at start). A stack rebuild does the same. |
| The brief card covered the demo panel's buttons (the bar's blur is its own stacking context) | The bar sits above the workspace. |
| After a reset the demo reopened at its last step | A reset starts the demo again at step 1 (with a test). |
| With the Mac's swap full, Keycloak's token endpoint stalled for up to 20 s during a rebuild or a week shift (probe: median 0.07 s, p95 0.5 s, maximum 20.4 s); the API then refused a copilot question as "sign-in service not reachable" | Correct fail-closed behaviour, kept. Docker Desktop restarted to free its swapped memory (product owner's choice); the runs above followed. Demo practice: close other apps first; reset before the audience arrives. |
| One model call timed out at the provider (an 18 s read timeout); the copilot gave its limited-mode answer | Correct degradation. The storyline test now counts a limited-mode answer as a failure. |

## 4. Gates

| Gate | Result |
|---|---|
| Lint (ruff, pyright, eslint, tsc, prettier) | pass |
| Unit | 410 passed |
| Integration (testcontainers) | 340 passed |
| Policy (OPA) | 17 of 17 |
| Web | 92 passed |
| Deterministic eval gate | pass, 104 of 104 |
| Scenario expectations (step 3) | pass (`tests/integration/test_scenarios.py`) |
| Model gate | not rerun: Slice 4 changed no agent, tool, prompt, semantic or eval-set file (`git diff 22bbda4..HEAD` over those paths is empty except the reset migration); the storyline's 30 live questions ran on the real model |

## 5. Open items carried forward

| Item | Where |
|---|---|
| Allocated bins and pick faces of unallocated quantity shown differently on the map (listed for Slice 4 in the Slice 3 record, not in the approved Slice 4 acceptance) | Product owner to place |
| `outlet_window_at_risk` after task execution, and `FESTIVAL_SHORTAGE` for `festival_rush` (D7) | Slice 6 |
| Reset expires proposals and fails submitted tasks (CWM 7.4 step 2) | Slice 6, with those tables |
| The laptop stack under memory pressure (Keycloak stalls, a slower screen): a lighter local profile or Keycloak memory settings | Slice 8 (release hardening) |
| Governed SQL fallback tool: ADR first | Slice 5 |
