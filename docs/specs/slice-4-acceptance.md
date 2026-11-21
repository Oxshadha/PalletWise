# Slice 4: Acceptance Criteria

**Status:** Approved by the product owner on 2026-11-21 (with the review's concurrency rule (D9) and fixed shift limits (step 6); D8 kept per browser)
**Goal:** Every feature can be shown to a client live, by the presenter's own clicks, in one guided story. The only thing the presenter cannot do in a real warehouse is wait hours or days; Slice 4 adds **time shift**: move the simulated warehouse forward on demand, with everything that happens in that period really generated, ingested and recalculated.

This document is a plan against the approved specifications. It does not redefine them. Where a decision below refines a spec, the spec gets a change-log entry when the work lands.

| Spec | What Slice 4 implements from it |
|---|---|
| Requirements (approved) | FR-SIM-04 (jump forward and reset; play, pause and speed are deferred, D2), FR-SIM-05 (calendar bounds), FR-SIM-06 (scenarios), FR-SIM-07 (guided tour), FR-SIM-09 (consistent clock), the notification bell (UX spec 2) |
| Simulator (approved) | 5.2 per-day schedules and jump, 5.3 determinism with the input log, 6 scenarios, 7 admin controls (jump, run scenario, reset), 7.1 reset on both sides, the section 9 tests for these |
| Canonical warehouse model (approved) | 7.4 `reset_simulated_site()`, tick markers driving `site_clock`; `site_clock.mode` tells a simulated site |
| Identity and access (approved) | A new scope `simulation.control` (D3) |
| UX screens (approved) | Notification bell (row 27); sections for the time shift bar and the guided demo |

## 1. Scope

**In scope**

- **Time shift:** +1 hour, +4 hours, +1 day, +1 week, or a chosen date and time, forward only and within the calendar. The simulator generates every event of the skipped period (orders, allocations, picks, shipments, receipts, replenishments, tick markers) with the same generator as the history; the connector ingests them in order; the exception engine, the live stream and the brief react as they would to a real WMS.
- **Scenarios:** YAML files that stage a situation through the simulator's input log (for example a large early outlet order, slower deliveries): `outlet_window_at_risk`, `monsoon_slowdown`, `dead_stock_in_prime`, `festival_rush`.
- **Guided demo:** one built-in storyline (section 2.1) that walks through every feature built so far; each step may run a scenario event or a time shift, then shows what to click or ask. The presenter does every click.
- **Reset:** back to the clean start from the seed for the next demo (simulator 7.1).
- **Notification bell** for new exceptions.
- Carried from Slice 3: the 3D scene renders on demand instead of continuously; the 2D plan's overlapping zone label.

**Out of scope (later slices)**

| Item | Slice |
|---|---|
| Play, pause and speeds (a clock that runs by itself) | later, if wanted (D2) |
| Governed SQL fallback tool (ADR first) | 5 |
| Proposals and task execution by the simulator; the after-execution part of `outlet_window_at_risk` | 6 |
| `FESTIVAL_SHORTAGE` and the festival question (the expectation of `festival_rush`) | 6 |
| Admin panel | 7 |
| Audit viewer and sealing, release hardening | 8 |

**Constraint: the demo is the real product.** No demo path writes to PalletWise tables. Every change comes from the simulator through the outbox and the connector, exactly as from a real WMS; every click goes through the normal API, OPA, roles and audit. Reset is the only way back, and it rebuilds from the seed through the same ingestion; data is never edited by hand.

## 2. Decisions

| # | Decision | Why |
|---|---|---|
| D1 | The simulator owns time. A time shift is a command from the browser through the API (OPA, audit, CSRF) to the simulator's internal control endpoint; the simulator generates and emits the period; PalletWise's clock moves only when it ingests the tick markers | Simulator decision 10 and FR-SIM-01: the simulator is a data source like any WMS; one clock, driven by ingested data |
| D2 | Time is paused unless shifted. Play, pause and the 1x, 10x and 60x speeds are not built now | The product owner demos by clicking through flows; a running clock adds nothing to that and would change the screen under the presenter |
| D3 | Product owner revision of FR-SIM-04 and the identity scope table: a new scope `simulation.control` (time shift, run a scenario step) for the Manager and the Admin, refused by the API unless `site_clock.mode = SIMULATED`; reset keeps `admin.simulation.control` (Admin only) because it discards the run | The demo runs from one Manager screen with no switching of accounts; shifting simulated time changes only simulated data, and a real site has no simulator to control |
| D4 | After a simulator restart, its state is rebuilt from the seed, the input log and the last emitted time | A restart never moves time or emits an event twice |
| D5 | A time shift is applied in batches; the site stays readable meanwhile and the screen shows "moving time to (date)" until the clock reaches the target | A week is thousands of records; the presenter sees progress, never half a state presented as current |
| D6 | The guided demo is a storyline file (section 2.1), shown in a step panel opened by a **Demo** button for holders of `simulation.control`. A step's setup runs only when the presenter clicks it, and it is confirmed and audited like any action. Later slices add their steps to the same file | One place shows every feature; it grows with the product |
| D7 | All four scenario files are written now; expectations that need a later feature (task execution, `FESTIVAL_SHORTAGE`) are checked in Slice 6, never faked | The demo can stage every story now |
| D8 | The bell shows a dot when exceptions opened since the viewer last opened the inbox; "last seen" is kept per viewer in the browser | UX spec row 27; a per-viewer convenience. The review's server-side marker per user was not adopted: it only helps one user on several devices, which a demo does not have, and would add a table, a write path with its own role and an API for a cosmetic dot; it can be added if real use needs it |
| D9 | One operation at a time per site: a time shift, a scenario step or a reset holds the simulator's lock for the site; a second request waits briefly, then is refused as busy. The same `Idempotency-Key` never applies an operation twice. Screens and the copilot always show a complete earlier moment during a shift, because the clock moves only with ingested tick markers, which follow every event before them | Product owner review: no shift, scenario or reset can interleave with another, and nothing partial is presented as current |

### 2.1 The guided demo storyline (first version)

| Step | Setup the step runs (when clicked) | What the presenter shows |
|---|---|---|
| 1. Start of shift | (reset is a separate Admin action) | The daily brief; the inbox ranked with "why it is ranked here"; the map highlights |
| 2. Find anything | none | Search a SKU, a bin and an order; the copilot answers with the map |
| 3. A big outlet order arrives | Scenario event: a large order due this afternoon (`outlet_window_at_risk`) | The stockout risk appears in the inbox and on the map without a reload |
| 4. Investigate | none | Ask the copilot about the exception; the order's status; acknowledge it |
| 5. Time passes | Time shift +4 hours | Picks and shipments happened; the order is late; exceptions change and resolve |
| 6. Next morning | Time shift to the next operating day 06:00 | A new daily brief; yesterday's throughput against the average |
| 7. The festival approaches | Time shift +1 week | Demand ramps; low stock rises; days of cover fall |
| 8. Ask anything | none | Flexible questions through the query language; an honest decline when out of scope |

## 3. Acceptance criteria by step

### Step 1: Time shift in the simulator

- The simulator continues the run past the clock start, day by day, with the history generator (schedules generated at the start of each simulated day); a shift emits all events up to the target, with tick markers, in batches.
- Forward only and within the calendar (FR-SIM-05); refused with a message otherwise.
- Tests (simulator 9): a shift to T gives the same state as shifting in smaller steps to T; tick markers move `site_clock` only when applied, never ahead of applied events; the same seed and input log give the same state; demand is 0 on non-operating days; old-run records are skipped.

### Step 2: Controls through the API

- `POST /api/sites/{id}/simulation/shift` (by a duration or to a time) and `POST .../simulation/scenario` with `simulation.control`; `POST .../simulation/reset` with `admin.simulation.control`; `GET .../simulation` (state, target, progress) for every reader of the site. OPA, audit with before and after, CSRF, `Idempotency-Key`.
- Refused on a site whose clock mode is not `SIMULATED`.
- Tests: the Manager may shift and run a scenario but not reset; any control on a real site is refused; a repeated request with the same key shifts once; backward and beyond-calendar shifts are refused; a scenario or reset while a shift runs is refused as busy (D9).

### Step 3: Scenarios

- Scenario files in `simulator/scenarios/`, the closed list of event types, preconditions checked first (refused with the reason), events applied from the current simulated time and recorded in the input log.
- Expectations run by the evaluation suite: `dead_stock_in_prime` (the six anchors), `outlet_window_at_risk` (a fixable `STOCKOUT_RISK` for SKU-214 within 90 simulated minutes), `monsoon_slowdown` (rising `LOW_STOCK`).
- Tests: preconditions refuse; the same seed and input log reproduce the state.

### Step 4: Reset

- Exactly as simulator 7.1: site `REBUILDING`, new run from the seed through the normal ingestion, dataset hash checked, then `READY`; a failure leaves `FAILED`, never `READY`. Open exceptions resolve as `SIMULATION_RESET`; the day gets a new brief.
- Tests: a failure during regeneration leaves `FAILED`; old-run records are skipped; the screen shows "data is being rebuilt" and recovers without a reload.

### Step 5: Screens

- Time shift bar for holders of `simulation.control` on a simulated site: the site time, the shift buttons, a date picker, progress while shifting; reset for the Admin only. Every action confirmed.
- Guided demo panel: steps of section 2.1 in order, Next and Back, each step's setup on click, its prompts as suggestion chips that run through the copilot.
- Notification bell; the 3D scene renders on demand; the zone label no longer overlaps.
- Tests: the Manager sees the bar and the demo without reset; the Admin sees reset; confirmations; the bell's dot appears for a new exception and clears when seen; no em or en dashes.

### Step 6: Quality

- Performance on the reference site, limits agreed before measuring (product owner review): a 1-day shift fully applied, with the screens updated, in at most 30 seconds; a 1-week shift in at most 3 minutes; an applied change reaches the screen within 2 seconds (NFR-PERF-05). Total duration and screen latency are recorded per shift; throughput, ingestion lag and recalculation time are broken down only if a limit is missed. The continuous-clock 60x test is not required (D2).
- Evals: the scenario expectations of step 3 pass; the existing gates stay green.
- Live browser check: the whole storyline of section 2.1, clicked through end to end, then reset and run again; evidence file like Slice 3's.

## 4. Definition of done

- Every step's criteria pass; engineering baseline 8.1 is met.
- Unit and integration tests, the deterministic eval gate, lint and CI pass.
- The storyline runs end to end twice (before and after a reset) with the same results.
- Code review and security review at the end of the slice; findings fixed or recorded.

## 5. Change log

| Date | Change |
|---|---|
| 2026-11-21 | Draft for product owner approval |
| 2026-11-21 | Revised after the product owner's direction: a clickable guided demo with time shift instead of a running clock (D2, D5, D6, 2.1); the Manager controls the simulation on simulated sites, reset stays Admin (D3); the demo is the real product, with no demo-only write paths |
| 2026-11-21 | Approved after the product owner's review: D9 (one operation at a time per site, idempotent, nothing partial shown) and fixed shift limits (1 day within 30 s, 1 week within 3 min, 2 s to screen); D8 kept per browser, with reason |
