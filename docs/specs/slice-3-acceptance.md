# Slice 3: Acceptance Criteria

**Status:** Draft for product owner approval
**Goal:** PalletWise tells the Warehouse Manager what needs attention before they ask: a ranked exceptions inbox that updates live, a short daily brief on the first visit of the day, and a search box that finds any SKU, bin or order in one step (vision principle 4, "tell, do not make them ask").

This document is a plan against the approved specifications. It does not redefine them. Where a decision below refines a spec, the spec gets a change-log entry when the work lands; the spec stays the source of truth.

| Spec | What Slice 3 implements from it |
|---|---|
| Requirements (approved) | FR-EXC-01 to 04, FR-EXC-05 (map and copilot parts), FR-BRF-01 to 03, FR-MAP-08, NFR-PERF-05 |
| Semantic layer and query DSL (approved) | Section 6 exception rules (all types but `FESTIVAL_SHORTAGE`), 6.1 impact score, 4.6 throughput |
| Canonical warehouse model (approved) | 9.2 exception tables, 11.3 `pw_evaluator` role, `acknowledge_exception()` for `pw_app` |
| Agent, tools and API (approved) | 9.2 site live stream, `GET /sites/{id}/exceptions`, `POST .../acknowledge`, `GET /sites/{id}/brief`, 10 visualization contract |
| UX screens (approved) | Search command box (row 26), inbox and brief sections added when this slice starts |
| Evals and observability (approved) | Exception fixtures, brief eval cases, audit of acknowledgements |

## 1. Scope

**In scope**

- Exception engine in the `worker-evaluator` process: six types (`STOCKOUT_RISK`, `DELAYED_ORDER`, `LOW_STOCK`, `DEAD_STOCK`, `SLOW_IN_PRIME`, `CAPACITY_HOTSPOT`), impact score, automatic resolution, evaluation history.
- Exceptions inbox: ranked list, "why it is ranked here", acknowledge, open on map, ask the copilot about it.
- Site live stream (invalidation hints over SSE): map, inbox and brief refresh within 2 seconds of a change.
- Daily brief: once per Manager per site-local day, at most five items, numbers from code, wording by Flash-Lite.
- Search command box: SKUs, bins and orders.

**Out of scope (later slices)**

| Item | Slice |
|---|---|
| Notification bell | 4 |
| Proposals, "draft a proposal" from an exception (FR-EXC-05 second action), the brief's proposals item | 5 |
| `FESTIVAL_SHORTAGE` exceptions and the festival question | 5 |
| Admin settings editor (ranking weights and caps keep their seeded defaults) | 6 |
| Audit viewer and sealing | 7 |

**Constraint.** Every number in the inbox and the brief comes from the semantic layer. The model writes only the brief's wording, grounded like any answer; nothing in this slice lets the model calculate.

## 2. Decisions for the product owner

Each has a recommendation. The ones marked **open** need your answer before step 1 starts.

| # | Decision | Recommendation | Why |
|---|---|---|---|
| D1 (open) | The site live stream (agent spec 9.2) is built in this slice | Yes | FR-EXC-03 needs exceptions to update and resolve live; the same stream gives FR-MAP-08 (map updates live), which no slice has built yet |
| D2 (open) | Where the brief is stored | New table `pw.daily_brief` (tenant, site, owner subject, site-local day, items, wording, settings version, generated at), forced RLS by tenant and owner, written by a new role `pw_brief` | FR-BRF-03 needs one brief per Manager per day; CWM has no table for it. AGENTS.md rule 7: a new write path gets its own role, so `pw_memory` and `pw_cache` are not widened |
| D3 (open) | How the engine knows when to run | It polls each `READY` site every 5 seconds and evaluates when the simulated clock has moved 15 minutes or more, or when a data load completed since its last run (debounced) | The simulator advances the clock in its own process; polling a watermark needs no new channel between processes and works the same for a real connector later |
| D4 (open) | Where the inbox lives in the UI | A third item in the pill navigation, "Inbox", beside Warehouse and Copilot, with the map on the same screen; selecting an exception highlights it | Personas: start of shift is "inbox ranked by impact, map highlights". The UX spec leaves the inbox section to this slice |
| D5 | The copilot can read open exceptions through a new read tool `get_open_exceptions` (top N by score, optional type) | Yes | "What needs my attention?" and "ask the copilot about this exception" then answer from the same records the inbox shows, through `tools.executor`, never a second calculation |
| D6 | Brief items before proposals exist | Four items: top three exceptions, orders at risk today, yesterday's throughput against the 7-operating-day average, data freshness. The proposals item appears in Slice 5 | FR-BRF-01 lists five; one depends on Slice 5 |
| D7 | Brief without a model (suspended agent, model unavailable, budget spent) | The same items with template wording; the numbers never wait for the model | FR-ADM-03: "the brief shown without generated wording" |
| D8 | Search is a read endpoint, not a copilot tool | `GET /sites/{id}/search?q=` through OPA as `pw_agent_read` (prefix match on SKU, product name, bin code, order number; at most 8 results); choosing a result runs the matching fast-path question | Instant results while typing must not cost a model call or a copilot run |

## 3. Acceptance criteria by step

### Step 1: Exception tables and engine

- Tables of CWM 9.2 (`exception_record`, `exception_bin`, `exception_order`, `exception_evaluation`) with forced RLS by tenant, composite site keys, and the unique partial index (one non-resolved record per site, type and subject).
- Role `pw_evaluator`: `EXECUTE` on `sem`, write access to the exception tables only. `pw_agent_read` gains read access through `sem` views, never write.
- The engine reuses the Slice 2 rule functions; it adds no second implementation of any rule. Each type maps to its subject key, bins and orders as semantic 6 states.
- Impact score exactly as semantic 6.1, with caps and weights from the settings version; components, caps, weights and settings version stored on each evaluation.
- Opens new records, updates changed ones (new evaluation row only when score or status changes), resolves cleared ones with `CONDITION_CLEARED`; a simulation reset resolves open records with `SIMULATION_RESET`.
- Runs only while the site is `READY`; uses the site clock, never the system clock.
- Tests: the 6.1 fixture (score 57.2); one triggering fixture per type and its clearing; idempotent re-evaluation (no duplicate records or evaluations); RLS isolation; the role cannot write anything else.

### Step 2: Site live stream

- `GET /sites/{id}/events` (SSE), authorized like the map reads; hints carry a per-site sequence number and the kinds that changed (`inventory`, `exceptions`, `clock`, `data_status`).
- Database changes `NOTIFY` a per-site channel; each API instance `LISTEN`s and forwards. A gap in sequence numbers or a reconnect makes the client refetch its snapshots; hints are batched for about 500 ms.
- Tests: a hint reaches a connected client; a missed hint leads to a refetch, never a stale screen; a user never receives hints for a site they cannot read; end-to-end change to screen within 2 seconds (NFR-PERF-05).

### Step 3: Exceptions API

- `GET /sites/{id}/exceptions`: open and acknowledged records ranked by score, with type, subject, score components, highlights (agent spec 10 severities), affected orders and first detected time.
- `POST /sites/{id}/exceptions/{exception_id}/acknowledge` with `Idempotency-Key`, Manager only, through `acknowledge_exception()` as `pw_app`; recorded with user and time, audited (FR-EXC-04).
- Tests: Admin reads but cannot acknowledge; repeating the request changes nothing; another tenant's or site's record is not found.

### Step 4: Inbox and map

- Inbox list ranked by score, each row with the type, the subject in words, the score and "why it is ranked here" (the three components and weights).
- Selecting an exception highlights its bins and zones on the map with the reason and severity of agent spec 10; "Ask the copilot" opens the copilot with the exception as page context.
- The inbox, the map states and answer cards show one severity per exception.
- Live: a new exception appears, and a cleared one leaves, without a reload.
- Tests: highlights equal the record's bins (FR-MAP-04); no em or en dashes; keyboard path through the list.

### Step 5: Daily brief

- `GET /sites/{id}/brief` returns the Manager's brief for the site-local day, generating it on the first request of the day and reusing it after (FR-BRF-03: a second visit makes no model call).
- Items per D6, numbers from the semantic layer (4.6 throughput, open exceptions, orders at risk today); Flash-Lite writes at most one sentence per item, checked for grounding and key facts like path A; template wording otherwise (D7).
- Each item links to its detail: exceptions to the inbox, orders at risk to the copilot's fast-path answer, each highlighting its locations (FR-BRF-02).
- Refuses with the "data is loading" status while the site is not `READY`.
- Tests: two requests on one day, one model call; a new site-local day makes a new brief; budget spent or agent suspended gives the template brief; every number in the wording appears in the items.

### Step 6: Search

- Command box from the shell's search icon; results as you type (debounced) from `GET /sites/{id}/search` per D8.
- Choosing a SKU runs "Where is SKU-x stored?", a bin "What is in bin x?", an order its delayed-order or order answer; each through the fast path and `tools.executor`.
- Tests: prefix and case-insensitive matches; at most 8 results; no results says so; another site's data never appears.

### Step 7: Copilot and quality

- `get_open_exceptions` (D5) registered with its OPA scope and Rego tests; fast-path routes for "What needs my attention?" and similar fixed phrases.
- Eval cases: exception questions, brief wording (facts gate), and the consistency check that a product's exception, its tool answer and its map state agree for one `as_of` and settings version.
- Performance: one engine evaluation of the reference site within its budget at 180 days of data; inbox and brief endpoints within the read budgets.

## 4. Definition of done

- Every step's criteria pass; engineering baseline 8.1 is met.
- Unit and integration tests, the deterministic eval gate, lint and CI pass.
- A live browser check on the reference site: the inbox ranks and explains, acknowledgement works, a data change shows on the map and inbox within 2 seconds, the brief appears once per day, search finds SKUs, bins and orders.
- Code review and security review at the end of the slice; findings fixed or recorded.

## 5. Change log

| Date | Change |
|---|---|
| 2026-11-20 | Draft for product owner approval |
