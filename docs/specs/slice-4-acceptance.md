# Slice 4: Acceptance Criteria

**Status:** Draft for product owner approval
**Goal:** The demo runs itself: time moves at a chosen speed, the warehouse lives (orders arrive, picks happen, stock moves, exceptions open and resolve), scenarios stage a story on demand, and the Admin controls it all from the screen. Today the clock stands still at the clock start; Slice 4 makes it move.

This document is a plan against the approved specifications. It does not redefine them. Where a decision below refines a spec, the spec gets a change-log entry when the work lands.

| Spec | What Slice 4 implements from it |
|---|---|
| Requirements (approved) | FR-SIM-04 (play, pause, speed, jump, reset), FR-SIM-05 (calendar bounds), FR-SIM-06 (scenarios), FR-SIM-07 (guided tour, Should), FR-SIM-09 (consistent clock), the notification bell (UX spec 2) |
| Simulator (approved) | 5.2 live operation, 5.3 determinism with the input log, 6 scenarios, 7 admin controls, 7.1 reset on both sides, the section 9 tests for these |
| Canonical warehouse model (approved) | 7.4 `reset_simulated_site()`, tick markers driving `site_clock` |
| Identity and access (approved) | `admin.simulation.control` (Admin only) |
| UX screens (approved) | Notification bell (row 27); a short section for the simulation controls when this slice starts |

## 1. Scope

**In scope**

- Live operation in the simulator: each simulated day's schedule generated at its start from the seed and the input log, events emitted as time passes, tick markers moving the PalletWise clock (simulator 5.2).
- Controls: play, pause, speed 1x, 10x and 60x, jump forward within the calendar, run a scenario, reset. Admin only, confirmed in the UI and audited.
- Scenarios as YAML files with preconditions and expectations: `monsoon_slowdown`, `dead_stock_in_prime`, `outlet_window_at_risk` and `festival_rush` (see D3 for the parts that wait for later slices).
- Reset on both sides (simulator 7.1): a clean new run from the seed, the site `REBUILDING` then `READY`, never partial data shown as ready.
- Screen: site time and speed for everyone; the simulation controls for the Admin; the notification bell; the guided tour from a scenario's tour lines.
- Carried from Slice 3: the 3D scene renders on demand instead of continuously; the 2D plan's overlapping zone label.

**Out of scope (later slices)**

| Item | Slice |
|---|---|
| Governed SQL fallback tool (ADR first) | 5 |
| Proposals and task execution by the simulator (`submit_task`, the after-execution part of `outlet_window_at_risk`) | 6 |
| `FESTIVAL_SHORTAGE` and the festival question (the expectation of `festival_rush`) | 6 |
| Admin panel: users, agent registry, settings editor, usage, unanswered questions | 7 |
| Audit viewer and sealing, release hardening | 8 |

## 2. Decisions

| # | Decision | Why |
|---|---|---|
| D1 | The simulator owns the tick loop and its state. Admin commands go browser to API (OPA `admin.simulation.control`, audit, CSRF) to the simulator's internal control endpoint; PalletWise never moves its own clock, which follows tick markers only | Simulator decision 10 and FR-SIM-01: the simulator is a data source like any WMS; one clock, driven by ingested data |
| D2 | After a simulator restart, its state is rebuilt from the seed, the input log and the last emitted time, and it comes back **paused** at that time | A restart never jumps time or replays events twice; the Admin chooses when to continue |
| D3 | All four scenario files are written now. `outlet_window_at_risk` checks its exception now and its after-execution expectation in Slice 6; `festival_rush` applies its events now and its `FESTIVAL_SHORTAGE` expectation is checked in Slice 6 | The demo can stage every story now; no expectation is faked before its feature exists |
| D4 | Until the Admin panel (Slice 7), the controls are a compact simulation bar under the page title, shown to the Admin only. Everyone sees the site time and "paused" or "running at 60x" | The demo needs the controls now; the full Admin panel stays one coherent slice |
| D5 | The bell shows a dot when exceptions opened since the viewer last opened the inbox; "last seen" is kept per viewer in the browser | UX spec row 27; a per-viewer convenience, not shared state |
| D6 | The guided tour shows a scenario's tour lines as suggestion chips that run through the copilot like any suggestion; it can be left at any step | FR-SIM-07; no new answer path |
| D7 | Live speed target: at 60x the PalletWise clock stays within 5 simulated minutes of the simulator, and a change reaches the screen within 2 seconds (NFR-PERF-05) | The demo is shown at 60x; ingestion and the exception engine must keep up |

## 3. Acceptance criteria by step

### Step 1: Live operation

- The simulator continues the run after the clock start, day by day, with the same generator as the history; events and tick markers go to the outbox in sequence.
- Tests (simulator 9): jump equals play (state after jumping to T equals state after playing to T); tick markers move `site_clock` only when applied, never ahead of applied events; the same seed and input log give the same state; demand is 0 on non-operating days.

### Step 2: Controls

- `POST /api/sites/{id}/simulation/{play|pause|speed|jump}` and `GET .../simulation` (status for every reader of the site). Admin only for commands, OPA scope `admin.simulation.control`, audited with before and after, CSRF checked.
- Jump forward only, within the calendar (FR-SIM-05); refused with a message otherwise. A jump emits the skipped period's events in batches; the site stays readable meanwhile.
- Tests: a Manager is refused (403, audited); jump backward and beyond the calendar are refused; speed changes take effect on the next tick.

### Step 3: Scenarios

- Scenario files in `simulator/scenarios/`, the closed list of event types, preconditions checked first (refused with the reason, never run at another time), events applied from the current simulated time and recorded in the input log.
- Expectations run by the evaluation suite against a fresh environment: `monsoon_slowdown` (rising `LOW_STOCK`, falling days of cover), `dead_stock_in_prime` (the six anchors), `outlet_window_at_risk` (a fixable `STOCKOUT_RISK` for SKU-214 within 90 minutes).
- Tests: preconditions refuse; the same seed and input log reproduce the state; golden highlights per dataset version.

### Step 4: Reset

- Reset on both sides exactly as simulator 7.1: site `REBUILDING`, old-run data and stream records skipped, new run from the seed, dataset hash checked, then `READY`; a failure leaves the site `FAILED`, never `READY`. Open exceptions resolve as `SIMULATION_RESET`; the day gets a new brief (Slice 3).
- Tests: a failure during regeneration leaves `FAILED`; old-run records are skipped; the screen shows "data is being rebuilt" and recovers without a reload.

### Step 5: Screens

- Simulation bar for the Admin: play or pause, speed, jump to a date and time, run scenario (with its title and preconditions), reset; every action asks for confirmation and shows the result. Site time and run state for everyone.
- Notification bell with a dot for new exceptions; opening it lists them and leads to the inbox.
- Guided tour from a running scenario's tour lines.
- 3D scene renders on demand; the zone label no longer overlaps.
- Tests: the Admin sees the bar, the Manager does not; confirmations; the bell's dot appears for a new exception and clears when seen; no em or en dashes.

### Step 6: Quality

- Performance (D7): at 60x for 10 real minutes, the PalletWise clock lag and the change-to-screen time are measured and within target; the exception engine keeps up.
- Evals: the scenario expectations of step 3 pass; the existing gates stay green.
- Live browser check: play at 60x and watch orders, the map and the inbox move; run `outlet_window_at_risk` and see its exception open; reset and see the site rebuild; the bell and the tour.

## 4. Definition of done

- Every step's criteria pass; engineering baseline 8.1 is met.
- Unit and integration tests, the deterministic eval gate, lint and CI pass.
- The live browser check of step 6, recorded in an evidence file like Slice 3's.
- Code review and security review at the end of the slice; findings fixed or recorded.

## 5. Change log

| Date | Change |
|---|---|
| 2026-11-21 | Draft for product owner approval |
