# PalletWise developer commands (engineering baseline 7).
SHELL := /bin/bash
COMPOSE := docker compose --env-file infra/images.env --env-file .env -f infra/compose.yaml

.PHONY: up down logs ps reset-data lint lint-py lint-web test test-unit test-integration contracts golden check-env

check-env:
	@test -f .env || { echo "Missing .env. Create it with: cp .env.example .env  (then replace every change-me value)"; exit 1; }
	@! grep -q "change-me" .env || { echo ".env still contains change-me values. Replace them before starting."; exit 1; }

up: check-env
	$(COMPOSE) up -d --build --wait
	@echo ""
	@echo "PalletWise is up:  http://localhost:$$(grep -E '^PW_EDGE_HOST_PORT=' .env | cut -d= -f2 || echo 8080)"
	@echo "Keycloak (dev):    http://localhost:$$(grep -E '^PW_KEYCLOAK_HOST_PORT=' .env | cut -d= -f2 || echo 8180)  users: manager, admin"

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

test: test-unit test-integration
	pnpm --filter @palletwise/web test

test-unit:
	uv run pytest tests/unit -q

test-integration:
	uv run pytest tests/integration -q

contracts:
	uv run python scripts/export_openapi.py packages/contracts/openapi.json
	pnpm --filter @palletwise/contracts generate
	pnpm exec prettier --write packages/contracts/src/api.ts >/dev/null 2>&1 || true

# Records the dataset hash of the running stack's completed load as the golden hash for its seed.
golden: check-env
	$(COMPOSE) exec -T simadmin python -c "import json; from palletwise.db import dataset_hash, site_repo; from palletwise.db.connection import connect; from palletwise.settings import *; c=role_credentials('PW_SIM_ADMIN'); conn=connect(DatabaseSettings().dsn(c.user,c.password)); r=site_repo.latest_run(conn, DEMO_TENANT_ID, DEMO_SITE_ID); print(json.dumps({'dataset_hash': r.dataset_hash, 'seed': r.seed, 'dataset_version': r.dataset_version}))"
