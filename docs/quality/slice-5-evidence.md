# Slice 5 evidence record

Slice 5 (slice-5-acceptance, ADR 0002): the governed SQL tier. Measured on 2026-11-21 on the
one-laptop stack (Apple silicon, 8 GB memory), 180-day reference site (seed 42), model Gemini
Flash-Lite on Vertex AI.

**Result: the tier is built and tested, and it failed its model gate. It stays off.**
`semantic/governed_gate.json` lists no passed configuration, so the copilot never offers
`run_governed_query`. Every other layer works as in Slice 4.

## 1. What was built (steps 1 to 4)

| Step | Delivered | Tests |
|---|---|---|
| 1. Governed views | `gq` schema (`site_now`, `products`, `locations`, `stock`, `movements`, `orders`, `order_lines`, `allocations`, `exceptions`, `calendar`), semantics catalog `semantic/gq_catalog.yaml`, role `pw_query_read` (governed views only, `search_path` pinned) | `tests/integration/test_governed_views.py` |
| 2. Guard | `pglast` 7 parser matched to the server major version; whole-tree node allowlist; functions, operators, casts and relations allowlisted; correctness rules from the catalog (many-to-one joins on keys, aggregates only of the first view's measures, non-additive measures, units, `count(DISTINCT)` of keys and references only); normalized SQL is the deparsed tree | `tests/unit/test_governed_guard.py` (72) |
| 3. Runner and traceability | Read-only transaction as `pw_query_read`; tenant and site from the session; 2 s timeout, 200 rows, 20 columns, 256 KB, EXPLAIN cost cap, pool of 2; `application_name`, `app.agent_id`, `app.run_id`; audit records the SQL, its hash, the fingerprint, the verdict and the rule, with agent id and version, prompt version, run and user; Admin switch `PUT /api/admin/agent/governed-query`; scope `query.governed` | `tests/integration/test_governed_tool.py` |
| 4. Copilot | Catalog-driven tool description; label "Answered with a custom query"; answer card badge; the tool is offered only after a passed gate | integration and web tests |

## 2. The model gate (step 5)

Sets: 30 governed questions with independently written reference SQL (rows compared), 15
semantically invalid questions, 15 adversarial prompts, plus the existing routing, operations and
adversarial sets. Gates (ADR 0002 section 7): `governed.correct >= 0.90`,
`governed.routing_kept >= 0.95`, `governed.invalid_refused = 1.0`, with ADR 0001's quality,
latency and cost gates in the same run.

| Run | Report | governed.correct | invalid refused | routing kept | tool accuracy | tier 3 complete p95 | Cost |
|---|---|---|---|---|---|---|---|
| c667e7e | `eval-reports/2026-11-21-c667e7e-model.md` | 0.49 | 1.0 | 1.0 | 0.90 | 8.36 s | 2.02 USD |
| ee15759 | `eval-reports/2026-11-21-ee15759-model.md` | 0.63 | 0.978 | 1.0 | 0.83 | 8.17 s | 1.89 USD |
| ee15c06 | `eval-reports/2026-11-21-ee15c06-model.md` | 0.00 | 1.0 | 1.0 | 0.58 | 7.05 s | 2.69 USD |
| b2e886c | `eval-reports/2026-11-21-b2e886c-model.md` | **0.73** | 0.978 | 1.0 | 0.90 | 9.00 s | 3.25 USD |

Total spend on the gate: **9.84 USD**.

What each run taught:

- **c667e7e:** the eval tenant's token budget ran out mid-run, so later cases degraded to
  templates. Fixed by raising the eval budget; some reference cases and the tool wording were
  corrected (ee15759).
- **ee15759:** the model ran out of output tokens while writing longer queries
  (`OutputTokenLimitExceeded`); the output cap was raised to 2,048. Offering an unevaluated tool
  also lowered routing accuracy and raised latency for every question, so the catalog now offers
  the tool only after a passed gate.
- **ee15c06:** the eval mark that should offer the tool was an empty set and evaluated as false,
  so the tool was never offered (0.00). Fixed in b2e886c with a unit test.
- **b2e886c:** the honest measurement: 22 of 30 governed questions correct, one invalid question
  answered instead of refused.

Safety held in every run: injection resistance 1.0, grounding violations 0, no misroutes, and no
query reached anything outside the governed views. The failure is the model's SQL accuracy on this
catalog, not the guard or the architecture.

**Decision (product owner, 2026-11-21):** leave the tier off, finish the slice, and retry later
with a stronger model or a revised prompt. A pass writes its fingerprint and report into
`governed_gate.json` for review.

## 3. Definition of done

| Item | State |
|---|---|
| Steps 1 to 4 criteria | Met |
| Step 5: gate passes for the committed fingerprint | **Not met** (above); the tier is off, so no unevaluated configuration runs |
| Step 6: storyline governed step and live browser check of a governed answer | Not done: depends on a passed gate |
| Lint, unit, integration, policy, web tests | Pass (section 4) |
| Code review and security review | Done (section 5) |
| Model gate rerun on the final configuration without the tier | Not run, to save cost; with the tool not offered, the live path is the Slice 4 configuration except the 2,048 token output cap |

## 4. Tests

| Suite | Result |
|---|---|
| `make lint` (ruff, pyright strict, import boundaries, ESLint, Prettier, tsc) | pass |
| Unit | 483 passed |
| Integration (PostgreSQL 17, OPA) | 399 passed (one stale assertion fixed: the copilot's default scopes now include `query.governed`, D4) |
| Policy | 17 of 17 |
| Web | 100 passed |

## 5. Code and security review

Reviewed: the guard, the runner and tool, the admin API, the admin read role and its policies.

| Finding | Severity | Fix |
|---|---|---|
| A type cast qualified with a schema other than `pg_catalog` (`x::other.text`) passed the type name check | Low (the role has no other schema; defence in depth) | Refused; adversarial unit case |
| A run-time data error in the model's SQL (division by zero, bad date) surfaced as an internal error | Low | Refusal with rule `runtime`, audited, the model may fix it once; integration test |
| CSV export quoted leading `= + - @` but not tab or carriage return | Low | Quoted; test |

Confirmed: CSRF on every state change (middleware), Idempotency-Key on admin changes, OPA on every
admin request, regular expressions not allowed in governed queries, no system functions, no
`WITH`, `INTO`, locking or set operations, the clock only from `gq.site_now`.
