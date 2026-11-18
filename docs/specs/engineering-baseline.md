# Engineering Baseline

| | |
|---|---|
| Status | Draft (revision 2), awaiting approval |
| Owner | Product owner |
| Last updated | 2026-11-18 |
| Implements | NFR-COD-01 to NFR-COD-07, NFR-SEC-05, NFR-QLT-02, FR-COP-13 (enforcement) in [requirements.md](../product/requirements.md) |

## 1. Purpose

How the code is organised, built, checked and delivered, so every slice is production quality from the first commit and nothing depends on reviewers remembering rules. Rules that can be enforced by a tool are enforced by a tool.

## 2. Platform versions

| Component | Version line |
|---|---|
| Python | 3.13 |
| Node.js | 24 LTS |
| PostgreSQL | 17 |
| Keycloak | 26.x |
| Open Policy Agent | 1.x |

At Slice 0, after checking the current stable releases, these are pinned exactly: Python and Node patch versions and their runtime image digests; PostgreSQL, Keycloak, OPA and Traefik image digests; all Python and Node dependencies in lockfiles (`uv.lock`, `pnpm-lock.yaml`); the `uv` and `pnpm` versions; and every CI action by commit SHA. Upgrades are deliberate pull requests that pass the full pipeline.

## 3. Repository layout

```
palletwise/
├── AGENTS.md                  rules for every contributor, human or AI (section 9)
├── CLAUDE.md                  imports AGENTS.md, points to docs/
├── README.md                  one-command start, architecture summary
├── Makefile                   developer commands (section 7)
├── .env.example               every variable, documented, no values that are secrets
├── docs/
│   ├── product/               vision, personas, requirements
│   ├── specs/                 approved specifications
│   ├── adr/                   architecture decision records
│   └── quality/               performance test plan, threat model, eval reports
├── apps/
│   └── web/                   Vite, React, TypeScript; map, copilot, inbox, admin
├── packages/
│   └── contracts/             TypeScript types generated from the OpenAPI document
├── services/
│   ├── api/                   FastAPI application and workers (Python package "palletwise")
│   └── simulator/             the simulated WMS (Python package "pw_simulator")
├── db/
│   ├── migrations/            Alembic migrations
│   └── sql/                   versioned SQL for functions, views and policies (pw, sem, sim schemas)
├── policy/                    OPA Rego policies and their tests
├── semantic/model.yaml        semantic catalogue
├── copilot/                   prompts, fast_path.yaml
├── evals/                     question sets, golden files, runner
└── infra/
    ├── compose.yaml           local stack
    ├── keycloak/              development realm export
    └── opa/                   OPA configuration
```

The API package contains the API, the connector worker, the exception evaluator and the workflow worker as separate entry points of one codebase. **A common codebase is not a common identity**: each process has its own entry point, database credentials and role, connection pool, readiness endpoint, resource limits and restart policy. No connection pool is shared across privilege contexts.

## 4. Module boundaries

### 4.1 Python (`palletwise`)

```
api ─> agent ─> llm
 │       └────> tools ─> semantic ─> db
 ├─> workflow ─────────────────────> db
 ├─> ingest ───────────────────────> db
 ├─> evaluator ─> semantic
 └─> (all may use) contracts, policy, telemetry, clock, settings
```

**Legend.** An arrow means "may import directly". Permission is **not transitive**: `agent` may import `tools`, and `tools` may import `semantic`, but `agent` may not import `semantic`. At runtime a question flows API, agent runtime, tool executor, semantic layer, database; the imports above are what makes any other path impossible.

| Rule | Reason |
|---|---|
| `agent` imports only `llm` (the `LLMProvider` interface and adapters), the tool executor and shared modules; never `db` or `semantic` | the model reaches data only through tools |
| tool handlers are invoked **only** through `tools.executor`, which checks the kill switch, policy, input model, audit and tracing on every call (agent spec 6, 7) | no path to a handler bypasses governance |
| `tools` must not import `workflow` | tools draft proposals; they never approve or submit |
| nothing imports `api`; routers call application services and hold no business logic | routers stay thin |
| `ingest` and `evaluator` must not import `agent` or `llm` | no model in data paths |
| `pw_simulator` must not import `palletwise`, and the reverse | the simulator is an external system |
| only `db` and the DSL compiler in `semantic` may import SQLAlchemy | executable SQL has exactly two homes |

Enforced by `import-linter` contracts in CI.

**Business time.** Domain code (`semantic`, `tools`, `agent`, `workflow`, `evaluator`, `ingest`) never reads the wall clock: it receives `as_of` or the site clock. Ruff's banned-API rule rejects, in those packages, `datetime.now`, `datetime.utcnow`, `datetime.today`, `date.today`, `time.time` and `time.time_ns`; code review and tests with injected clocks cover indirect access. Infrastructure code (telemetry timestamps, timeouts, rate limits, retries) may use wall time, because it does not make business decisions.

### 4.2 Frontend (`apps/web`)

```
src/
├── app/            routing, providers, session
├── features/
│   ├── map/        R3F scene, 2D and 3D views, styles from the reason table
│   ├── copilot/    chat, answer cards, suggestions, stream client
│   ├── inbox/      exceptions, brief
│   ├── proposals/
│   └── admin/
└── shared/         ui components (shadcn/ui), generated API client, stores
```

Features communicate only through shared stores and the generated API client, never by importing each other's internals. Enforced by `dependency-cruiser` in CI.

## 5. Tooling and checks

| Area | Python | TypeScript |
|---|---|---|
| Format | ruff format | Prettier |
| Lint | ruff (including bugbear, security, no `print`) | ESLint with typescript-eslint strict, jsx-a11y, react-hooks |
| Types | pyright strict | `tsc` strict, no `any` |
| Unit tests | pytest | Vitest, Testing Library |
| Integration | pytest with Testcontainers PostgreSQL, real roles and RLS | |
| End to end | | Playwright: real stack, assertions on map highlights and rendered text |
| Policy | `opa test` | |
| Boundaries | import-linter | dependency-cruiser |
| Dependencies | `pip-audit` | `pnpm audit` |

### 5.1 Project-specific checks

| Check | How |
|---|---|
| No em or en dashes in UI text (FR-COP-13) | a test scans `apps/web/src` string literals and message files for U+2014 and U+2013; Playwright scans rendered text; generated answers are normalized at runtime |
| No executable SQL outside its two homes | structural: the import-linter rule in 4.1 (only `db` and the DSL compiler import SQLAlchemy) and ruff banning `sqlalchemy.text` everywhere except `db`. A keyword scan of string literals runs as a warning only. |
| No secrets | gitleaks in pre-commit and CI |
| No co-author trailers in commits | CI checks **every commit of the pull request** and is authoritative; the `commit-msg` hook only gives early feedback. Merges use **rebase only** (squash and merge commits disabled), so the commits CI checked are exactly the commits that land on `main`. |
| Contract drift | Pydantic models are the source of truth; FastAPI generates OpenAPI; `packages/contracts` is generated from it and committed. CI regenerates both and fails on any difference. No hand-edited API types exist. |
| Migrations | CI applies every migration to an empty database, then tests a round trip: downgrade the latest revision, upgrade again, and rerun the role, RLS and privileged-function tests. A migration that destroys data says so in its header and, where it changes an approved decision, has an ADR. |
| Privileged functions | catalog test from CWM 11.4 |

## 6. CI pipeline

GitHub Actions. Every pull request runs:

1. Format, lint, types, boundaries (Python and TypeScript).
2. Unit tests with coverage. At least 80 percent on `semantic`, `tools`, `policy`, `workflow` and domain code (NFR-COD-03). Coverage is a floor, not proof: the test suites named in the specs for tenant isolation, tool authorization, proposal and task boundaries, simulator reset, connector idempotency and semantic edge cases are mandatory regardless of the percentage.
3. Migrations on an empty database, then integration tests (RLS, roles, functions).
4. `opa test`.
5. Contract drift, raw SQL, dashes, secrets, dependency audit.
6. Build of the web app and container images.
7. **Evaluation gates**, in two levels:
   - **Deterministic gate, every pull request**: no model needed. For each evaluation case, the gold tool and parameters are executed and must produce the gold highlights; fast-path routing, input validation, decline cases, indirect-injection handling at the data boundary, and suggestion rules are checked.
   - **Model gate, when a pull request changes prompts, tools, the semantic layer or model settings**: runs the suite against the configured model and blocks merge below the NFR-QLT-02 thresholds. If the provider is unavailable, the check reports **pending, provider unavailable** and keeps blocking; it is never skipped or passed silently. A maintainer reruns it when the provider is back.
   - The full model-backed suite also runs nightly.
8. End-to-end tests on the composed stack for slice acceptance checks.

`main` is protected: pull requests only, all checks green, linear history.

## 7. Local development

| Command | Does |
|---|---|
| `make up` | builds and starts the stack, runs migrations, loads the calendar and the deterministic seed, prints the URLs and development logins |
| `make down` | stops the stack |
| `make reset-data` | resets the simulated site through the normal reset path |
| `make test` | unit and integration tests |
| `make e2e` | end-to-end tests against the running stack |
| `make evals` | evaluation suite (needs model credentials) |
| `make lint` | every static check from section 5 |
| `make contracts` | regenerates OpenAPI and frontend types |

`make up` on a clean machine with Docker is the only setup step besides creating `.env` from `.env.example` (NFR-COD-07).

**Optional credentials never block startup.**

| Missing | Behaviour |
|---|---|
| `.env` | `make up` stops with a message explaining how to create it from `.env.example` |
| Google credentials | the stack starts fully; the copilot runs on path B only (fast path with template answers, agent spec 3.2) and says that the model is not configured |
| Langfuse keys | telemetry export is disabled; structured logs with trace IDs still work locally |

Migrations, calendar loading, simulator seeding and the UI never depend on optional credentials. CI uses the same behaviour for every job except the model gate.

### 7.1 Local stack (`infra/compose.yaml`)

| Service | Purpose |
|---|---|
| `migrate` | one-shot: applies migrations, creates roles and grants, loads the calendar reference data; every other application service waits for it to complete successfully |
| `postgres` | one local instance holding the PalletWise schemas and the `sim` schema; isolation comes from the database roles (CWM 11.3, simulator spec 2), which behave the same here as on separate instances in production |
| `keycloak` | development realm with `warehouse_manager` and `admin` users and the copilot client |
| `opa` | policy decision point with policies from `policy/` |
| `api` | FastAPI BFF |
| `worker-connector` | connector worker (`sim_outbox_reader` and `pw_ingest`) |
| `worker-evaluator` | exception engine (`pw_evaluator`) |
| `worker-workflow` | proposal submission and reconciliation (`pw_workflow`) |
| `simulator` | simulated WMS (`sim_owner`) |
| `simadmin` | simulation admin service (`pw_sim_admin`): initial load, hash check, site readiness; simulation controls in a later slice |
| `web` | frontend |
| `edge` | Traefik, single entry point on localhost |

Every service has a health check, and `depends_on` uses readiness conditions, not just start order: workers and the simulator wait for `migrate`; the API waits for PostgreSQL and Keycloak to be healthy; the connector worker waits for both the simulator outbox and the ingestion functions; `web` is served through `edge` once the API is healthy.

Langfuse is not part of the stack. The API and workers export to it only when keys are present, after masking personal data in-process (agent spec 13); in production configuration, prompt and response text are not exported.

### 7.2 Secrets and configuration

- `.env` is local and ignored by git; `.env.example` documents every variable.
- `GOOGLE_APPLICATION_CREDENTIALS` points to a key file **outside** the repository. Deployed environments use workload identity and no key file.
- Database passwords for each role, Keycloak client secrets and Langfuse keys come from `.env` locally and from a secret manager in deployed environments.
- The Keycloak development realm contains development-only passwords and is never used outside local development.

## 8. Ways of working

| Topic | Rule |
|---|---|
| Branches | one branch per slice or fix, short-lived, merged by pull request |
| Commits | Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `chore:`), imperative mood, no co-author trailers |
| Pull requests | link the slice and the requirement IDs; checklist from the Definition of Done |
| Architecture changes | an ADR in `docs/adr/` approved by the product owner before implementation (NFR-COD-06) |
| Reviews | `/code-review`, `/simplify` and `/security-review` run on every slice before the product owner reviews it |

### 8.1 Definition of Done for a slice

1. Every acceptance check of the slice passes, with tests that would fail without the feature.
2. All CI checks green, including the evaluation gate where it applies.
3. No new warnings from linters or type checkers.
4. Review skills run and their findings resolved or explained.
5. Specs, README and `AGENTS.md` updated if behaviour or rules changed.
6. The slice runs from `make up` on a clean checkout.

### 8.2 Enforcement boundaries

- Static checks catch mistakes early; they never replace runtime authorization, RLS or policy checks.
- CI is authoritative; local hooks are a convenience and can be bypassed.
- Each worker runs as its own process with its own credentials.
- No business logic uses wall-clock time.
- All external telemetry is masked in-process before export.
- Missing optional credentials degrade features but never block startup.
- Database role boundaries hold even when schemas share one local PostgreSQL instance.

### 8.3 Slice 0 acceptance

Slice 0 builds the foundation only; no application features.

| Step | Must prove |
|---|---|
| Repository scaffold | formatting, linting, types and import boundaries run and pass in CI |
| Compose | every service starts healthy with `make up`, with and without optional credentials |
| Core migrations | roles, grants, RLS, privileged functions and the migration round trip pass their tests |
| Calendar | `calendar_region` and `business_calendar` load exactly from `calendar.csv` (reference data, loaded by `migrate`) |
| Deterministic seed | the simulator generates the site and history and the connector ingests it through the normal connector path, never by writing canonical tables directly; the same seed gives the same dataset hash |
| First commit | no secrets, no co-author trailer, CI green |

## 9. `AGENTS.md` rules

`AGENTS.md` is created in Slice 0 with these rules, kept short so it is read:

1. Read the approved specs in `docs/` before changing behaviour; specs win over assumptions.
2. Never change an approved architecture decision without an ADR approved by the product owner.
3. Business calculations live only in the semantic layer; the model never calculates.
4. The model never writes SQL; tools call semantic functions only.
5. Every tool call goes through policy, audit and tracing; no shortcuts for "internal" calls.
6. Use the site clock, never the system clock, in domain code.
7. Each write path uses its own database role; never widen a role to make something work.
8. No secrets in the repository, in logs or in traces.
9. No em or en dashes in user-facing text.
10. No co-author trailers in commit messages.
11. Small, tested changes; a slice is done only when its Definition of Done is met.

## 10. Decisions for review

| # | Decision |
|---|---|
| 1 | Version lines: Python 3.13, Node 24 LTS, PostgreSQL 17, Keycloak 26, OPA 1; exact pins, digests and action SHAs set at Slice 0 |
| 2 | One Python codebase for API and workers, run as separate processes with separate roles and pools; the simulator is a separate package that cannot import it |
| 3 | Boundaries enforced by import-linter and dependency-cruiser; arrows are direct, non-transitive imports; single tool executor; SQLAlchemy only in `db` and the DSL compiler; wall-clock ban in domain packages only |
| 4 | Dash and co-author rules enforced by CI on every commit, with rebase-only merges |
| 5 | Two evaluation levels: a deterministic gate on every pull request and a model gate that blocks as pending when the provider is unavailable |
| 6 | `make up` is the single setup command; optional credentials degrade features but never block startup |
| 7 | Langfuse stays outside the local stack; export only with keys, masked in-process |
| 8 | No extra "semantic interface" layer and no live-event outbox; the review's suggestions on these were not adopted, with reasons (4.1, and agent spec 9.2) |

## 11. Change log

| Date | Change |
|---|---|
| 2026-11-18 | First draft. |
| 2026-11-18 | Slice 0 facts: `simadmin` added to the local stack table; TypeScript pinned to 6.0 because typescript-eslint and dependency-cruiser do not support 7.0 yet; `make reset-data` drops the volume until the simulation controls slice adds the CWM 7.4 reset path. |
| 2026-11-18 | Revision 2 after review: exact pinning scope; per-process identity and pools; import legend, `llm` module, single tool executor, SQLAlchemy confinement, scoped business-time rule; structural SQL check; rebase-only merges with CI commit checks; committed generated contracts; migration round trip; coverage as a floor; two-level evaluation gate; optional-credential behaviour; `migrate` service, health checks and one-instance role isolation; telemetry masking; enforcement boundaries; Slice 0 acceptance. |
