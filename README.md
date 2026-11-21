# PalletWise

A warehouse copilot on a live digital twin. Ask a question in plain language. The answer comes
from the warehouse database through governed tools, and the locations it is about light up on a 3D
(or 2D) map of the warehouse.

One warehouse is simulated end to end: products, zones, racks and bins, customers, orders,
allocations, picks, receipts and 180 days of stock movements.

![PalletWise system architecture](docs/architecture/containers.svg)

## Run it (Windows, macOS, Linux)

You need **Docker Desktop** (or Docker Engine with Compose v2) with about **6 GB of memory** for
Docker. Nothing else is needed: no Python, Node or `make`.

```bash
git clone <this repository> palletwise && cd palletwise

# 1. Create .env with fresh random secrets and the demo sign-in cards
python scripts/setup.py
#    No Python? The same script in a container (PowerShell, bash and zsh alike):
docker run --rm -v "${PWD}:/w" -w /w python:3.13-alpine python scripts/setup.py

# 2. Build and start the stack: migrations, calendar, 180-day simulation, ingest (about 5 minutes the first time)
docker compose --env-file infra/images.env --env-file .env -f infra/compose.yaml up -d --build --wait
```

With `make` installed, `make setup` and `make up` do the same.

Open **http://localhost:8080**. The sign-in page shows two demo account cards, **Manager** and
**Admin**: click one to fill in the form (the password is filled, never shown), then sign in. The passwords are the random ones that
setup wrote into `.env` (`KC_DEV_MANAGER_PASSWORD`, `KC_DEV_ADMIN_PASSWORD`). They are generated
on your machine and never committed.

| What | Where |
|---|---|
| PalletWise | http://localhost:8080 |
| API documentation | http://localhost:8080/api/docs |
| Keycloak (development realm) | http://localhost:8180 |

**The model.** The copilot uses Gemini on Vertex AI. Set `GCP_PROJECT_ID` and `GCP_SA_FILE` (a
service account key file outside the repository) in `.env`. Without them everything else runs, and
the copilot still answers the suggested and known questions with template wording.

**Troubleshooting.** A 502 on port 8080 in the first minutes means Keycloak is still starting
(it can take two minutes on a laptop under memory pressure); `--wait` returns once it is healthy.
`docker compose ... down` stops the stack; the data volume is kept.

## Try it

1. Sign in as **Manager**. The daily brief and the ranked exception inbox are on the right, the
   warehouse on the left, and the figures strip under the map shows units, stocked locations,
   active zones and open alerts.
2. Ask the assignment's questions, or click the suggestion chips:
   - *Show me the top 5 fastest-moving items.* Their pick locations light up.
   - *Which items are running low on stock?* The low pick faces light up.
   - *Where is product SKU-102 stored?* Every location holding it is pinned and framed.
   - *Which warehouse zones have the highest stock levels?* The zones are outlined.
   - *Show me items that have not moved in the last 30 days.* Dead stock lights up.
   - Follow-ups use the previous answer: *Of those, which are in zone B?*
3. Switch **Heatmap** on: racks are coloured by units picked in the last 7 days, in 3D and 2D.
   The focus button beside 3D and 2D gives the map its whole panel; drag the bar between the
   map and the copilot to resize them, or click **Copilot** in the top bar to hide it.
   Ask by voice with the microphone in the question box (the words appear as you speak), and
   press **Listen** on an answer to hear it read aloud.
4. Open **Demo**: the guided storyline adds a large outlet order and moves time forward 4 hours,
   then a week. The inbox, the map and the brief change live, with no reload. **Reset** returns to
   the seed.
5. Sign in as **Admin** and open **Admin**: the copilot's status and kill switch, usage and
   estimated cost, unanswered questions, and the audit log of every tool call with the agent's
   identity (CSV export).

## How a question becomes highlighted locations

![From a question to highlighted locations](docs/architecture/question-flow.svg)

1. **Ask.** The browser posts the question with its session cookie and CSRF token. The browser
   never holds an access token.
2. **Check.** The API checks the session, the rate limit, that the data is ready and the agent is
   active, then exchanges the user's token for the copilot's own token, on behalf of the user.
3. **Route.** Known phrasings, suggestion chips and exact identifiers (a SKU, a bin, an order
   number) go straight to a tool (the fast path). Anything else goes to the model, which only
   chooses a tool and its arguments.
4. **Execute.** Every call goes through `tools.executor`: argument validation, an Open Policy Agent
   decision (role, site, agent scopes), the kill switch, and an audit record naming the agent, its
   version, the model, the prompt version, the run and the user.
5. **Compute.** The tool calls one semantic-layer function at the site's clock. It returns rows,
   the bins, racks and zones to highlight, and the evidence of how it was computed.
6. **Verify.** The model words the answer from the rows. Every number, SKU, bin and zone in the
   wording must appear in those rows; if not, a template answer is shown instead.
7. **Highlight.** The result streams to the browser over SSE. The answer text, the table and the
   map highlights come from the same result envelope, so they cannot disagree.

### Answer layers

| Layer | What answers | Model's part |
|---|---|---|
| Fast path | Known questions, chips, exact identifiers | Wording only, or none (template) |
| Specific tools | Low stock, fast movers, dead stock, slow stock in prime locations, zone summary, capacity hotspots, delayed orders, stockout risks, order status, open exceptions, product locations, bin contents | Picks the tool |
| Query language | `query_inventory`: approved measures, dimensions and filters, compiled and validated by code | Picks measures and filters |
| Multi-step | Up to five tool calls; follow-ups on the previous answer's identifiers | Plans the calls |
| Governed SQL | Off: failed its evaluation gate (see below) | Would write one read-only `SELECT` |
| Honest decline | Says what it cannot answer and offers two or three things it can | None |

## Data path

![Data path, one database role per step](docs/architecture/data-flow.svg)

PalletWise never reads the simulator's tables. The simulator writes ordered records (master data,
orders, allocations, movements, time markers) to an outbox. The connector applies them through
invariant-keeping database functions as `pw_ingest`. A real WMS replaces the simulator with an
adapter that produces the same record stream from its API, change data capture or exports. Nothing
after the connector changes. A real site's clock is real time, and the simulation controls are
refused for it.

- **`pw` schema:** the canonical warehouse model, with forced row-level security on every tenant
  table and a movement ledger.
- **`sem` schema:** the semantic layer. Every business calculation lives here, once. The map, the
  copilot, the brief and the inbox all read it, and an evaluation check proves they agree.
- **`gq` schema:** read-only governed views for the governed SQL tier.
- **The seed is deterministic.** The same seed always produces the same data; its dataset hash is
  checked against `services/simulator/golden/` on every load.

### Connecting a third-party WMS

PalletWise integrates through a **connector**, not through endpoints that the WMS must call. It
works in three layers, and only the first is written per WMS:

| Layer | Per WMS? | What it does |
|---|---|---|
| **Adapter** | Yes, one per WMS product | Reads the WMS the way that WMS allows: its REST or OData API, webhooks, change data capture on its database, or scheduled exports. Maps its codes, units, locations and statuses to PalletWise's canonical records. |
| **Connector contract** | No, fixed | `pull(after_sequence, limit)` returns the next records in order; `submit_task(task, idempotency_key)` and `get_task_result(key)` send approved work back; `health()` reports status. Records are master data, orders, allocations, movements, task results and time markers, each with a sequence number, the source system and the WMS's own id. |
| **Connector worker and ingest** | No, fixed | Applies records strictly in order, in batches, as `pw_ingest`, through invariant-keeping database functions. Ingest is idempotent on natural keys, so a replay creates nothing twice, and the cursor commits with each batch. |

Above the connector nothing changes: the semantic layer, the copilot, the map and the inbox work
as they are. The adapter holds the only WMS credentials, read only except for task submission.
The warehouse layout (zones, racks, bins and their positions for the 3D map) comes in as master
data, or from a one-time layout file when the WMS does not hold coordinates.

Today the one adapter is the simulator (`services/simulator`), which feeds PalletWise through
exactly this contract (`docs/specs/simulator.md` section 2). A real WMS adapter is the next
integration step; it does not need changes to the core.

## Security model

| Concern | How |
|---|---|
| Identity | Keycloak (OIDC). The API is a backend for frontend: sessions are server side and the browser holds an opaque cookie. Passwords stay with the identity provider; the sign-in page is a PalletWise Keycloak theme. |
| The agent's identity | Token exchange: the copilot acts as itself on behalf of the user, with the intersection of the user's and the agent's scopes. Every action is traceable to agent id and version, run and user. |
| Authorization | Open Policy Agent decides every request and every tool call. |
| Data isolation | Forced row-level security; one database role per write path (`pw_app`, `pw_ingest`, `pw_agent_read`, `pw_evaluator`, `pw_brief`, `pw_admin_read`, `pw_query_read` and more). None can bypass RLS. |
| The model's reach | Tools only, through `tools.executor`. No credentials, no database access, no SQL in the live product. Inputs are length limited and screened; outputs are grounded; markdown is sanitized under a strict content security policy. |
| Kill switch and budget | The Admin suspends the copilot at once. A daily token budget degrades answers to templates instead of failing. |
| Audit | Every tool call, refusal, time shift, reset and admin change, in a hash-chained log. |
| Secrets | None in the repository, logs or traces. Setup generates them per machine. |

## Decisions

Architecture decisions are in [`docs/adr/`](docs/adr/); slice decisions are in each
`docs/specs/slice-N-acceptance.md`.

| Decision | Why |
|---|---|
| The semantic layer owns every calculation; the model never computes | Answers are reproducible and checkable; the model cannot invent a number |
| Every tool call through one executor (policy, kill switch, audit, tracing) | One enforcement point, no shortcuts |
| Backend for frontend with server-side sessions | No tokens in the browser |
| Token exchange for the agent, scopes intersected with the user's | The copilot can never do more than its user |
| One database role per write path, forced RLS | A bug in one path cannot write elsewhere or across tenants |
| Simulator behind a connector, not a seed script | The same path a real WMS uses; time and scenarios are real data |
| ADR 0001: one gate for quality, latency and cost together | Improving one never hides a regression in another |
| ADR 0002: governed SQL as a last-resort layer, behind an evaluation gate | More questions answered without giving the model the database |
| Fast path before the model | Known questions answer in milliseconds and work with no model at all |
| Working sets carry provenance and re-query current data (Slice 2) | Follow-ups never answer from stale values |
| Input classifier is an extra layer, never the boundary (Slice 2) | A classifier can be wrong; OPA, validation and grounding are the boundary |
| Live site stream over SSE with a durable per-site change sequence (Slice 3) | Map, inbox and brief update live; a lost event triggers a full refetch |
| Exceptions evaluated once per state change, ranked by impact (Slice 3) | The inbox ranks by business impact, not by when something happened |
| One daily brief per site and day, stored (Slice 3) | One model call a day, the same brief for everyone, template without a model |
| Search is a read endpoint, not a copilot tool (Slice 3) | Instant and exact; choosing a result runs the copilot through the executor |
| The simulator owns time; a time shift, not a running clock (Slice 4) | The presenter controls the demo; nothing changes under them |
| Reset and time controls for the Manager, on simulated sites only (Slice 4) | A demo recovers from one screen; a real site has nothing to reset |
| One operation at a time per site (Slice 4) | A shift, a scenario step and a reset can never interleave |
| Governed tier stays off until its gate passes (Slice 5) | An unevaluated tool measurably degraded routing and latency |

## Governed SQL tier: built, evaluated, failed its gate

The sixth answer layer (ADR 0002) lets the model write one read-only `SELECT` over governed views
for questions no tool covers. It is built and tested: a PostgreSQL 17 parser guard (`pglast`) with
a whole-tree allowlist and declared join and measure rules, a read-only role limited to the `gq`
views, hard limits (2 s, 200 rows, 20 columns, 256 KB, a cost cap), an admin switch, and audit
records with the SQL hash and the agent's identity.

The rule is that the model is offered the tool only after it passes the evaluation gate for the
deployed model and prompt. **It did not pass.** Over four gate runs on Gemini Flash-Lite:

| Measure | Result | Gate |
|---|---|---|
| Correct answers on governed questions | 0.73 | 0.90 |
| Invalid queries refused | 0.978 | 1.0 |
| Prompt injection, grounding, routing safety | 100% | 100% |

Safety held; SQL accuracy did not. The cause is the model's SQL accuracy on this catalog, not the
guard or the architecture. The tier is therefore **off** (`semantic/governed_gate.json` lists no
passed configuration), and the copilot answers through the five other layers. It can be retried
with a stronger model or a revised prompt; a pass is recorded in the gate file with its report.

## Quality

| Command | Runs |
|---|---|
| `make lint` | ruff, pyright (strict), import boundaries, ESLint, Prettier, tsc |
| `make test` | unit tests, integration tests against real PostgreSQL 17 and OPA, policy tests |
| `make evals` | the deterministic evaluation gate |
| `make evals-model` | the model gate: three runs per case on the real model, for quality, latency and cost |
| `make storyline` | the guided demo, clicked through in a browser twice |

Evidence per slice (evaluation reports, performance, browser checks) is in
[`docs/quality/`](docs/quality/).

## Known limits

- The governed SQL tier is off (above).
- Proposals and task execution, the full admin panel (users, settings) and audit sealing are
  specified but not built.
- Play, pause and clock speeds are not built; time moves by explicit shifts.
- Voice uses the browser's own speech services. In Chrome and Edge, speech recognition is done by
  the browser vendor's online service; Firefox has no speech recognition, so the microphone is
  hidden there.
- On an 8 GB laptop the full stack runs close to its memory limit; close other applications for a
  live demo.

## Repository

```
apps/web            single-page app: map, copilot, inbox, admin, simulation controls
services/api        API, agent runtime, tools, semantic layer client, workers
services/simulator  simulated WMS
db                  SQL and migrations
policy              Open Policy Agent policies and tests
evals               evaluation sets, references and gates
docs                product, specifications, ADRs, architecture diagrams, quality evidence
infra               compose stack, Keycloak realm and theme, Traefik
scripts             first-time setup
```

Diagrams are generated: `python docs/architecture/build_diagrams.py`.
Contributor rules: [AGENTS.md](AGENTS.md).
