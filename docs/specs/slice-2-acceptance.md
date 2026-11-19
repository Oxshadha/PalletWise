# Slice 2: Acceptance Criteria

**Status:** Draft for product owner approval
**Goal:** PalletWise answers the remaining operational questions with reusable semantic tools and with user-defined combinations of approved measures and dimensions, safely, with results on the map.

This document is a plan against the approved specifications. It does not redefine them. Where a decision below refines a spec, the spec gets a change-log entry when the work lands; the spec stays the source of truth.

| Spec | What Slice 2 implements from it |
|---|---|
| Semantic layer and query DSL (approved) | Section 6 rules, 7 query DSL, 7.5 working set, 7.6 highlights, 8 fixed tools |
| Canonical warehouse model (approved) | 9.1 `settings_version` |
| Agent, tools and API (approved) | Tool registry growth, 14b answer-wording cache, deferred input classifier |
| Evals and observability (approved) | Facts gate, adversarial cases, new eval cases |
| UX screens (approved) | Map states, answer cards for new result types |

## 1. Scope

**In scope**

- Fixed tools over approved rules: `get_low_stock`, `get_top_movers`, `get_dead_stock`, `get_zone_summary`, `get_capacity_hotspots`, `get_delayed_orders`, `get_order_stockout_risks`, `get_slow_in_prime`.
- `query_inventory`: the query DSL exactly as semantic spec 7 defines it.
- Follow-up questions through the working set (semantic spec 7.5).
- `settings_version` (CWM 9.1) with seeded defaults; every result names the settings version it used.
- Map states LOW STOCK and OVER CAPACITY, in addition to NORMAL and EMPTY.
- Answer-wording cache (agent spec 14b), input classifier, required-facts eval gate.

**Out of scope (later slices)**

| Item | Slice |
|---|---|
| Festival question and `get_festival_risks` | 5 |
| Exceptions inbox, daily brief, exception engine and its tables | 3 |
| Search box | 3 |
| Admin settings editor | 6 |
| Learned routes, cross-chat memory, broader autonomous behaviour | not planned |

**Constraint.** The DSL implements the approved measure catalogue, dimensions, filters and operators only. No measure, dimension or operator is added in Slice 2.

## 2. Decisions recorded against the approved specs

| # | Decision | Spec affected | Why |
|---|---|---|---|
| D1 | Add `get_slow_in_prime`, a thin tool over the approved `SLOW_IN_PRIME` rule, using the **product-level** `days_since_last_pick` as the rule defines | Semantic 8 (tool list) | The personas question "Are slow movers sitting in the fast-pick zone?" maps to this rule; `is_prime` is not a DSL dimension, so the DSL cannot answer it reliably. No new semantics |
| D2 | Low stock comes from each product's replenishment policy (`available` in the pick bin `<= min_qty`), not from a settings threshold | none (confirms semantic 6) | The approved rule already defines it |
| D3 | Settings use the CWM 9.1 `settings_version` table: one row per version per site, `values` validated against `schema_version`, highest `version_no` in effect | none (implements CWM 9.1) | No second settings design |
| D4 | The answer-wording cache key adds the settings version explicitly; it adds no authorization fingerprint and no role | Agent 14b | A settings change must miss the cache visibly. The lookup runs only after the user's own authorized call produced this exact result, so an authorization fingerprint would split the cache without adding protection (14b reasoning) |
| D5 | Working sets carry provenance (originating run and tool) and have no time expiry | Semantic 7.5 | Follow-ups always re-query current data, so age cannot make values stale; resumed conversations keep working (FR-COP-11) |
| D6 | The input classifier is an extra detection layer; OPA, executor validation and grounding remain the security boundary | Agent spec (classifier) | A classifier can be wrong; it must never be the only control |

## 3. Acceptance criteria by step

### Step 1: Semantic foundations

- `pw.settings_version` per CWM 9.1, with forced RLS by tenant and composite site keys; written only through an owner function; readable by `pw_agent_read` through `sem`.
- Seeded defaults per site: dead-stock period 30 days, slow-in-prime period 14 days, capacity threshold 90 percent, stockout horizon 24 hours, trailing demand window, dispatch lead time. Values validated against a JSON schema before insert.
- `sem` functions for the rules and measures, each taking `site_id`, `as_of` and `settings_version_id` (semantic spec 2): `LOW_STOCK`, `DEAD_STOCK`, `SLOW_IN_PRIME`, `CAPACITY_HOTSPOT`, `STOCKOUT_RISK` (H as a parameter, with the fixable flag), delayed order (4.5), zone summary, top movers, and every measure in the 7.2 catalogue.
- Each rule has one implementation, used by its tool, by the DSL where the measure appears, and by the map state.
- Tests: hand-calculated fixtures per rule; the consistency test of semantic spec 10 (one number, identical across tool, DSL and map state for one `as_of` and settings version).

### Step 2: Fixed operational tools

- The eight tools in section 1, each a thin typed wrapper: strict input models (unknown fields refused), run through `tools.executor` (kill switch, validation, rate limit, OPA, audit), as `pw_agent_read` in a read-only snapshot.
- Every result is a result envelope with highlights from the rule (`reason` and `severity` per semantic 7.6), evidence with `as_of`, `settings_version_id`, freshness and "how computed".
- OPA scopes: each tool maps to its read scope in policy data; Rego tests cover each.
- Fast-path routes for the unambiguous personas questions.

### Step 3: Dynamic query engine

- `query_inventory` accepts the request shape of semantic 7.1 and runs the pipeline of 7.4 in order: schema, catalogue, values, authorization, compilation, execution, envelope.
- Compilation resolves every name only through the semantic catalogue to reviewed expressions over `sem` objects; every value is a bound parameter; nothing from the model becomes SQL text.
- Rejections return a structured reason the agent can act on and count toward the step limit.
- Non-existent dimension values return "no such {dimension}: {value}", never an empty table.
- `limit + 1` fetch for truncation; 3 second statement timeout; DSL results highlight per 7.6 with `reason = query`, `severity = info`.
- Tests: every rule of 7.1 to 7.4 accepted and rejected; an injection suite (names, values and operators that try to reach other tables, functions or SQL) is refused or bound as literal values; a property test that compiled SQL never contains request text.

### Step 4: Conversational working sets

- The working set is the identifiers of the last successful result in the same conversation, site, tenant and owner, at most 500, with provenance (run and tool).
- `scope.working_set = true` restricts the next query to those identifiers and re-queries current data.
- Identifiers that no longer match are reported in the answer ("2 of the 12 products are no longer stocked").
- No previous result, or no usable identifier type, is rejected with "there is no previous result to narrow down".
- Cleared by a new conversation and by a simulation reset.
- Tests: the personas follow-ups ("Of those, which are in zone B?", "Show only the ones worth more than 50,000"); isolation across users, conversations and sites.

### Step 5: Copilot integration

- All new tools registered; the model chooses among them on path C; fixed tools preferred over the DSL when one fits.
- Grounding covers the new result types (numbers and identifiers in the text appear in results).
- Suggestions after each answer map only to built tools (FR-COP-07).

### Step 6: Frontend and map

- Map states LOW STOCK and OVER CAPACITY from the same semantic rules, drawn with colour plus pattern or icon (FR-MAP-03).
- Answer highlights are a separate layer over the base state and never replace it.
- Answer cards render every new result type: table, how computed, freshness, settings version, truncation.
- Map and results use the same canonical location identifiers.

### Step 7: Quality and efficiency

- Answer-wording cache per agent 14b plus D4: lookup only after authorization, a fresh tool run and result hashing; only grounded wording stored; a settings change misses.
- Input classifier per D6; adversarial eval cases for direct and indirect injection pass at 100 percent.
- Required-facts gate: eval cases list required facts (identifiers, quantities, locations, dates); an answer missing one, or containing an incorrect operational fact, fails even if well written.
- Eval sets cover every personas question except the festival one, including follow-ups.

## 4. Definition of done

- Engineering baseline 8.1 met; lint, types and import contracts clean.
- Deterministic eval gate passes in CI; model gate passes on Vertex with every gated metric at or above its threshold, facts gate included.
- Code review and security review findings fixed with regression tests.
- Specs updated with change-log entries for D1 to D6 and for any findings during build.
- Merged to `main` through a pull request with green CI.

## 5. Change log

| Date | Change |
|---|---|
| 2026-11-19 | First draft after the product owner approved the Slice 2 boundary and decisions D1 to D6. |
