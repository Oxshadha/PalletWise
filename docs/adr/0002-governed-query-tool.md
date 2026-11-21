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

### 2. The guard: an allowlist over the parsed tree

The SQL text is parsed by PostgreSQL's own parser (`pglast`, libpg_query, pinned to the same major
version as the server, PostgreSQL 17, with a compatibility test on every upgrade) into a syntax
tree. Parsing only proves the text is a `SELECT`; acceptance comes from walking the whole tree and
allowing nothing that is not listed. Anything unlisted is refused, with the rule that refused it.

| Construct | Allowed in the first version |
|---|---|
| Statements | exactly one `SELECT`; no `INTO`, locking clause, `WITH`, `UNION`/`INTERSECT`/`EXCEPT`, `VALUES`, `TABLESAMPLE` |
| Node types | an explicit list of the node types a plain select uses (target list, column and constant references, boolean and comparison expressions, `CASE`, `IN`, `BETWEEN`, `IS NULL`, `LIKE`/`ILIKE`, sort, group, limit); every other node type is refused |
| Relations | `FROM` and `JOIN` only over `gq` views, schema-qualified or resolved against `search_path = gq`; no subquery in `FROM`, no function in `FROM` |
| Subqueries | only in `WHERE` and `HAVING`, as `EXISTS`, `IN` or a scalar comparison, themselves checked by the same rules |
| Functions | aggregates `count`, `count(DISTINCT ...)`, `sum`, `avg`, `min`, `max`; `round`, `abs`, `coalesce`, `nullif`, `greatest`, `least`; `lower`, `upper`; `date_trunc`, `extract`, date arithmetic. No `now()`, `current_date` or other clock reads: the site's current time comes from the one-row view `gq.site_now` (domain code uses the site clock, AGENTS rule 6). No window functions in the first version. |
| Operators | `=`, `<>`, `<`, `<=`, `>`, `>=`, `+`, `-`, `*`, `/`, `AND`, `OR`, `NOT`, `LIKE`, `ILIKE`, and date plus or minus interval |
| Casts and types | casts only to `integer`, `bigint`, `numeric`, `text`, `date`, `timestamptz`, `interval`, `boolean` |

**Why names identify the objects.** The guard checks names, and the database makes those names
unambiguous: the query role has no `CREATE` privilege on any schema and no `TEMPORARY` privilege on
the database, so it cannot define a function, operator, type or cast of its own; it has `USAGE` on
`gq` only, so `pw`, `sem` and `public` objects cannot be resolved at all; and its `search_path` is
fixed to `pg_catalog, gq`. Every allowed name therefore resolves to the built-in object of that name.
A test asserts these privileges so a later grant cannot silently break the argument.

The accepted tree is printed back by the parser (deparse) and that normalized text is what runs and
what the user sees. Deparsing removes comments and odd formatting; it is not the security boundary.
The boundaries are the tree allowlist and the role's privileges.

### 3. The data it may see: governed views with declared semantics

A new schema `gq` holds read-only views over the semantic layer for this tier only: products,
locations, stock, movements, orders, order lines, allocations, exceptions, the calendar and the
site's current time (`gq.site_now`). Customers appear by business code and name only; no contact or personal data, no session, token,
user, audit, usage, settings or proposal data.

**Isolation never depends on the query.** Like the existing `sem` views, the `gq` views are
owner-rights views over tables with forced row-level security keyed on `pw.current_tenant()`, which
raises when the tenant is missing; each view also filters on the request's site,
`current_setting('app.site_id')::uuid`, which raises when it is missing. Both settings are set by
the runner from the authenticated session, in the query's own transaction, before the query runs;
the tool's input has no tenant or site field, and `set_config` and `SET` are not allowed by the
guard. The query role is granted `SELECT` on the `gq` views only (never on the tables underneath,
which is why the views are not security-invoker) and never `BYPASSRLS`. A tenant or site id
written inside the query is just a filter on already-isolated rows, never an authorization.

**Each view declares its semantics** in a catalog file versioned with the semantic layer
(`gq_catalog.yaml`), which is also the model's schema description:

- its grain (one row per what), primary key, and the foreign keys to other views with their
  cardinality (many to one);
- for each measure: its unit and whether it is additive (`SUM`, `MIN`, `MAX`, `AVG` allowed), or
  non-additive (no `SUM` or `AVG`; filter, sort, `MIN` and `MAX` only). Examples: stock on hand and
  available (units, additive across bins and products), inventory value (LKR, additive), days of
  cover and utilization percent (non-additive), order line quantities (units, additive at line grain).

### 4. Correctness rules checked by the guard

A query that is allowed by section 2 is also checked against the catalog. These rules make join
fan-out and meaningless aggregates impossible in the first version rather than detected afterwards:

1. **Joins only many to one.** Every join must be an equality from a foreign key of the left side
   to the declared primary key of the joined view. Rows of the base view (the first in `FROM`) are
   therefore never multiplied, so counts and sums over it stay correct.
2. **Measures are aggregated only at the base grain.** `SUM` and `AVG` may use measures of the base
   view only; a joined view contributes attributes to filter and group by, never a summed measure.
   Order-level values summed over order lines are refused this way.
3. **Aggregates follow the measure's declaration.** `SUM` or `AVG` of a non-additive measure (days
   of cover, utilization) is refused.
4. **Units never mix.** Arithmetic between measures of different units (units and LKR) is refused.
5. **`count` counts something defined:** `count(*)` counts base-grain rows; `count(DISTINCT key)`
   counts a declared key.

A refusal tells the model why ("days of cover cannot be summed across products"); the model may try
once more, else the copilot declines honestly. A question these rules cannot answer correctly is a
decline, never an approximate number.

### 5. The runner: read-only role and hard limits

- A new database role `pw_query_read` (rule 7: its own path) with the privileges of section 2 and 3
  and nothing else; its own connection pool of at most 2 connections, so governed queries can never
  take the copilot's or the API's connections.
- Each query runs in its own read-only transaction: `statement_timeout` 2 seconds, `work_mem`
  capped, at most 200 rows (the runner wraps the query in a limit and reports truncation), at most
  20 columns, at most 256 KB of response. A timeout cancels the statement on the server and the
  connection is reset before reuse.
- `EXPLAIN` before running refuses a plan above a fixed cost as too broad. Planner estimates are not
  bounds, so this only adds to the timeout, transaction and size limits, which hold regardless.

### 6. The answer: labelled, grounded, shown

- The result envelope is marked `source: governed_query`. The answer card says "Answered with a
  custom query" and shows the rows, the data time and whether the rows were truncated, so it is
  never mistaken for a standard metric. Users do not see the SQL; the normalized query is kept in
  the audit record and the trace, where the Admin can inspect it.
- The existing output checks apply unchanged: numeric grounding (agent spec 6.1), named entities,
  dashes. A governed answer whose text fails them falls back to a plain table of the rows.
- Highlights only for rows that carry a bin, zone or product id from the views.
- Every call is audited: the normalized SQL, its hash, the guard verdict and the rule that refused
  it, rows, truncation, bytes and duration. The Admin's "Unanswered questions" view also lists
  questions the governed tier answered, so frequent ones can become proper tools.

### 7. Evaluation and enablement

The tier is on by default for every tenant and the Admin can switch it off (and on again) per
tenant: an agent registry flag, rechecked on every call like the kill switch, set through an Admin
API endpoint under `admin.agents.manage` in Slice 5 and a toggle in the admin panel in Slice 7.
"On" takes effect only for a configuration that passed this gate:

| Gate | Threshold |
|---|---|
| Guard refusals on an adversarial SQL set (writes, catalogs, `pw` and `sem` objects, other sites and tenants, `set_config`, sleeps, files, stacked statements, comment tricks, casts to unlisted types) | 100 percent refused, as a deterministic test, no model involved |
| Semantically invalid queries (summing days of cover, order values over order lines, mixed units, a fan-out join, a measure no view defines) | 100 percent refused by the correctness rules, deterministic |
| Routing: questions the existing tools or the query language answer must not use the governed tier | at least 95 percent stay on their tools |
| Correctness on governed-only questions, against reference SQL written independently | at least 90 percent exact rows |
| Grounding violations | 0 |
| Latency and cost | ADR 0001's balanced gate, unchanged |

**A passed gate covers one configuration.** The gate report records a fingerprint of the guard
version, the model and prompt versions, the `gq` view definitions and catalog, the semantic measure
definitions and the query role's privileges. The tier runs only while the running configuration's
fingerprint matches a passed report; any change switches it off until the gate passes again, so a
tested configuration cannot silently become an untested one.

### 8. Requirements and rules this changes (product owner approval)

| Item | Now | After |
|---|---|---|
| FR-COP-03 | The model never writes or runs SQL. | The model never writes SQL except a `SELECT` submitted to the governed query tool, which parses it and validates it against an allowlist and the governed views' declared semantics in application code, and runs it read-only on governed views with hard limits. No other path sends model output to the database. |
| FR-COP-02 | Five answer layers | Six: fast path, specific tools, query language, multi-step combination, governed query, honest decline |
| Baseline rule 4 | The model never writes SQL; tools call semantic functions only. | The same, except the governed query tool, which runs only parsed and validated `SELECT`s on `gq` views as `pw_query_read`. |
| Baseline rule 3 | Business calculations live only in the semantic layer. | Unchanged for every defined metric. A governed query may count and aggregate the semantic layer's measures only as each measure's declaration allows (section 4); its answer is labelled as a custom query and never presented as a standard metric. |

## Consequences

- More questions answered, each with its query shown and audited; frequent ones are promoted to
  proper tools over time.
- New attack surface, bounded by the tree allowlist, the role's privileges, the views and the
  limits, and tested deterministically on every change.
- The first version is deliberately narrow: no window functions, no `WITH`, no `FROM` subqueries,
  joins only many to one. Some questions will still be declined; each widening is a later change
  with its own correctness rule and evaluation.
- One more model round trip on these questions (the model writes the query, then words the rows);
  they are held to the same latency and cost gate.
- More to maintain: the `gq` views and their descriptions move with the semantic layer.

## Product owner decisions (2026-11-21)

1. On by default; the Admin can switch it off per tenant (an API endpoint now, the admin panel in
   Slice 7). It runs only for a configuration whose gate passed (section 7). Revised by the product
   owner from "off by default".
2. Users do not see the SQL. The answer card is labelled as a custom query, with the rows, data
   time and truncation; the Admin inspects the normalized query in the audit record and the trace.
   Revised by the product owner from "shown to every user".
3. The first `gq` views as listed in section 3; customers by code and name only.
4. Initial limits: 2 seconds, 200 rows, 20 columns, plus 256 KB of response.

## Status

Proposed, revision 2, 2026-11-21, after the product owner review: allowlist over the whole tree with privileges that make names unambiguous, declared view semantics and correctness rules (join fan-out, non-additive measures, units), owner-rights views for isolation, response size and pool limits, a semantic-invalidity gate and a configuration fingerprint. Awaiting approval; no code until approved.
