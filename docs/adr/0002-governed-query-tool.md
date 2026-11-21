# 0002. Governed query tool: a last-resort, read-only SQL tier

## Context

The copilot answers through the fast path, the specific tools and the query language (FR-COP-02).
The model never writes SQL (FR-COP-03, engineering baseline rule 4), and business calculations
live only in the semantic layer (rule 3). This is safe and accurate, but a question the tools and
the query language cannot express ends in a decline, even when the data to answer it is in the
semantic views. Examples measured or expected from real use:

- "Which customers ordered SKU-258 more than twice this month?" (the query language has no
  customer dimension over order lines)
- "How many orders had more than 10 lines last week?" (no filter on a per-order count)
- "Which racks have both a dead-stock item and a low-stock item?" (a join of two rule results)

The product owner wants these answered, as a hybrid on top of the current approach, not instead of
it. A literal "answers any question" is not a claim this product will make: a governed tier answers
more, within fixed limits, and still declines honestly outside them.

Text-to-SQL is risky in three ways, and each needs a deterministic boundary, not a prompt:

1. **Access.** The query could read another tenant's or site's data, system catalogs, or tables
   holding sessions, tokens and audit records.
2. **Effect.** It could write, lock, call functions with side effects, or run for minutes.
3. **Truth.** The model would be defining a calculation, so an answer could be wrong while looking
   official, and the numbers in the text could differ from the rows.

## Decision

### 1. One new tool, last in line

`run_governed_query(sql, purpose)` is a tool in the existing registry, called through
`tools.executor` like every tool (policy, kill switch, audit, tracing; AGENTS rule 5). The model may
call it only after deciding that no other tool and no query-language request can express the
question; the prompt says so, and the eval gate measures it (section 6). It never replaces a tool:
a question the existing tools answer must still go to them.

### 2. The guard: parse, never pattern-match

The SQL text is parsed by PostgreSQL's own parser (`pglast`, libpg_query) into a syntax tree and
accepted only when every rule holds. Anything the guard does not recognise is refused.

- Exactly one statement, a plain `SELECT` (with `WITH` of `SELECT`s, joins, `GROUP BY`, `HAVING`,
  `ORDER BY`, window functions). No `INTO`, no `FOR UPDATE` or `SHARE`, no `LOCK`, no data-modifying
  `WITH`, no `COPY`, no `SET`, no `DO`, no transaction control.
- Relations only from an allowlist of governed views in a new schema `gq` (section 3). Any other
  schema, table, catalog (`pg_*`, `information_schema`) or function returning a relation is refused.
- Functions only from an allowlist: aggregates (`count`, `sum`, `avg`, `min`, `max`), arithmetic,
  rounding, `coalesce`, `nullif`, comparison, date and time arithmetic and truncation, string
  comparison and `lower`/`upper`. Nothing that sleeps, reads files or other databases, uses large
  objects, sets configuration or calls `pw.` or `sem.` functions.
- The parsed tree, not the model's text, is what runs: it is printed back by the parser (deparse),
  so comments, odd quoting and stacked statements cannot survive.

### 3. The data it may see: governed views

A new schema `gq` holds read-only views over the semantic layer for this tier only: products,
locations, stock, movements, orders and order lines, allocations, exceptions, the calendar. Each
view:

- is filtered by the tenant (row-level security underneath) **and** by the site of the request
  (`current_setting('app.site_id')`), so the query cannot name another site even by id;
- exposes only operational columns with plain names and comments; no session, token, user, audit,
  usage, settings or proposal data, and no internal ids beyond the ones answers need for highlights;
- exposes the semantic layer's own measures (on hand, available, days of cover, value) as columns,
  so a governed query aggregates defined measures rather than inventing them.

The view definitions, their columns and comments are the model's schema description; they are
versioned with the semantic layer.

### 4. The runner: read-only role and hard limits

- A new database role `pw_query_read` (rule 7: its own path), `SELECT` on the `gq` views only, no
  other privilege, `BYPASSRLS` never.
- Each query runs in its own read-only transaction with the tenant and site set, `statement_timeout`
  2 seconds, `work_mem` capped, at most 200 rows returned (the runner wraps the query in a limit and
  reports truncation), at most 20 columns.
- `EXPLAIN` before running: a plan above a fixed cost is refused as too broad, with a suggestion to
  narrow the question.

### 5. The answer: labelled, grounded, shown

- The result envelope is marked `source: governed_query`. The answer card says "Answered with a
  custom query" and shows the query (formatted, read-only) under "How it was computed", next to the
  data time, so the user can see exactly what was counted.
- The existing output checks apply unchanged: numeric grounding (agent spec 6.1), named entities,
  dashes. A governed answer whose text fails them falls back to a plain table of the rows.
- Highlights only for rows that carry a bin, zone or product id from the views.
- Every call is audited: the SQL as run, its hash, the guard verdict and the rule that refused it,
  rows, truncation and duration. The Admin's "Unanswered questions" view also lists questions the
  governed tier answered, so frequent ones can become proper tools.

### 6. Evaluation before enabling

The tier is off by default and switched on per tenant by the Admin (agent registry flag, rechecked
on every call like the kill switch). It may be switched on only after the model gate passes on a new
case set:

| Gate | Threshold |
|---|---|
| Guard refusals on an adversarial SQL set (writes, catalogs, other sites and tenants, sleeps, files, stacked statements, comment tricks) | 100 percent refused, as a deterministic test, no model involved |
| Routing: questions the existing tools answer must not use the governed tier | at least 95 percent stay on their tools |
| Correctness on governed-only questions, against reference SQL written independently | at least 90 percent exact rows |
| Grounding violations | 0 |
| Latency and cost | ADR 0001's balanced gate, unchanged |

### 7. Requirements and rules this changes (product owner approval)

| Item | Now | After |
|---|---|---|
| FR-COP-03 | The model never writes or runs SQL. | The model never writes SQL except a `SELECT` submitted to the governed query tool, which parses and validates it in application code and runs it read-only on governed views with hard limits. No other path sends model output to the database. |
| FR-COP-02 | Five answer layers | Six: fast path, specific tools, query language, multi-step combination, governed query, honest decline |
| Baseline rule 4 | The model never writes SQL; tools call semantic functions only. | The same, except the governed query tool, which runs only parsed and validated `SELECT`s on `gq` views as `pw_query_read`. |
| Baseline rule 3 | Business calculations live only in the semantic layer. | Unchanged for every defined metric. A governed query may count, sum or average the semantic layer's measures; its answer is labelled as a custom query and never presented as a standard metric. |

## Consequences

- More questions answered, each with its query shown and audited; frequent ones are promoted to
  proper tools over time.
- New attack surface, bounded by the parser allowlist, the role, the views and the limits, and
  tested deterministically on every change.
- One more model round trip on these questions (the model writes the query, then words the rows);
  they are held to the same latency and cost gate.
- More to maintain: the `gq` views and their descriptions move with the semantic layer.

## Open questions for the product owner

1. Off by default, enabled per tenant by the Admin after the gate passes. Recommended: yes.
2. Show the SQL to every user under "How it was computed", or only to the Admin. Recommended:
   to every user (it explains the answer; it reveals no data beyond the rows).
3. Which data in the first `gq` views: products, locations, stock, movements, orders, order lines,
   allocations, exceptions, calendar. Recommended: these, and no customer personal data (customers
   appear by code and name only).
4. Limits: 2 s, 200 rows, 20 columns. Recommended: these, revisited after measuring.

## Status

Proposed, 2026-11-21. Awaiting product owner approval; no code until approved.
