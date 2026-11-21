# Slice 3: Acceptance Criteria

**Status:** Approved by the product owner on 2026-11-20 with revisions (D1 and D3 revised and confirmed; lifecycle and event safeguards added)
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

## 2. Decisions

| # | Decision | Why |
|---|---|---|
| D1 | The site live stream (agent spec 9.2) is built in this slice, with the safeguards of section 2.1 | FR-EXC-03 needs exceptions to update and resolve live; the same stream gives FR-MAP-08 (map updates live), which no slice has built yet |
| D2 | The brief is stored in a new table `pw.daily_brief`, written by a new role `pw_brief`, one brief per Manager, site and site-local business date (section 2.2) | FR-BRF-03 says once per Manager per day; AGENTS.md rule 7: a new write path gets its own role, so `pw_memory` and `pw_cache` are not widened |
| D3 | The engine evaluates a site when its evaluation key changes, one evaluation per site at a time, in one transaction (section 2.3) | Evaluates only new states, never unchanged data twice, and consumes normalized state changes the same way for a real connector later |
| D4 | "Inbox" is a third item in the pill navigation, beside Warehouse and Copilot, with the map on the same screen. Warehouse and Copilot stay as they are; the inbox is a list beside the map, not a dashboard | Personas: start of shift is "inbox ranked by impact, map highlights" |
| D5 | The copilot reads open exceptions through a read tool `get_open_exceptions` (top N by score, optional type) over the **same persisted records** as the inbox | "What needs my attention?" and "ask the copilot about this exception" answer from what the inbox shows; no second calculation of exceptions |
| D6 | Four brief items until proposals exist: top three exceptions, orders at risk today, yesterday's throughput against the 7-operating-day average, data freshness. Selection and ranking are deterministic and each item carries why it was selected (for exceptions: its score and rank). The proposals item arrives in Slice 5 | FR-BRF-01 lists five; one depends on Slice 5. The model only words the items; it never selects or ranks them |
| D7 | Without a model (agent suspended, model unavailable, budget spent) the brief shows the same items with template wording | FR-ADM-03 |
| D8 | Search is a read endpoint, not a copilot tool: `GET /sites/{id}/search?q=` through OPA as `pw_agent_read`. Exact identifiers (SKU, bin code, order number) rank first, then prefix matches, then product-name matches; at most 8 results. Choosing a result runs the matching fast-path question | Finding a record never costs a model call or a copilot run |

### 2.1 Live stream safeguards (D1)

Hints stay invalidation signals, never data (agent spec 9.2): the screen always shows the snapshot endpoints, so a hint can be lost, repeated or late without showing anything wrong, provided the client notices and refetches. The durable source is a **per-site change sequence** in the database, not the notifications:

1. **Durable sequence.** Every change that a screen shows (inventory load, exception evaluation, clock step, data status) increments the site's change sequence and records the changed kinds **in the same transaction** as the change. `NOTIFY` only wakes the API; PostgreSQL delivers it only after commit, so no hint describes uncommitted data.
2. **Reconnect and replay.** SSE events carry the sequence as their event ID. On connect or reconnect (`Last-Event-ID`) the API sends the current sequence and the kinds changed since the client's ID; if that history is no longer available, it says "refetch all". A heartbeat every 15 seconds carries the current sequence, so a client detects a lost hint even when no later change happens.
3. **Authorization.** Every stream connection is authenticated by the session and authorized by OPA for the tenant and site, like the map reads; a revoked session or role ends the stream.
4. **Duplicates and order.** The client ignores events at or below the sequence it has applied and refetches on any gap.
5. **Coalescing.** The API coalesces hints per connection and the client batches for about 500 ms, so a burst of changes leads to one refetch per snapshot.
6. **Measured.** The 2-second target (NFR-PERF-05) is measured from the committing transaction to the visible change in the browser, in the live browser check.

A full event outbox with replayable payloads is not built: payload-free hints recovered by refetch need only the durable sequence, and agent spec 9.2 (approved) records that no outbox is needed in v1.

### 2.2 Brief storage (D2)

`pw.daily_brief`: tenant, site, owner subject, business date (site-local), status (`GENERATING`, `READY`, `FAILED`), items (the deterministic facts, rankings and selection reasons), wording, wording source (`model` or `template`), data watermark and `as_of`, settings version, prompt version, generated at. Forced RLS by tenant and owner. Unique `(tenant, site, owner, business_date)`: concurrent first visits create one brief (insert on conflict, the loser waits for or reads the winner). A refresh returns the stored brief unchanged. Generated per Manager, as FR-BRF-03 states; the items are site facts and identical for every Manager of a site on a given data watermark.

### 2.3 Engine scheduling (D3)

- **Evaluation key:** `site_id + data watermark + settings version + clock boundary` (the simulated clock floored to 15 minutes). The clock is part of the key because time-based rules (delayed orders, hours until due) change as the clock moves even when the data does not.
- **Triggers:** the data watermark advances (a load is applied), the clock crosses a 15-minute boundary, a load completes or the simulation resets, or the settings version changes. The worker `LISTEN`s for wake-ups and reconciles every 5 seconds by comparing each `READY` site's current key with the last evaluated one, so a missed wake-up costs at most 5 seconds.
- **One at a time:** a per-site transaction-level advisory lock; a second trigger for the same site waits or is skipped when the key is already evaluated.
- **Idempotent:** the last evaluated key is stored per site; evaluating the same key again writes nothing.
- **Atomic:** one evaluation of one site is one transaction (records, links, evaluations, change sequence); readers never see a partial result, and the hint is delivered after commit.
- **Retry:** a failed evaluation rolls back, is logged and counted, and is retried with backoff on the next reconciliation; the previous committed state stays visible meanwhile.

### 2.4 Exception lifecycle

1. **Acknowledged is not resolved.** Acknowledging changes the status to `ACKNOWLEDGED`; the exception stays in the inbox, keeps its score and is re-evaluated like any open one.
2. **Resolution needs evidence.** A record resolves only when an evaluation recalculates its rule and the subject is no longer in the result (`CONDITION_CLEARED`), or on a simulation reset (`SIMULATION_RESET`). Acknowledgement, a copilot answer or a proposal never resolves it (FR-PRP-13).
3. **Reopening is a new occurrence.** If the condition returns after resolution, a new record opens for the same subject (the unique index allows one non-resolved record per subject); the earlier occurrence stays in history. An acknowledgement does not carry over.
4. **Explainable ranking.** Every evaluation stores the three normalized components, caps, weights and settings version, not only the score (semantic 6.1).
5. **No duplicates.** One non-resolved record per site, type and subject, enforced by the database.
6. **History.** `exception_evaluation` gains a `status` and an `event` (`OPENED`, `RESCORED`, `ACKNOWLEDGED`, `RESOLVED`), so one table shows when an exception appeared, changed, was acknowledged and resolved; acknowledgements are also in the audit log. CWM 9.2 gets a change-log entry for these two columns.

## 3. Acceptance criteria by step

### Step 1: Exception tables and engine

- Tables of CWM 9.2 (`exception_record`, `exception_bin`, `exception_order`, `exception_evaluation`) with forced RLS by tenant, composite site keys, and the unique partial index (one non-resolved record per site, type and subject).
- Role `pw_evaluator`: `EXECUTE` on `sem`, write access to the exception tables only. `pw_agent_read` gains read access through `sem` views, never write.
- The engine reuses the Slice 2 rule functions; it adds no second implementation of any rule. Each type maps to its subject key, bins and orders as semantic 6 states.
- Impact score exactly as semantic 6.1, with caps and weights from the settings version; components, caps, weights and settings version stored on each evaluation.
- Lifecycle exactly as section 2.4; scheduling exactly as section 2.3.
- Runs only while the site is `READY`; uses the site clock, never the system clock.
- Tests: the 6.1 fixture (score 57.2); one triggering fixture per type and its clearing; acknowledged stays listed; a returning condition opens a new occurrence; the same key evaluated twice writes nothing; two concurrent triggers run one evaluation; a failing evaluation leaves the previous state and is retried; RLS isolation; the role cannot write anything else.

### Step 2: Site live stream

- `GET /sites/{id}/events` (SSE) with the safeguards of section 2.1; hints carry the site's change sequence as the event ID and the kinds that changed (`inventory`, `exceptions`, `clock`, `data_status`).
- Tests: a hint reaches a connected client only after commit (a rolled-back change sends nothing); reconnect with `Last-Event-ID` returns the kinds missed; a lost notification is detected by the heartbeat and leads to a refetch; duplicate and out-of-order events are ignored; a burst of 100 changes leads to one refetch; a user never receives hints for a site they cannot read; change to screen within 2 seconds (NFR-PERF-05).

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

- `GET /sites/{id}/brief` returns the Manager's brief for the site-local day, generating it on the first request of the day and reusing it after (FR-BRF-03: a second visit makes no model call), stored per section 2.2.
- Items per D6, numbers from the semantic layer (4.6 throughput, open exceptions, orders at risk today); Flash-Lite writes at most one sentence per item, checked for grounding and key facts like path A; template wording otherwise (D7).
- Each item links to its detail: exceptions to the inbox, orders at risk to the copilot's fast-path answer, each highlighting its locations (FR-BRF-02).
- Refuses with the "data is loading" status while the site is not `READY`.
- Tests: two requests on one day, one model call; two concurrent first requests, one brief; a new site-local day makes a new brief; budget spent or agent suspended gives the template brief; every number in the wording appears in the items.

### Step 6: Search

- Command box from the shell's search icon; results as you type (debounced) from `GET /sites/{id}/search` per D8.
- Choosing a SKU runs "Where is SKU-x stored?", a bin "What is in bin x?", an order its delayed-order or order answer; each through the fast path and `tools.executor`.
- Tests: an exact identifier ranks above prefix and name matches; prefix and case-insensitive matches; at most 8 results; no results says so; another site's data never appears.

### Step 7: Copilot and quality

- `get_open_exceptions` (D5) registered with its OPA scope and Rego tests; fast-path routes for "What needs my attention?" and similar fixed phrases.
- Eval cases: exception questions, brief wording (facts gate), and the consistency check that a product's exception, its tool answer and its map state agree for one `as_of` and settings version.
- Performance: one engine evaluation of the reference site within its budget at 180 days of data; inbox and brief endpoints within the read budgets.

## 4. Definition of done

- Every step's criteria pass; engineering baseline 8.1 is met.
- Unit and integration tests, the deterministic eval gate, lint and CI pass.
- A live browser check on the reference site: the inbox ranks and explains, acknowledgement works, a data change shows on the map and inbox within 2 seconds, the brief appears once per day, search finds SKUs, bins and orders.
- The browser check also covers (product owner review of step 4): choosing exception A then B shows only B; Ask the copilot hands the map to the new answer when its result arrives; an exception resolving while chosen clears its layer; switching Warehouse, Inbox and Copilot while an answer streams loses nothing; 1920 by 1080 and 1366 by 768; keyboard and touch. Step 7: `get_open_exceptions` checks that a page-context exception belongs to the caller's tenant and site, and never trusts the id.
- Code review and security review at the end of the slice; findings fixed or recorded.

## 5. Change log

| Date | Change |
|---|---|
| 2026-11-20 | Draft for product owner approval |
| 2026-11-20 | Product owner review: D1 approved with stream safeguards (2.1; durable per-site change sequence in place of a payload outbox, consistent with agent spec 9.2); D2 approved with fields and uniqueness (2.2), per Manager; D3 revised (2.3; the clock boundary added to the evaluation key); D4 approved, Warehouse and Copilot unchanged; D5 to D8 approved with details (same persisted records, deterministic brief selection with reasons, exact identifiers first); exception lifecycle added (2.4) |
| 2026-11-20 | Product owner confirmed the D1 and D3 revisions; approved |
