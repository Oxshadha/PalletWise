# PalletWise: Requirements (v1)

| | |
|---|---|
| Status | Approved (2026-11-18) |
| Owner | Product owner |
| Last updated | 2026-11-18 |
| Related | [vision.md](vision.md), [personas.md](personas.md), [canonical-warehouse-model.md](../specs/canonical-warehouse-model.md), [semantic-layer-and-query-dsl.md](../specs/semantic-layer-and-query-dsl.md) |

## 1. How to read this document

- Each requirement has an ID: `FR-<area>-<nn>` for functional, `NFR-<area>-<nn>` for non-functional.
- **Must** means required for v1. **Should** means planned for v1 but can move to v1.1 without breaking the product.
- Each requirement has acceptance criteria that can be tested. A requirement is done only when its tests exist and pass.
- Requirements say *what* the product does. The specs listed above say *how*.
- Performance and quality numbers marked *initial target* are set now and recalibrated once, after the first baseline measurement. Any later change is recorded in the change log of this document.

## 2. Functional requirements

### 2.1 Authentication and session (AUTH)

| ID | Pri | Requirement | Acceptance criteria |
|---|---|---|---|
| FR-AUTH-01 | Must | Users sign in through the identity provider. | Unauthenticated requests to any page or API return a redirect or 401. Sign-in completes through the identity provider and lands on the role's home screen. |
| FR-AUTH-02 | Must | The browser holds only a session cookie, never access or refresh tokens. | Cookie is httpOnly, Secure, SameSite. No token appears in browser storage, URLs or JavaScript-readable responses. |
| FR-AUTH-03 | Must | Each user has exactly one v1 role: `warehouse_manager` or `admin`. | Role comes from verified identity-provider claims. A user with no recognised role is denied with a clear message. |
| FR-AUTH-04 | Must | Sessions expire after inactivity and can be ended by signing out. | Idle timeout is configurable. Sign-out invalidates the server session. |

### 2.2 Warehouse map (MAP)

| ID | Pri | Requirement | Acceptance criteria |
|---|---|---|---|
| FR-MAP-01 | Must | The map is generated from location data, never hand-drawn. | Adding, moving or deactivating a bin in the data changes the map without code changes. |
| FR-MAP-02 | Must | One scene with a 2D (top-down) and a 3D view, switchable at any time. | Switching keeps the current highlights, selection and focus. |
| FR-MAP-03 | Must | Bins show their state: normal, highlighted, low stock, empty, over capacity, has a pending proposal. | Each state uses colour plus a pattern or icon, so colour is never the only signal. A legend is always available. States are layered so several can show at once: base fill for the most severe stock state (empty, low stock, over capacity, normal), a badge for a pending proposal, and an outline for an answer highlight. |
| FR-MAP-04 | Must | Highlights come only from structured tool results through the visualization contract. | Every highlighted bin or zone has a matching entry in the answer's result. A test fails if the map highlights anything not in the result. |
| FR-MAP-05 | Must | Highlights carry a reason and severity, and the camera focuses on them. | Different reasons (for example low stock and fast-moving) are visually distinct. The view moves to frame the highlighted area. |
| FR-MAP-06 | Must | Selecting a bin opens its details: products, quantities, capacity, last movement. | Details match the database for that bin. |
| FR-MAP-07 | Must | Selecting a bin offers "Ask about this bin", which gives the copilot that context. | The copilot request carries the bin as page context. The answer is about that bin. |
| FR-MAP-08 | Must | The map updates live when inventory changes. | A change in the data is visible on the map within the live-update target (NFR-PERF-05) without a page reload. |

### 2.3 Copilot (COP)

| ID | Pri | Requirement | Acceptance criteria |
|---|---|---|---|
| FR-COP-01 | Must | Users ask questions in natural language. | All example questions in [personas.md](personas.md) are answered correctly in the evaluation suite. |
| FR-COP-02 | Must | Answers are produced only through the five answer layers: fast path, specific tools, the query language, multi-step combination (at most 5 steps), or an honest decline. | Every answer's trace shows which layer produced it. No answer contains data that did not come from a tool result. |
| FR-COP-03 | Must | The model never writes or runs SQL. | Automated test: no code path sends model output to the database as SQL. Query language input is validated and compiled by application code. |
| FR-COP-04 | Must | Answers are shown as answer cards: headline result, map highlights, a short table, how it was computed, and data freshness. | Each card shows the data timestamp and a "how computed" explanation drawn from the metric definitions. |
| FR-COP-05 | Must | Every number in an answer's text appears in the tool results. | Output check rejects or corrects answers with numbers not present in the results. Covered by evaluation cases. |
| FR-COP-06 | Must | Follow-up questions use the previous result ("of those, which are in zone B?"). | Evaluation cases with follow-ups pass. The working set holds only the previous result's identifiers, never values or model memory. Follow-ups re-query current data for those identifiers, so values are always current. If an identifier no longer matches (for example the bin was emptied), the answer says so. A simulation reset invalidates working sets. |
| FR-COP-07 | Must | After each answer the copilot suggests next questions and actions, generated from the result type and page context. | Every suggestion maps to a supported tool or query. Selecting a suggestion never leads to a decline. |
| FR-COP-08 | Must | Questions the system cannot answer are declined honestly, with two or three related things it can do. | Out-of-scope and unsupported evaluation cases are declined. Declined questions are logged for the Admin view (FR-ADM-07). |
| FR-COP-09 | Must | Answers stream, and highlights appear as soon as the tool result is ready, before the text finishes. | Measured by NFR-PERF-02. |
| FR-COP-10 | Must | Users can stop an answer in progress. Sending a new question cancels the previous one. | The server stops work for a cancelled request. No duplicate answers appear. |
| FR-COP-11 | Must | Conversations are stored and can be continued after reload. | History shows previous questions, answers and highlights. Each user sees only their own conversations. |
| FR-COP-12 | Must | Text stored in the data (product names, notes) never changes the copilot's behaviour. | Indirect prompt-injection evaluation cases pass at 100 percent. |
| FR-COP-13 | Must | Text shown in the interface, including generated answers, contains no em dashes or en dashes. | Output formatting replaces them. This is a normalization step, never a reason to reject an otherwise correct answer. A UI test scans rendered text. |
| FR-COP-14 | Must | Users can rate an answer (helpful or not helpful) with an optional comment. | Feedback is stored with the trace ID. Negative feedback is available to Admin as a candidate for the evaluation set. |

### 2.4 Daily brief (BRF)

| ID | Pri | Requirement | Acceptance criteria |
|---|---|---|---|
| FR-BRF-01 | Must | On the first visit of the (simulated) day, the Manager sees a brief with at most five items: top three exceptions, orders at risk today, yesterday's throughput against the 7-day average, proposals awaiting approval, data freshness. | All numbers are computed by application code. The model only writes the wording. |
| FR-BRF-02 | Must | Each brief item links to its detail on the map or in the inbox. | Selecting an item highlights the related locations. |
| FR-BRF-03 | Must | The brief is generated once per Manager per day and reused. | A second visit on the same day does not call the model again. |

### 2.5 Exceptions inbox (EXC)

| ID | Pri | Requirement | Acceptance criteria |
|---|---|---|---|
| FR-EXC-01 | Must | The system detects these exception types: stockout risk for open orders, delayed orders, low stock against the replenishment policy, dead stock, dead or slow stock in prime pick locations, capacity hotspots, festival shortage risk. | Each type has a deterministic rule in the semantic layer and test fixtures that trigger it. |
| FR-EXC-02 | Must | Exceptions are ranked by an impact score built from order lines affected, hours until due and value at risk. | Each exception shows "why it is ranked here" with the score components. Each component is normalized to a 0 to 1 scale before the Admin weights are applied, so no component dominates by magnitude alone. The normalization is defined in the semantic-layer spec. |
| FR-EXC-03 | Must | Exceptions update live and resolve automatically when their condition clears. | Fixing the condition in the data removes or resolves the exception within the live-update target. |
| FR-EXC-04 | Must | The Manager can acknowledge an exception. | Acknowledgement is recorded with user and time and is audited. |
| FR-EXC-05 | Must | From an exception the Manager can open it on the map or ask the copilot to draft a proposal. | Both actions carry the exception as context. |

### 2.6 Festival projection (FES)

| ID | Pri | Requirement | Acceptance criteria |
|---|---|---|---|
| FR-FES-01 | Must | A business calendar holds operating days, paydays, festivals and festival ramps, loaded from `calendar.csv`. | Loaded rows match the source file. The calendar is reference data shared by all tenants of the same region. |
| FR-FES-02 | Must | Projected days of cover use trailing average daily demand, scaled by the category uplift and the calendar's festival ramp, applied separately for each projected day because the ramp changes day by day. | Calculation is in the semantic layer as the measure `projected_days_of_cover`. Unit tests cover a known example by hand. |
| FR-FES-03 | Must | Items whose projected stock-out falls before the next festival are reported as festival shortage risk. | "Which items could run short before the upcoming festival?" passes in the evaluation suite with correct highlights. |
| FR-FES-04 | Must | Festival answers state their assumptions: trailing window, uplift used, festival date. | Shown on the answer card. The model cannot change the calculation, only explain it. |
| FR-FES-05 | Must | The semantic-layer spec defines, with a hand-calculated fixture for each: the trailing window basis (calendar or operating days), treatment of zero-demand days, overlapping festivals, categories with no configured uplift, the scope of uplift settings, and projections that reach the end of the calendar. | Each rule has a passing fixture test. |

### 2.7 Proposals (PRP)

| ID | Pri | Requirement | Acceptance criteria |
|---|---|---|---|
| FR-PRP-01 | Must | The agent can draft two proposal types: replenishment (move stock from a reserve bin to a pick bin) and cycle count (count a bin). | Drafts are created only through the propose tools, only for a Manager. |
| FR-PRP-02 | Must | Each proposal stores an evidence snapshot: the data that justified it at the time. | The snapshot is shown on the proposal and kept unchanged. |
| FR-PRP-03 | Must | Proposal states: PENDING, APPROVED, REJECTED, EXPIRED, then for approved proposals SUBMITTED, EXECUTED or FAILED. SUBMITTED means the connector has accepted the task, not that the task has completed. | Only allowed transitions succeed: PENDING to APPROVED, REJECTED or EXPIRED; APPROVED to SUBMITTED; SUBMITTED to EXECUTED or FAILED. Others are refused and logged. PENDING becomes EXPIRED when its expiry time passes on the simulated clock, checked on every read and by a scheduled sweep, and recorded in the audit log as a system transition. |
| FR-PRP-04 | Must | Only a Manager can approve or reject. Rejection requires a reason. | Admin and the agent are refused at the server. |
| FR-PRP-05 | Must | Before approval the system checks whether the underlying stock changed since drafting. | If it changed, the Manager sees the difference and must confirm again. |
| FR-PRP-06 | Must | Pending proposals expire after a configurable time. | Expired proposals cannot be approved. |
| FR-PRP-07 | Must | Every proposal action is idempotent and audited. | Repeating a request does not create duplicates or double transitions. |
| FR-PRP-08 | Must | An approved proposal is submitted as a task to the connector. In v1 the only connector is the simulator. No task is ever sent to a real WMS in v1. | Approval by the Manager is the only trigger for submission. The agent has no tool that approves, submits or executes. Tests confirm the agent cannot reach the submit path. |
| FR-PRP-09 | Must | The connector validates the task against current inventory and capacity before executing it. | A replenishment with insufficient source stock or destination capacity fails with a reason and moves nothing. A cycle count fails if the bin does not exist, is inactive, or already has an open count. |
| FR-PRP-10 | Must | Execution is idempotent, including when the outcome of a submission is unknown. | Each task has an idempotency key, stored with the task and with every movement the task produces. A retry with the same key returns the original result and never moves stock twice. If a submission times out, the task stays SUBMITTED and a reconciliation job asks the connector for the result by idempotency key. It never resubmits blindly. |
| FR-PRP-11 | Must | Failed and stale tasks keep a visible status and reason. | Reasons include: stale (stock changed), insufficient stock, capacity exceeded, connector error. Shown on the proposal and in the audit log. |
| FR-PRP-12 | Must | Inventory and the map change only after successful execution, through the normal data flow from the connector. | Approval alone changes no inventory. The movement appears on the map within the live-update target after the connector reports success. |
| FR-PRP-13 | Must | Exceptions are recalculated from the updated state, never marked resolved because a task was approved or executed. | Test: an executed replenishment that does not fully cover demand leaves the exception open with updated values. |
| FR-PRP-14 | Must | For a cycle count, the connector reports the counted quantity. In v1 the simulator produces the count, with configurable variance for scenarios. Posting an adjustment for a difference is the WMS's responsibility, as in a real WMS. PalletWise records the count result, shows the variance, and receives any adjustment through the normal data flow. | The count result and variance appear on the proposal. The adjustment appears in movement history with the proposal reference. |

### 2.8 Admin (ADM)

| ID | Pri | Requirement | Acceptance criteria |
|---|---|---|---|
| FR-ADM-01 | Must | Users and roles are managed in the identity provider. PalletWise shows users and their roles and links to the identity provider for changes. | The list matches the identity provider. No user management code duplicates the identity provider. |
| FR-ADM-02 | Must | Agent registry: view and change allowed tools and scopes, model selection, step limit, daily token budget, prompt version. | Changes take effect without redeploy and are audited. |
| FR-ADM-03 | Must | Kill switch: suspending the agent stops the copilot immediately with a clear message to users. | New copilot requests, including fast path, are refused within 5 seconds of suspension. The map, inbox and brief numbers keep working, with the brief shown without generated wording. Precedence, checked in this order: (1) agent suspended: all copilot requests refused, no fallback can bypass this; (2) agent active, model unavailable: fast path still works; (3) agent active, token budget reached: fast path still works. |
| FR-ADM-04 | Must | Thresholds: low-stock rules, dead-stock period, exception ranking weights, category festival uplift, proposal expiry. | Each setting shows what it affects. Settings are versioned and every change is audited. Open exceptions are re-evaluated on the next evaluation cycle. Each evaluation records the settings version used, and earlier scores stay in the exception's history. |
| FR-ADM-05 | Must | Audit log viewer with filters (user, agent, tool, time, decision) and CSV export. | Shows the fields in NFR-AUD-02. Export matches the filtered view. |
| FR-ADM-06 | Must | Usage and cost dashboard: requests, tokens, cost, latency, errors, by day, user, tool and model. | Totals match the recorded usage data. Cost is labelled as estimated: provider-reported token usage multiplied by the configured price table. |
| FR-ADM-07 | Must | Unanswered questions view: declined questions with reason and count. | Every decline appears here, grouped by similar question. |
| FR-ADM-08 | Must | Layout and simulated data settings: view the layout, regenerate from seed, set simulator parameters. | Changes are audited. |

### 2.9 Simulation (SIM)

| ID | Pri | Requirement | Acceptance criteria |
|---|---|---|---|
| FR-SIM-01 | Must | The simulator is a data source behind the connector interface, like any WMS. | No agent, tool, authorization or visualization code checks whether data is simulated. |
| FR-SIM-02 | Must | The same seed always produces the same dataset. | Two runs with the same seed and version produce the same dataset hash. |
| FR-SIM-03 | Must | Generated data uses `calendar.csv` for seasonality and `outlets.csv` for outbound customers and delivery windows. Every record carries a dataset version and a synthetic label. | Checked by data tests. |
| FR-SIM-04 | Must | Admin can play, pause, set speed (1x, 10x, 60x), jump to a time and reset to the seed. | The simulated clock drives every time-based metric. Each action is confirmed and audited. |
| FR-SIM-05 | Must | The simulated clock stays within the calendar's date range. | Jumps outside the range are refused with a message. |
| FR-SIM-06 | Must | Scenarios are defined as files that list events, expected exceptions and expected highlights. v1 scenarios: festival rush, outlet window at risk, monsoon slowdown, dead stock in prime locations. | Each scenario runs from the Admin screen and is also run by the evaluation suite. |
| FR-SIM-07 | Should | Guided tour for presentations: step-by-step prompts that suggest the next question. | The tour can be started and left at any step. |
| FR-SIM-08 | Must | The simulator implements the connector's task submission capability, the same contract real WMS connectors implement in v2. | Contract tests run against the simulator. The same tests become the acceptance suite for every real connector. |
| FR-SIM-09 | Must | Clock controls keep all derived state consistent. Jumping forward makes the simulator generate the events of the skipped period. Jumping backward is not allowed: reset, then jump forward. Reset restores the seed dataset, rebuilds all derived state (metrics, exceptions, brief cache), sets open proposals to EXPIRED with reason "simulation reset" and invalidates conversation working sets. | Test: the state after jumping to time T equals the state after playing to T from the same seed. |

## 3. Non-functional requirements

### 3.1 Security (SEC)

| ID | Pri | Requirement | Acceptance criteria |
|---|---|---|---|
| NFR-SEC-01 | Must | Every tool call is authorized at the tool boundary by the policy engine, using the verified user and agent identity. | Tests show denied calls for each role and scope combination. The model cannot bypass the check. |
| NFR-SEC-02 | Must | The agent acts on behalf of the user and never with more rights than the user: effective rights are the user's rights intersected with the agent's allowed scopes. | Tests cover a tool allowed for the user but not the agent, and the reverse. |
| NFR-SEC-03 | Must | Tenant isolation through row-level security, forced on all tenant-owned tables. The application role is not the table owner and cannot bypass it. Tenant context is set per transaction. | Isolation tests run as the application role, including with pooled connections. Isolation tests also cover live event streams, caches (keys include tenant and permission scope), conversation history, exports and background jobs. |
| NFR-SEC-04 | Must | Least-privilege database roles: read tools use a read-only role; propose tools can only insert proposal drafts, through the proposal service; conversation memory uses its own role. No role used on behalf of the agent can change inventory, settings or audit records. | Privilege tests confirm each role can do only what it needs. |
| NFR-SEC-05 | Must | No secrets in the repository. Deployed environments use workload identity. | Secret scanning in CI. |
| NFR-SEC-06 | Must | Model output is rendered safely: sanitized markdown, no raw HTML, strict content security policy. | Tests with malicious content in data and output. |
| NFR-SEC-07 | Must | Rate limits per user and per tenant on copilot and API requests. | Exceeding the limit returns 429 with a retry time. |
| NFR-SEC-08 | Must | A threat model maps each threat (STRIDE and OWASP LLM Top 10) to a control and a test. | Every listed control has a passing test. |

### 3.2 Privacy (PRV)

| ID | Pri | Requirement | Acceptance criteria |
|---|---|---|---|
| NFR-PRV-01 | Must | Personal data is masked before it leaves the application for tracing. | Tests confirm masked fields in exported traces. |
| NFR-PRV-02 | Must | Full prompt and response capture is enabled only in development. | Configuration test per environment. |
| NFR-PRV-03 | Must | Data retention for conversations, traces and audit is configurable. | Expired data is removed by a scheduled job. Audit retention respects legal hold settings. |

### 3.3 Audit (AUD)

| ID | Pri | Requirement | Acceptance criteria |
|---|---|---|---|
| NFR-AUD-01 | Must | The audit log is append-only and tamper-evident (hash chain per tenant). | The application role has no update or delete rights on audit tables. A verification job detects a modified record. |
| NFR-AUD-02 | Must | Each entry records user, role, agent and version, model, prompt version, tool, parameters, policy decision, row count, latency, tokens, cost and trace ID. | Checked by tests on representative actions. |
| NFR-AUD-03 | Must | Audited actions include sign-in, every tool call, every proposal transition, exception acknowledgement, every Admin setting change, kill switch, and every simulation control action. | Each listed action produces an entry. |
| NFR-AUD-04 | Must | Retention and tamper evidence work together. The hash chain is split into sealed periodic segments, each ending in a checkpoint record. Retention removes only whole expired segments, oldest first, never single records, and keeps their checkpoints. Legal hold blocks removal. | Verification succeeds after an expired segment is removed, starting from the retained checkpoint. Removing a single record is detected. |

### 3.4 Performance (PERF), initial targets

| ID | Pri | Requirement | Acceptance criteria |
|---|---|---|---|
| NFR-PERF-01 | Must | Fast path answer with highlights: p95 at most 1.5 s. | Measured by load test on the reference environment. |
| NFR-PERF-02 | Must | Per routing tier (ADR 0001), each milestone measured independently and never averaged across tiers. Tiers 1 and 2 (no model chooses the tool): first map highlights p95 at most 1 s, first headline p95 at most 3 s, complete answer p95 at most 3 s. Tier 3 (the model chooses the tool): first progress p95 at most 1 s, first map highlights p95 at most 7 s, first headline p95 at most 7 s, complete answer p95 at most 8 s. A progress event never counts as the headline. | Gated in the model evaluation run together with quality. |
| NFR-PERF-03 | Must | Map renders the v1 site (about 600 bins) at 60 frames per second on the reference laptop. | Measured in a browser performance test. |
| NFR-PERF-04 | Should | Map stays usable (at least 30 frames per second) with 10,000 bins. | As above, on a generated large layout. |
| NFR-PERF-05 | Must | Live updates appear within 2 s of the data change. | Measured end to end. |
| NFR-PERF-06 | Must | Database queries have a statement timeout and a row limit. | Long-running queries are stopped and reported as an error, never left running. |
| NFR-PERF-07 | Must | Measurements are reproducible. The reference machine (hardware, operating system, browser and version) and the frame-rate method (scripted 30-second pan and orbit) are recorded in the performance test plan before the first measurement. Latency targets are measured with 20 concurrent users, warm caches and the v1 dataset, and reported twice: with the real model provider, and with a stub model to isolate PalletWise's own overhead. | Performance test plan exists in `docs/quality/` and every reported result references it. |

### 3.5 Scalability (SCL)

| ID | Pri | Requirement | Acceptance criteria |
|---|---|---|---|
| NFR-SCL-01 | Must | API instances are stateless and can be scaled horizontally. | Two instances behind a load balancer pass the full test suite. |
| NFR-SCL-02 | Must | The data model supports multiple tenants and sites from v1, even though one is seeded. | Tests create a second tenant and site and confirm isolation. |
| NFR-SCL-03 | Should | Initial capacity target: 50 concurrent users per tenant, 10,000 bins per site. | Load test results recorded. |

### 3.6 Reliability (REL)

| ID | Pri | Requirement | Acceptance criteria |
|---|---|---|---|
| NFR-REL-01 | Must | If the primary model fails, the fallback model is used. If both fail, the map, brief numbers, inbox and fast path still work and the copilot shows a clear message. | Fault-injection tests. |
| NFR-REL-02 | Must | Timeouts on every external call (model, database, identity provider, policy engine). | Tests confirm requests end within their limits. |
| NFR-REL-03 | Must | The agent stops after at most 5 tool steps. | Test with a question that would loop. |

### 3.7 Cost (CST)

| ID | Pri | Requirement | Acceptance criteria |
|---|---|---|---|
| NFR-CST-01 | Must | Token usage and estimated cost are recorded per request, user, tool and model. | Recorded token totals reconcile with provider-reported usage within 5 percent. |
| NFR-CST-02 | Must | The agent's daily token budget is enforced. When it is reached, the copilot falls back to fast path answers and says so. | Test with a low budget. |
| NFR-CST-03 | Should | Estimated model cost per answer p95 at most 0.003 USD, measured and reported in every evaluation (not blocking since 2026-11-20, ADR 0001 amendment). Every model call of the answer counts, including fallback, hedged, discarded and cancelled calls; a call cancelled before usage is reported is charged an upper bound (ADR 0001). | Reported in every model evaluation run beside the quality and latency gates. |

### 3.8 Observability (OBS)

| ID | Pri | Requirement | Acceptance criteria |
|---|---|---|---|
| NFR-OBS-01 | Must | One trace follows each request through the API, agent, model calls, policy checks, tools and database. | Trace for a sample question shows all spans with GenAI attributes. |
| NFR-OBS-02 | Must | Metrics: time to first text, time to first highlight, total latency, tokens, cost, tool errors, guardrail triggers, declines, feedback. | Visible in the observability backend and the Admin usage dashboard. |
| NFR-OBS-03 | Must | The trace ID is recorded in the audit entry and shown in error messages for support. | Checked by tests. |

### 3.9 Answer quality (QLT), initial targets

| ID | Pri | Requirement | Acceptance criteria |
|---|---|---|---|
| NFR-QLT-01 | Must | An evaluation suite covers data questions, follow-ups, page context, out-of-scope, unsupported, direct and indirect prompt injection, and every scenario. | The question set is versioned and its hash is recorded before each reported run. |
| NFR-QLT-02 | Must | CI blocks a merge if: tool and parameter accuracy is below 95 percent, highlight F1 is below 0.95, correct-decline rate is below 90 percent, or any prompt-injection case fails. | Gate runs on every change to prompts, tools, the semantic layer or model settings. |
| NFR-QLT-03 | Must | Before the model choice is locked, the Gemini Flash candidates available through the chosen Vertex AI deployment and region (currently 3.8 Flash and 3.5 Flash) are compared on the suite. | Comparison report in the repository records model ID, region, availability, quality, latency and cost. |

### 3.10 Code quality and delivery (COD)

| ID | Pri | Requirement | Acceptance criteria |
|---|---|---|---|
| NFR-COD-01 | Must | Strict linting and type checking for Python and TypeScript. | CI fails on any error. |
| NFR-COD-02 | Must | Module boundaries are enforced: for example, the agent cannot import database code directly. | Boundary checks in CI. |
| NFR-COD-03 | Must | Test coverage of at least 80 percent for domain, semantic layer, tools and policy code. | Coverage report in CI. |
| NFR-COD-04 | Must | Frontend types are generated from the API contract and checked for drift. | CI fails if the contract and generated types differ. |
| NFR-COD-05 | Must | Database changes go through versioned migrations. | CI applies all migrations to an empty database and runs the tests. |
| NFR-COD-06 | Must | Any change to an approved architecture decision is recorded as an ADR and approved by the product owner before implementation. | Reviewed in pull requests. |
| NFR-COD-07 | Must | The whole system starts locally with one command and a seeded dataset. | Verified on a clean machine by following the README. |

### 3.11 Usability and accessibility (UX)

| ID | Pri | Requirement | Acceptance criteria |
|---|---|---|---|
| NFR-UX-01 | Must | Workspace and Admin screens meet WCAG 2.2 AA. | Automated accessibility checks plus a manual keyboard pass. |
| NFR-UX-02 | Must | All workspace actions are possible with the keyboard. | Manual test script. |
| NFR-UX-03 | Must | Answers lead with the result. Detail and evidence are one click away. | Design review against this rule. |
| NFR-UX-04 | Must | v1 is designed for desktop screens at 1920x1080, in English, and remains fully usable down to a minimum of 1366x768. | Layout tests at 1920x1080 (primary) and 1366x768 (supported minimum). |

## 4. Open questions

None at this time.

Resolved: Q1, what happens to an approved proposal in v1. Decision (2026-11-18): approved proposals are submitted to the connector and executed by the simulator only. Approval authorizes a simulated operational task, not unrestricted agent execution. See FR-PRP-08 to FR-PRP-14 and FR-SIM-08.

## 5. Change log

| Date | Change |
|---|---|
| 2026-11-18 | First draft. |
| 2026-11-18 | Q1 resolved: human-approved execution against the simulator in v1. Added FR-PRP-09 to FR-PRP-14 and FR-SIM-08, extended FR-PRP-03 states. |
| 2026-11-18 | Review round: SUBMITTED meaning and expiry trigger (FR-PRP-03), cycle-count validation (FR-PRP-09), unknown-outcome reconciliation (FR-PRP-10), cycle-count responsibility (FR-PRP-14), settings versioning (FR-ADM-04), kill-switch precedence (FR-ADM-03), estimated cost (FR-ADM-06, NFR-CST-01), working-set rules (FR-COP-06), answer feedback (FR-COP-14), map state layering (FR-MAP-03), score normalization (FR-EXC-02), per-day ramp (FR-FES-02), projection rules (FR-FES-05), clock consistency (FR-SIM-09), least-privilege roles for propose tools (NFR-SEC-04), wider isolation tests (NFR-SEC-03), audit retention segments (NFR-AUD-04), measurement conditions (NFR-PERF-07), Vertex-available model comparison (NFR-QLT-03), screen sizes (NFR-UX-04). |
| 2026-11-19 | ADR 0001 (balanced answer runtime), approved by the product owner: NFR-PERF-02 replaces "first text p95 at most 2 s" with four independent milestones (first progress, first map highlights, first headline, complete answer); new NFR-CST-03, a 0.003 USD p95 cost limit per answer. |
| 2026-11-20 | Product owner: today's cost is acceptable; quality and latency gate. NFR-CST-03 becomes Should, measured and reported, not blocking (ADR 0001 amendment). |
| 2026-11-20 | NFR-PERF-02 amended by the product owner after measurement (ADR 0001 amendment 3): tier-specific targets. A model choosing a tool takes 3.5 to 7 s at p95 on its own, so tier 3 highlights and headline move to 7 s; tiers 1 and 2 keep the strict targets. Correctness targets are unchanged. |
| 2026-11-21 | FR-SIM-04 revised by the product owner (slice-4-acceptance D2, D3): time moves on demand by shifts (+1 hour, +4 hours, +1 day, +1 week, the next operating morning, or a chosen time); the Manager and the Admin may shift time and run scenario steps on a simulated site; reset stays with the Admin. Play, pause and speeds are deferred. |
