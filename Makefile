# PalletWise developer commands (engineering baseline 7).
SHELL := /bin/bash
COMPOSE := docker compose --env-file infra/images.env --env-file .env -f infra/compose.yaml

.PHONY: up down logs ps reset-data lint lint-py lint-web test test-unit test-integration test-policy evals evals-model storyline contracts golden check-env

check-env:
	@test -f .env || { echo "Missing .env. Create it with: cp .env.example .env  (then replace every change-me value)"; exit 1; }
	@! grep -q "change-me" .env || { echo ".env still contains change-me values. Replace them before starting."; exit 1; }
	@! grep -qE '^GCP_SA_FILE=[^/]' .env || { echo "GCP_SA_FILE must be an absolute path (Compose resolves relative paths from infra/)."; exit 1; }

up: check-env
	$(COMPOSE) up -d --build --wait
	@edge=$$(grep -E '^PW_EDGE_HOST_PORT=' .env | cut -d= -f2); \
	kc=$$(grep -E '^PW_KEYCLOAK_HOST_PORT=' .env | cut -d= -f2); \
	echo ""; \
	echo "PalletWise is up:  http://localhost:$${edge:-8080}"; \
	echo "Keycloak (dev):    http://localhost:$${kc:-8180}  users: manager, admin"

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=100

ps:
	$(COMPOSE) ps

# Slice 0: drops and recreates the stack's database volume. The reset path through the
# simulation admin service (CWM 7.4) arrives with the simulation controls slice.
reset-data: check-env
	$(COMPOSE) down -v
	$(MAKE) up

lint: lint-py lint-web

lint-py:
	uv run ruff check .
	uv run ruff format --check .
	uv run pyright
	uv run lint-imports

lint-web:
	pnpm --filter @palletwise/web lint
	pnpm --filter @palletwise/web typecheck
	pnpm --filter @palletwise/contracts typecheck

test: test-unit test-integration test-policy
	pnpm --filter @palletwise/web test

test-unit:
	uv run pytest tests/unit -q

test-integration:
	uv run pytest tests/integration -q --durations=15

# Deterministic eval gate (evals spec 5): no model, no credentials; blocks every merge.
evals:
	uv run python -m evals --level deterministic

# Model eval gate: three runs per case on the real model. Reads GCP_SA_FILE and GCP_PROJECT_ID
# from .env; without them it writes a pending report and exits 3 (never a silent pass).
# CONFIG=name runs one runtime configuration; COMPARE=a,b compares several on the same cases
# (ADR 0001), for example COMPARE=baseline,baseline for an A/A noise run.
CONFIG ?= production
evals-model:
	@sa=$$(grep -E '^GCP_SA_FILE=' .env 2>/dev/null | cut -d= -f2-); \
	project=$$(grep -E '^GCP_PROJECT_ID=' .env 2>/dev/null | cut -d= -f2-); \
	PW_LLM_CREDENTIALS_FILE="$$sa" PW_LLM_PROJECT="$$project" uv run python -m evals --level model \
	  --config "$(CONFIG)" $(if $(COMPARE),--compare "$(COMPARE)")

# OPA policy tests with the pinned OPA image (CI uses the setup-opa action).
test-policy:
	docker run --rm -v "$(CURDIR)/policy:/policy:ro" "$$(grep -E '^OPA_IMAGE=' infra/images.env | cut -d= -f2-)" test /policy

contracts:
	uv run python scripts/export_openapi.py packages/contracts/openapi.json
	pnpm --filter @palletwise/contracts generate
	pnpm exec prettier --write packages/contracts/src/api.ts >/dev/null 2>&1 || true

# The guided demo clicked through twice on the live stack, each run after a reset (Slice 4 step 6).
storyline: check-env
	uv run --with playwright python -m evals.storyline

# Records the running stack's completed load as the golden hash for its seed, then copies the
# file into the repository (review and commit it).
golden: check-env
	$(COMPOSE) exec -T simadmin palletwise-simadmin record-golden
	docker cp $$($(COMPOSE) ps -q simadmin):/app/services/simulator/golden/. services/simulator/golden/
