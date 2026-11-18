# PalletWise

A WMS-agnostic warehouse intelligence platform: a governed AI copilot, an interactive 2D/3D
warehouse map, and human-approved operational workflows. Start with
[docs/product/vision.md](docs/product/vision.md).

## Status

**Slice 0, foundation.** The repository, tooling, local stack, canonical data model and a
deterministic simulated warehouse are in place. Application features (map, copilot, exceptions,
proposals) arrive from Slice 1.

## Run it

Requirements: Docker, `make`. For development also `uv` and `pnpm`.

```bash
cp .env.example .env      # then replace every change-me value
make up                   # builds, migrates, loads the calendar, simulates and ingests 180 days
```

`make up` returns once every service is healthy and the simulated site is `READY`
(about 5 minutes on first start). Then open:

| What | Where |
|---|---|
| PalletWise | http://localhost:8080 |
| API status | http://localhost:8080/api/sites/current/status |
| API docs | http://localhost:8080/api/docs |
| Keycloak (development realm) | http://localhost:8180, users `manager` and `admin` |

No model or telemetry credentials are needed to start the stack.

## Architecture in one picture

```
simulator (simulated WMS, own "sim" schema)
   │ outbox, in global sequence order, with tick markers
   ▼
connector worker ── pw_ingest ──> canonical model (pw schema, RLS, invariant-keeping functions)
                                          │
simulation admin ── pw_sim_admin ─────────┤ site lifecycle: LOADING, hash check, READY
API (BFF) ───────── pw_app ───────────────┘ read
```

Every process has its own database role. The simulator is reached only through the connector
interface, exactly as a real WMS will be. See [docs/specs/](docs/specs/).

## Commands

| Command | Does |
|---|---|
| `make up` / `make down` | start / stop the stack |
| `make reset-data` | drop the database volume and load again from the seed |
| `make lint` | ruff, pyright strict, import boundaries, ESLint, Prettier, dependency-cruiser, tsc |
| `make test` | unit, integration (real PostgreSQL 17) and web tests |
| `make contracts` | regenerate OpenAPI and the frontend types |

## Data

Development data is synthetic. The only external inputs are `data/reference/calendar.csv` and
`data/reference/outlets.csv`. The same seed always produces the same dataset; the golden hash is
in `services/simulator/golden/`.

## Rules

See [AGENTS.md](AGENTS.md).
