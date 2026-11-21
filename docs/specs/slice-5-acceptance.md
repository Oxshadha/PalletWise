# Slice 5: Acceptance Criteria

**Status:** Approved; closed 2026-11-21 with the tier off (model gate failed, see docs/quality/slice-5-evidence.md)
**Goal:** The copilot answers ad-hoc questions about the warehouse data that its tools and query language cannot express, with a governed, read-only SQL tier used only as a last resort (ADR 0002, accepted). Security and correctness are equal: nothing outside the governed views is reachable, and a query that would compute a wrong number is refused, never answered.

This document is a plan against the approved specifications and ADR 0002. It does not redefine them. Where a decision below refines a spec, the spec gets a change-log entry when the work lands.

| Spec | What Slice 5 implements from it |
|---|---|
| ADR 0002 (accepted) | The whole decision: guard, governed views with declared semantics, correctness rules, runner limits, labelled answers, traceability to the agent's identity, the gate and the configuration fingerprint |
| Requirements | FR-COP-02 (six answer layers) and FR-COP-03 (the governed exception), revised as ADR 0002 section 8 says |
| Engineering baseline | Rules 3 and 4 revised as ADR 0002 section 8; rule 5 (every tool through `tools.executor`) and rule 7 (own database role) unchanged |
| Agent and tools spec | A new tool, its place in the paths (3.2), guardrails (6), the result envelope's `source`, audit fields (13) |
| Identity and access | A new scope for the tool (D4); the Admin switch under `admin.agents.manage` |
| Evals and observability | New case sets and gates (step 5) |

## 1. Scope

**In scope**

- `gq` governed views and their semantics catalog; role `pw_query_read`.
- The guard (tree allowlist and correctness rules), the runner, the tool `run_governed_query` in the registry.
- Copilot integration: last-resort routing, the answer card label, fallbacks.
- Traceability to the agent's identity: audit, database tagging, trace spans.
- The configuration fingerprint and the gate that enables the tier; the Admin switch (API now; screen in Slice 7).
- Evaluation sets and the model gate for the tier; one storyline step that shows it.

**Out of scope**

| Item | Slice |
|---|---|
| Window functions, `WITH`, `FROM` subqueries, many-to-many joins (each a later widening with its own rule and gate) | later |
| The admin panel screen for the switch and for reading governed queries | 7 |
| Proposals and task execution | 6 |

## 2. Decisions

| # | Decision | Why |
|---|---|---|
| D1 | `pglast` 7 (libpg_query for PostgreSQL 17), pinned; a test parses a fixed set of statements and fails if the parser's major version differs from the server's | ADR 0002 section 2: the parser must match the server |
| D2 | The semantics catalog is a YAML file beside the semantic model (`semantic/gq_catalog.yaml`): per view its grain, primary key, foreign keys with cardinality, and per column its kind (key, attribute, measure), unit and allowed aggregates. A test checks the catalog against the live view definitions (every column described, no column missing) | One source for the guard, the model's schema description and the tests |
| D3 | The passed configurations are a committed file (`semantic/governed_gate.json`): each entry is a fingerprint and the gate report it came from. The model gate writes the entry when it passes; a person reviews and commits it, like the golden files. At start and on every call the API computes the running fingerprint; the tier runs only when it is listed | ADR 0002 section 7, without a write path from the eval environment into the product database |
| D4 | A new scope `query.governed` for the tool, granted to the Manager and the Admin roles and in the copilot's allowed scopes | The tool is a different kind of data access; the policy can withdraw it by role without touching other tools |
| D5 | The switch: a column on the agent registry, `governed_query_enabled`, default true; `PUT /api/admin/agents/copilot/governed-query` with `admin.agents.manage`, CSRF, `Idempotency-Key`, audited with before and after | ADR 0002 section 7 and the product owner's "on by default, Admin can switch it off" |
| D6 | When the guard refuses a query, the model gets the reason and may try once more in the same run; a second refusal ends in an honest decline that names what could not be computed | ADR 0002 section 4: never an approximate number |
| D7 | The first catalog's measure rules: stock on hand, available and allocated (units, additive); inventory value (LKR, additive); days of cover and utilization percent (non-additive); order line quantities ordered, allocated, picked and shipped (units, additive at line grain); movement quantity (units, additive) | ADR 0002 section 3 examples, made exact |

## 3. Acceptance criteria by step

### Step 1: Governed views, catalog and role

- Migration: schema `gq`, views for products, locations, stock, movements, orders, order lines, allocations, exceptions, calendar and `site_now`; owner-rights views over forced RLS, each filtered by `current_setting('app.site_id')`; customers by code and name only.
- Role `pw_query_read`: `USAGE` on `gq`, `SELECT` on its views, nothing else; no `CREATE` anywhere, no `TEMPORARY`, `search_path = pg_catalog, gq`, no `BYPASSRLS`.
- The catalog file (D2) for every view.
- Tests: another tenant's and another site's rows are invisible; a missing tenant or site setting errors; the role cannot read any `pw` or `sem` relation, call any `pw` or `sem` function, create anything or bypass RLS (asserted from the catalog tables, so a later grant fails the test); the catalog matches the views.

### Step 2: The guard

- Parse (D1), walk the whole tree, allow only ADR 0002 section 2's constructs; then the section 4 correctness rules against the catalog; deparse the accepted tree.
- Every refusal names its rule in words the model and the Admin can read.
- Deterministic tests, no model and no database: an adversarial set (writes, locks, catalogs, `pw` and `sem` names, `set_config` and `SET`, sleeps, file and large-object functions, stacked statements, comments, unlisted casts and functions, `WITH`, window functions, `FROM` subqueries) is 100 percent refused; a semantically invalid set (sum or average of days of cover, order values over order lines, units mixed with LKR, a join to a non-key, a many-to-many path, a column no view defines) is 100 percent refused; a valid set (including the ADR's three example questions) is accepted and deparses stably.

### Step 3: Runner, tool and traceability

- The runner (ADR 0002 section 5): own pool of at most 2 connections as `pw_query_read`; tenant and site from the session; read-only transaction; 2 s statement timeout; `work_mem` cap; 200 rows, 20 columns, 256 KB; `EXPLAIN` cost refusal; cancellation and connection reset on timeout.
- `run_governed_query(sql, purpose)` in the registry under `query.governed` (D4), through `tools.executor` (policy, kill switch, the switch D5 and the fingerprint D3 rechecked per call, audit, tracing).
- Traceability (ADR 0002 section 6a): the audit record carries actor type `AGENT`, agent id and version, model, prompt version, run id, user and role, scope, decision, plus the normalized SQL, hash, guard verdict and rule, rows, bytes, truncation, duration and fingerprint. The query's transaction carries `application_name` and settings with the agent id and run id. Guard, runner and database call are spans of the run's trace.
- The switch endpoint (D5).
- Tests: limits each enforced (a slow query is cancelled within 2.5 s; 201 rows report truncation; a wide row set stops at 256 KB); a query naming another site's id returns nothing of it; the tool is refused when switched off, when the fingerprint is not listed, when the agent is suspended and when the user lacks the scope; the audit record and the database tag name the agent and run; a copilot run without delegation cannot call the tool.

### Step 4: Copilot integration

- The tool description and the catalog as the model's schema description; the prompt says the tool is the last resort after tools and the query language; D6 retry and decline.
- Result envelope `source: governed_query`; the answer card says "Answered with a custom query", with rows, data time and truncation; no SQL shown to users.
- Grounding, entity and dash checks unchanged; on failure the rows are shown as a plain table.
- Highlights only from rows carrying a bin, zone or product id.
- Tests: a governed answer renders with the label and without SQL; a failed grounding shows the table; the existing paths are unchanged (deterministic gate stays 104 of 104).

### Step 5: Evaluation and the gate

- New case sets: governed-only questions with reference SQL written independently (at least 30), routing cases that tools or the query language answer (at least 30, from the existing sets), semantically invalid questions (at least 15), adversarial prompts that try to make the model write harmful SQL (at least 15).
- The model gate reports ADR 0002 section 7's gates and ADR 0001's latency and cost gate on the same run, with the fingerprint; when every gate passes it writes the entry for D3.
- Product owner reviews the report before the entry is committed.

### Step 6: Quality

- Storyline: step 8 ("Ask anything") gains one governed question and one that is refused for correctness; the storyline test runs twice as in Slice 4.
- Live browser check of a governed answer and a refusal; the Admin switch through the API turns the tier off and on.
- Evidence file like Slice 4's; code review and security review at the end of the slice.

## 4. Definition of done

- Every step's criteria pass; engineering baseline 8.1 is met.
- Unit and integration tests, the deterministic eval gate, lint and CI pass.
- The model gate passes for the committed fingerprint, and the running stack's fingerprint is listed.
- Code review and security review at the end of the slice; findings fixed or recorded.

## 5. Change log

| Date | Change |
|---|---|
| 2026-11-21 | Draft for approval, from ADR 0002 (accepted) |
| 2026-11-21 | Approved by the product owner |
| 2026-11-21 | Closed: steps 1 to 4 met; the model gate failed four times (best governed.correct 0.73 against 0.90), so the tier stays off and step 6's governed storyline step waits for a pass (product owner decision) |
