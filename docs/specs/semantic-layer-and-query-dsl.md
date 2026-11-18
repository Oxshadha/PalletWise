# Semantic Layer and Query DSL

| | |
|---|---|
| Status | Approved (2026-11-18, revision 2) |
| Owner | Product owner |
| Last updated | 2026-11-18 |
| Implements | FR-COP-02 to FR-COP-07, FR-EXC-01 to FR-EXC-03, FR-FES-01 to FR-FES-05, FR-BRF-01, NFR-PERF-06 in [requirements.md](../product/requirements.md) |
| Consumes | [canonical-warehouse-model.md](canonical-warehouse-model.md) |

## 1. Purpose

The semantic layer is the **only** place where PalletWise defines what its numbers mean: available stock, demand, days of cover, festival projection, exception rules and impact scores. Every consumer uses the same definitions:

- copilot tools and the query DSL
- the exception engine
- the daily brief
- map states (low stock, empty, over capacity)

If two parts of the product could compute the same number differently, they eventually will. This spec prevents that by allowing exactly one implementation of each definition.

**The model never calculates.** The LLM selects tools and fills DSL requests. The semantic layer validates, computes and explains. The model may restate a result and its assumptions; it cannot change a formula, a parameter it was not given, or a number.

## 2. Architecture

```
copilot tools ─┐
query DSL ─────┤
exception engine ─┼──> semantic functions (sem schema) ──> canonical model (pw schema)
daily brief ───┤         run as pw_agent_read or pw_evaluator, RLS enforced
map states ────┘
```

- **Definitions as code in one place.** Each definition is a SQL function or view in the `sem` schema, declared `STABLE` and `SECURITY INVOKER`, so it runs with the caller's role and tenant context. `pw_agent_read` can use only the `sem` schema (CWM 11.3).
- **Semantic model file.** `semantic/model.yaml` in the repository lists every measure, dimension and filter the DSL accepts: name, label, unit, class, allowed dimensions, the `sem` object that computes it, and its "how computed" explanation template. It is versioned with the code. Changing it requires passing tests and the evaluation suite (NFR-QLT-02). Admin cannot edit it at runtime in v1.
- **Explicit inputs.** Every semantic function takes `site_id`, `as_of` and `settings_version_id` as parameters. A request reads the site clock and the current settings version once, at its start, and passes them to every call. All numbers in one answer, brief or evaluation therefore share one point in time and one settings version.
- **Independent check for critical calculations.** Days of cover, festival projection and impact score also have a small pure-Python reference implementation used only in tests. Both implementations must agree on every fixture and on randomized property tests.
- **Compute on demand in v1.** At v1 volumes the functions run on demand within the performance budget (section 9). If one exceeds it, a snapshot table refreshed on each clock step is introduced through an ADR, with the same function as its only writer.

## 3. Time and calendar rules

| Term | Definition |
|---|---|
| now | `as_of`, read from the site clock (CWM 7.3). Never the database clock. |
| today | the site-local date of `as_of` |
| operating day | a date with `business_calendar.is_operating = true` for the site's region |
| yesterday (for the brief) | the most recent operating day before today |
| "last N days" in a question or DSL `time_range` | N calendar days ending at `as_of` |
| trailing window (for averages) | the last W operating days before today, today excluded. W is the setting `trailing_window_operating_days`, default 28. |

Questions use calendar days because that is how people speak. Averages use operating days because a warehouse does not consume stock on days it is closed.

## 4. Core definitions

### 4.1 Stock

| Name | Definition |
|---|---|
| `on_hand` | `inventory_balance.on_hand` |
| `allocated` | `inventory_balance.allocated` (active reserved quantity) |
| `available` | `on_hand - allocated` |
| storable bin | an active bin with role `PICK`, `RESERVE` or `BULK`. `STAGING`, `RECEIVING` and `RETURNS` bins hold stock that is in transit, not available for orders. |
| `storable_on_hand(product)` | sum of `on_hand` over storable bins |
| `storable_available(product)` | sum of `available` over storable bins |
| `pick_face_on_hand(product)`, `pick_face_available(product)` | the same sums over `PICK` bins only |
| `stock_value` | `on_hand * product.unit_value` |

### 4.2 Demand and movement

| Name | Definition |
|---|---|
| demand event | a `PICK` movement whose order is not `CANCELLED` adds its quantity; a `TRANSFER` with `reason_code = 'PICK_REVERSAL'` subtracts its quantity. `SHIP` is never demand. |
| `daily_demand(product, date)` | sum of demand events with site-local `occurred_at` date equal to `date` |
| `avg_daily_demand(product)` | demand per operating day: sum of `daily_demand` over all dates from the first to the last day of the trailing window (including any non-operating dates in between), divided by W. Operating days with no picks count as zero. |
| `demand_units(product, range)` | sum of demand events in a time range |
| `units_moved(range, type)`, `movement_count(range, type)` | sum of `qty` and count of movements in a time range, optionally by movement type |
| `days_since_last_pick(product)` | days between `as_of` and the latest `PICK` movement of the product whose order is not `CANCELLED`; reversals are ignored. Null if never picked. |
| `days_since_last_pick(product, bin)` | the same, restricted to picks from one bin. A separate definition: it describes a bin, not a product. |
| `days_since_last_move(product)` | days between `as_of` and the latest movement of any type |

**Why non-operating dates count in the numerator.** Stock picked on a non-operating date (for example an extra Sunday shift) was really consumed. Dropping it would understate demand and overstate cover. Because projections consume stock only on operating days (section 5), the total demand of the window is spread over its W operating days. In the v1 simulation, non-operating days have no picks, so the two readings give the same result; the rule matters for real WMS data.

**"Not moved"** in a Manager's question means not picked for an order. The copilot maps "items that have not moved in N days" to `days_since_last_pick > N`, and the answer card says so. Internal transfers do not count as movement for this purpose, because a pallet being relocated is not the product being used.

### 4.3 Cover and projection

| Name | Definition |
|---|---|
| `days_of_cover(product)` | `storable_on_hand / avg_daily_demand`, expressed in **operating days** and labelled as such. Null when `avg_daily_demand = 0`, shown as "no recent demand". |
| `projected_days_of_cover(product)` | festival-aware projection, section 5 |

`on_hand` rather than `available` is used for cover, because projected demand already includes the orders that hold the allocations. Using `available` would count those orders twice.

### 4.4 Capacity

| Name | Definition |
|---|---|
| `bin_volume_used` | sum over products in the bin of `on_hand * unit_volume_m3` |
| `bin_weight_used` | sum of `on_hand * unit_weight_kg` |
| `utilization_pct(bin)` | `100 * max(bin_volume_used / capacity_volume_m3, bin_weight_used / capacity_weight_kg)` |
| `utilization_pct(zone or rack)` | the same principle at every level: `100 * max(sum(volume used) / sum(capacity_volume_m3), sum(weight used) / sum(capacity_weight_kg))` over active bins |
| over capacity | `utilization_pct(bin) > 100` |

Division by zero cannot occur for a bin: the CWM requires both capacities to be greater than 0. A zone or rack with no active bins has null utilization, shown as "no active storage", never as 0 percent.

### 4.5 Orders

| Name | Definition |
|---|---|
| open line | line of an order whose status is not `SHIPPED` or `CANCELLED` |
| `unallocated_qty(line)` | `qty_ordered - qty_allocated` |
| due within H | `dispatch_deadline <= as_of + H` |
| delayed order | not `SHIPPED` or `CANCELLED` and `as_of > dispatch_deadline`, or `shipped_at > dispatch_deadline` |
| hours until due | `(dispatch_deadline - as_of)` in hours; negative when overdue |

### 4.6 Throughput (daily brief)

| Name | Definition |
|---|---|
| `units_picked(day)` | `daily_demand` summed over products for that day |
| `orders_shipped(day)` | orders with site-local `shipped_at` date equal to that day |
| comparison | yesterday's value against the average of the 7 operating days before yesterday |

## 5. Festival projection

### 5.1 Inputs

| Input | Source |
|---|---|
| base demand `B` | `avg_daily_demand(product)` |
| stock `S` | `storable_on_hand(product)` |
| uplift `u` | `settings.festival_uplift[category]`, in percent, per site per category. A category with **no value** uses 0 and the answer says "no festival uplift configured for this category". A category **explicitly set to 0** says "festival uplift: 0 percent". The two cases are never shown the same way. |
| ramp `r(d)` | `business_calendar.festival_ramp` for date `d` |
| next festival `F` | the first date after today with a non-null `festival` in the calendar |

### 5.2 Calculation

1. The projection days are the operating days from today through `F` inclusive. Non-operating days have no demand.
2. Projected demand on day `d` is `D(d) = B * (1 + u/100 * r(d))`. The ramp is applied **per day**, because it changes every day.
3. Cumulative demand `C(k)` is the sum of `D` over the first `k` projection days.
4. The **projected stock-out day** is the first projection day where `C(k) > S`.
5. `projected_days_of_cover = (k - 1) + (S - C(k - 1)) / D(k)` for that day `k`, with `C(0) = 0`. If no stock-out occurs through `F`, the value is null and shown as "more than N operating days", where N is the number of projection days.
6. **Festival shortage risk** means the projected stock-out day falls **on or before** the festival date. A shortage on the festival day itself is a festival shortage.

Every projection returns one of three outcomes, never a bare yes or no:

| Outcome | Meaning |
|---|---|
| `RISK` | a stock-out is projected on or before `F` |
| `NO_RISK_THROUGH_FESTIVAL` | the projection covers every day through `F` and no stock-out occurs |
| `UNKNOWN_BEYOND_DATA` | no festival exists in the calendar data after today, so nothing can be said beyond the calendar end. The answer says "the calendar data ends on {date}; no festival risk can be assessed beyond it". This is never shown as "no risk". |

Because `F` is found in the calendar itself, a found festival is always inside calendar coverage, so a projection to `F` is always complete.

### 5.3 Edge rules

| Situation | Rule |
|---|---|
| `B = 0` | no projection; outcome `NO_RISK_THROUGH_FESTIVAL` with the note "no recent demand" |
| Overlapping festivals | each day uses the ramp value stored for that date, whichever festival it leads up to; no festival "owns" a ramp day for the calculation. Risk is reported against `F`, the nearest festival only. |
| No festival after today in the calendar | outcome `UNKNOWN_BEYOND_DATA` |
| `projected_days_of_cover` used without a festival question (DSL) | horizon is through `F` if one exists, otherwise through the calendar end; a null value is shown as "more than N operating days, through {date}" |
| Exception lookahead | a `FESTIVAL_SHORTAGE` exception is raised only when `F` is within `festival_lookahead_days` (setting, default 21). The copilot can answer about a later festival on request. |

### 5.4 Hand-calculated fixture

Product in a category with 50 percent uplift. Trailing window total demand 280 units over 28 operating days, so `B = 10`. The next four operating days have ramps 0.6, 0.7, 0.8, 0.9, and the festival day (operating) has ramp 1.0.

| Day | Ramp | D(d) | C(k) |
|---|---|---|---|
| 1 | 0.6 | 13.0 | 13.0 |
| 2 | 0.7 | 13.5 | 26.5 |
| 3 | 0.8 | 14.0 | 40.5 |
| 4 | 0.9 | 14.5 | 55.0 |
| 5 (festival) | 1.0 | 15.0 | 70.0 |

| Stock S | Stock-out day | projected_days_of_cover | Festival shortage risk |
|---|---|---|---|
| 50 | 4 | 3 + (50 - 40.5) / 14.5 = 3.66 | yes |
| 60 | 5 (festival day) | 4 + (60 - 55) / 15 = 4.33 | yes |
| 100 | none | null, "more than 5 operating days" | no |

The same product with no uplift configured: `D = 10` every day, `C(5) = 50`; with `S = 50` there is no stock-out (50 is not greater than 50), so no risk.

## 6. Exception rules

The exception engine runs on every clock step (default every 15 simulated minutes) and after data changes, debounced, and only while the site's `data_status` is `READY` (CWM 5.2). It **reads** through the semantic functions and **writes** only through its own role, `pw_evaluator` (CWM 11.3), which has `EXECUTE` on the `sem` schema plus write access to the exception tables. `pw_agent_read` never gains write access because a service consumes semantic results. For each type the engine computes the current set of subjects, opens or updates their records, and resolves records whose condition no longer holds with `CONDITION_CLEARED` (FR-EXC-03). An evaluation row is written when the score or status changes.

| Type | Subject | Condition | Default settings |
|---|---|---|---|
| `STOCKOUT_RISK` | product | per product at the site: `D` = sum of `unallocated_qty` over open lines due within H; `P` = `pick_face_available`; `S` = `storable_available`. Raised when `D > P`. `shortfall = D - P`; `reserve_available = S - P`. **Fixable by replenishment** when `reserve_available >= shortfall`, otherwise **true shortage** of `shortfall - reserve_available` units. | H = 24 hours |
| `DELAYED_ORDER` | order | delayed order (4.5) | |
| `LOW_STOCK` | product and pick bin | product has a replenishment policy and `available` in its pick bin `<= min_qty` | |
| `DEAD_STOCK` | product | `storable_on_hand > 0`, `days_since_last_pick > D` (or never picked), and first receipt more than D days ago, so new items are not flagged | D = 30 days |
| `SLOW_IN_PRIME` | product | the product is slow (`days_since_last_pick(product) > P`, or never picked) and has stock in at least one bin of an `is_prime` zone. The prime bins are linked through `exception_bin` and highlighted. The rule is about slow **products** occupying prime space; idle stock of a fast product in a second bin is a different question, answered with `days_since_last_pick(product, bin)` through the DSL or bin details. | P = 14 days |
| `CAPACITY_HOTSPOT` | zone | `utilization_pct(zone) >= T`, or any bin in it over capacity | T = 90 |
| `FESTIVAL_SHORTAGE` | product | festival shortage risk (5.2) with `F` within the lookahead | 21 days |

**Replenishment need before the first dispatch window** (personas question) is the `STOCKOUT_RISK` rule evaluated with H set to the time until today's earliest open dispatch deadline, keeping only fixable products. The same function serves both, with H as a parameter.

### 6.1 Impact score

Three components, each normalized to 0 to 1 before weighting, so no component dominates by magnitude (FR-EXC-02):

| Component | Raw value | Normalized |
|---|---|---|
| lines `L` | open order lines affected | `min(L / L_cap, 1)` |
| urgency `U` | hours until the earliest affected dispatch deadline | `1` if overdue, otherwise `max(0, 1 - hours / H_cap)`; `0` if no order is affected |
| value `V` | value at risk in currency: shortfall quantity times unit value for shortage types; stock value for dead, slow and capacity types | `min(log10(1 + V) / log10(1 + V_cap), 1)` |

`score = 100 * (wL * L_n + wU * U_n + wV * V_n) / (wL + wU + wV)`

Defaults: `L_cap = 20` lines, `H_cap = 48` hours, `V_cap = 1,000,000` LKR, all weights 1. Caps and weights are site settings, validated on save: every cap greater than 0, every weight 0 or greater, and the sum of weights greater than 0. The score components, caps and weights are stored with each evaluation and shown as "why it is ranked here".

**Fixture.** A stockout risk affecting 5 lines, due in 12 hours, with 20,000 LKR at risk, default settings: `L_n = 0.25`, `U_n = 0.75`, `V_n = log10(20001) / log10(1000001) = 0.717`; score = `100 * (0.25 + 0.75 + 0.717) / 3 = 57.2`.

## 7. Query DSL

The DSL lets the copilot answer combinations that no fixed tool covers, without ever writing SQL (FR-COP-03). The model produces a JSON request; application code validates and compiles it.

### 7.1 Request shape

```json
{
  "measures": ["on_hand", "stock_value"],
  "dimensions": ["zone"],
  "filters": [
    {"field": "category", "op": "in", "value": ["Beverages"]},
    {"field": "days_since_last_pick", "op": ">", "value": 30}
  ],
  "time_range": {"last_days": 30},
  "sort": {"by": "stock_value", "direction": "desc"},
  "limit": 10,
  "scope": {"working_set": false}
}
```

| Field | Rule |
|---|---|
| `measures` | 1 to 3 names from the measure catalogue |
| `dimensions` | 0 to 2 names, each allowed for every requested measure |
| `filters` | 0 to 5; field is a dimension or a measure; operator allowed for that field's type |
| `time_range` | required when the request contains at least one flow measure, forbidden when it contains none. It applies **only to flow measures**; snapshot measures in the same request are evaluated at the shared `as_of`. `{"last_days": n}` with 1 to 365, or `{"from": date, "to": date}` within calendar coverage. The answer card states both: "stock as of {as_of}; demand over {range}". |
| `sort` | by a requested measure or dimension |
| `limit` | 1 to 100, default 20 |
| `scope.working_set` | when true, restricts results to the identifiers of the previous result (FR-COP-06); rules in 7.5 |

### 7.2 Measure catalogue

| Measure | Class | Allowed dimensions |
|---|---|---|
| `on_hand`, `allocated`, `available`, `stock_value` | snapshot | product, category, brand, zone, rack, bin, bin_role |
| `utilization_pct` | snapshot | zone, rack, bin |
| `days_of_cover`, `projected_days_of_cover` | snapshot | product, category, brand. For category and brand the value is the ratio of sums (total stock over total demand), not an average of product ratios. |
| `days_since_last_pick` | snapshot | product, or product and bin (uses the bin-level definition) |
| `days_since_last_move` | snapshot | product |
| `demand_units` | flow | product, category, brand, customer, day, week, month |
| `units_moved`, `movement_count` | flow | product, category, brand, movement_type, day, week, month |

Filterable dimensions also include `name_contains` (product name or SKU, at least 2 characters, matched as a literal with wildcards escaped).

### 7.3 Operators

| Field type | Operators |
|---|---|
| text dimension | `=`, `!=`, `in` (up to 50 values) |
| numeric measure | `=`, `!=`, `<`, `<=`, `>`, `>=`, `between` |
| time | `within_last_days`, `older_than_days` |
| product text | `name_contains` |

### 7.4 Validation and execution pipeline

1. **Schema**: the request matches the JSON schema exactly; unknown fields are rejected.
2. **Catalogue**: every measure, dimension, filter field and operator exists and is compatible (7.2, 7.3).
3. **Values**: types and ranges checked. A dimension value that does not exist produces a clear "no such {dimension}: {value}" result for every dimension type (product, SKU, category, brand, zone, rack, bin, customer, movement type), never an empty table that looks like a real answer.
4. **Authorization**: the policy engine confirms the user and agent may read the requested measures for the site (NFR-SEC-01).
5. **Compilation**: SQLAlchemy Core builds a query in which every measure, dimension and filter name is resolved **only through the semantic catalogue** to a fixed, reviewed expression over `sem` objects. No name can reach an arbitrary table, column or function. Every value is a bound parameter. The model's text never becomes SQL.
6. **Execution**: as `pw_agent_read`, in a transaction with tenant context, `statement_timeout` of 3 seconds, and `limit + 1` rows fetched to detect truncation (NFR-PERF-06).
7. **Result envelope**: returned to the agent and the UI (7.5).

Any rejection returns a structured reason the agent can act on, for example "measure days_of_cover cannot be grouped by zone; allowed: product, category, brand". Rejections count toward the step limit (NFR-REL-03).

### 7.5 Result envelope

Every tool and DSL result, not only the DSL, returns the same envelope:

| Field | Content |
|---|---|
| `columns`, `rows` | the data |
| `truncated` | true when more rows existed than the limit |
| `highlights` | list of `{target_type: bin, rack or zone, target_id, reason, severity}` (FR-MAP-04, FR-MAP-05) |
| `evidence` | `as_of`, `settings_version_id`, freshness per stream (CWM 10.2), and the "how computed" text of each measure, filled from `model.yaml` templates with the actual parameters (FR-COP-04) |
| `entities` | typed identifiers in the result (products, bins, zones, orders), stored as the next working set |
| `normalized_request` | the validated request, for audit and evaluation |

**Working set rules**

- Holds only typed identifiers (`product`, `bin`, `zone`, `order`), at most 500, from the last successful result in the same conversation, site and tenant.
- A follow-up re-queries current data for those identifiers; it never reuses old values.
- Replaced by every new successful result; cleared by a new conversation and by a simulation reset (FR-SIM-09).
- `scope.working_set = true` with no previous result, or with no identifiers of a type the request can use, is rejected with the reason "there is no previous result to narrow down".

### 7.6 Highlight derivation

Highlights are derived by the semantic layer, never by the model:

| Result contains | Highlights |
|---|---|
| a `bin`, `rack` or `zone` dimension | those locations |
| a `product` dimension and no location dimension | the storable bins holding those products, ordered by `on_hand` descending then bin code, at most 200. If more exist, the envelope marks highlights as truncated and the card says "showing the 200 largest locations". |
| neither | no highlights; the card says the result is not location-based |

Fixed tools set `reason` and `severity` from their rule, for example `low_stock` with `warning`. DSL results use `reason = query` and `severity = info`.

## 8. Fixed tools and their definitions

Each fixed tool is a thin, typed wrapper around semantic functions. Tools never contain business rules of their own.

| Tool | Semantic definition used |
|---|---|
| `find_product_locations` | stock (4.1) by bin for one product |
| `get_bin_contents` | stock by product for one bin, plus `last_movement_at` |
| `get_low_stock` | `LOW_STOCK` rule (6) |
| `get_top_movers` | `demand_units` over a range, sorted descending |
| `get_dead_stock` | `DEAD_STOCK` rule (6) |
| `get_zone_summary` | stock and `utilization_pct` by zone |
| `get_order_stockout_risks` | `STOCKOUT_RISK` rule with H as a parameter |
| `get_delayed_orders` | delayed order (4.5) |
| `get_capacity_hotspots` | `CAPACITY_HOTSPOT` rule (6) |
| `get_festival_risks` | festival projection (5) |
| `query_inventory` | the DSL (7) |
| `propose_replenishment`, `propose_cycle_count` | read the evidence through the same functions as `pw_agent_read`, then insert a `PENDING` proposal through a separate connection as `pw_proposal_writer` (CWM 11.3). The read role never writes; the write role never reads raw tables. |

`get_festival_risks` is added to the approved tool list so that the festival question has a dedicated, testable path rather than relying on the DSL. The tool registry spec will hold the authoritative list.

## 9. Performance budget

At the v1 data volumes (CWM 12), on the reference environment (NFR-PERF-07):

| Call | p95 budget |
|---|---|
| any single-product or single-bin function | 50 ms |
| site-wide snapshot functions (all products) | 300 ms |
| festival projection, all products | 500 ms |
| full exception evaluation, all types | 2 s |
| DSL query | 1 s, hard timeout 3 s |

## 10. Tests required by this spec

| Test | Expected result |
|---|---|
| Definition fixtures | Each definition in section 4 has a hand-calculated fixture that passes |
| Festival fixtures | Section 5.4 table passes exactly, plus one fixture per edge rule in 5.3 and one per outcome in 5.2 (FR-FES-05); missing and explicit-zero uplift are reported differently |
| Average demand | A window with demand on a non-operating date divides total demand by W; a window with no picks gives 0 |
| Stockout fixability | D = 30, P = 10, S = 25: shortfall 20, reserve 15, true shortage of 5. D = 30, P = 10, S = 40: fixable. |
| Slow in prime | A slow product in a prime bin is raised; a fast product with idle stock in a second prime bin is not |
| Capacity | A zone over its weight capacity but under its volume capacity is reported by weight; a zone with no active bins is null |
| Mixed measures | `on_hand` with `demand_units` evaluates stock at `as_of` and demand over the range |
| Last pick | A reversal after the last pick does not change `days_since_last_pick` |
| Working set | Narrowing with no previous result is rejected; identifiers beyond 500 are not stored |
| Score fixture | Section 6.1 example gives 57.2 |
| Reference agreement | SQL and Python reference implementations agree on all fixtures and on 1,000 randomized cases |
| Exception rules | Each type has a fixture that raises it and one that resolves it |
| Consistency | For one `as_of` and settings version, the same number is identical in a tool result, the exception engine, the brief and the map state |
| Single `as_of` | All values in one answer carry the same `as_of` and settings version |
| DSL validation | Each rejection rule in 7.4 has a test; unknown fields, incompatible dimensions and oversized limits are rejected |
| No SQL from the model | DSL compilation uses only bound parameters; a test feeds SQL fragments as values and confirms they are treated as literals |
| Highlight derivation | Highlights match section 7.6 for each result shape |
| Role | Semantic functions work as `pw_agent_read` and fail on raw tables |
| Performance | Section 9 budgets met on the v1 dataset |

## 11. Decisions for review

| # | Decision |
|---|---|
| 1 | "Not moved" means not picked; internal transfers do not count (4.2) |
| 2 | Storable stock excludes staging, receiving and returns bins (4.1) |
| 3 | Averages use operating days, today excluded; zero-demand operating days count (3, 4.2) |
| 4 | Cover and projection use on-hand, not available, to avoid counting allocated orders twice (4.3) |
| 5 | Festival shortage includes a stock-out on the festival day itself. This sharpens FR-FES-03, which says "before the next festival". |
| 6 | Festival exceptions only within a 21-day lookahead; the copilot can still answer about later festivals |
| 7 | Stockout risk is one rule with a "fixable by replenishment" flag, and the morning replenishment question reuses it with a shorter horizon |
| 8 | Impact score normalization: linear caps for lines and urgency, logarithmic cap for value (6.1) |
| 9 | One result envelope for every tool and the DSL, with highlights derived by the semantic layer (7.5, 7.6) |
| 10 | `get_festival_risks` added to the fixed tool list (8) |
| 11 | Semantic model file is changed only through the repository with tests and evaluations, not by Admin at runtime in v1 |
| 12 | Average demand keeps non-operating-date demand in the numerator and divides by W operating days, so real demand is never dropped (4.2). Review suggestion to exclude it was not adopted, with reason. |
| 13 | `SLOW_IN_PRIME` is a product-level rule with linked prime bins; bin-level idleness is a separate measure (6, 4.2) |
| 14 | Festival projection has three outcomes, including `UNKNOWN_BEYOND_DATA`, never a bare "no risk" (5.2) |
| 15 | DSL allows mixing snapshot and flow measures; `time_range` applies only to flow measures (7.1) |

## 12. Change log

| Date | Change |
|---|---|
| 2026-11-18 | First draft. |
| 2026-11-18 | Revision 2 after review: average-demand rationale; exact stockout fixability formula; `SLOW_IN_PRIME` subject and bin-level last-pick measure; weight-aware zone and rack utilization and null handling; three festival outcomes; mixed snapshot and flow measures; last pick ignores reversals; operating-days label; missing versus zero uplift; evaluator and proposal write paths; cap and weight validation; catalogue-only name resolution; unknown values per dimension; working set rules; deterministic highlight truncation; new tests. |
