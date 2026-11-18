# AGENTS.md

Rules for every contributor, human or AI. Keep this file short so it is read.

## Read first

- Product: `docs/product/` (vision, personas, requirements).
- Specs: `docs/specs/` (canonical model, semantic layer, agent and tools, simulator, engineering baseline).
- Decisions: `docs/adr/`.

Approved specs win over assumptions. If a spec is unclear, ask; do not guess.

## Rules

1. Read the approved specs before changing behaviour.
2. Never change an approved architecture decision without an ADR approved by the product owner.
3. Business calculations live only in the semantic layer; the model never calculates.
4. The model never writes SQL; tools call semantic functions only.
5. Every tool call goes through `tools.executor`: policy, kill switch, audit and tracing. No shortcuts.
6. Domain code uses the site clock, never the system clock.
7. Each write path uses its own database role. Never widen a role to make something work.
8. No secrets in the repository, logs or traces.
9. No em dashes or en dashes in user-facing text.
10. No co-author trailers in commit messages.
11. Small, tested changes. A slice is done only when its Definition of Done is met (`docs/specs/engineering-baseline.md`, section 8.1).

## Commands

| Command | Does |
|---|---|
| `make up` | start the local stack with migrations, calendar and seed |
| `make down` | stop the stack |
| `make lint` | all static checks |
| `make test` | unit and integration tests |
| `make contracts` | regenerate OpenAPI and frontend types |
