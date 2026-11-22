#!/usr/bin/env bash
# Deploys PalletWise on the server: the latest main, the production overrides, every service healthy.
#   bash infra/deploy/deploy.sh [branch]   (default: main)
set -euo pipefail
BRANCH="${1:-main}"
cd "$(dirname "$0")/../.."
BEFORE="$(git rev-parse HEAD)"
git fetch --quiet origin "$BRANCH"
git checkout --quiet "$BRANCH"
git reset --quiet --hard "origin/$BRANCH"
[ -f .env ] || python3 scripts/setup.py --no-start
docker compose -f compose.yaml -f infra/compose.prod.yaml up -d --build --wait --remove-orphans
# Keycloak caches its theme in production mode: restart it when the theme changed.
if ! git diff --quiet "$BEFORE" HEAD -- infra/keycloak/themes; then
  docker compose -f compose.yaml -f infra/compose.prod.yaml restart keycloak
  docker compose -f compose.yaml -f infra/compose.prod.yaml up -d --wait keycloak
fi
docker image prune -f >/dev/null
echo "PalletWise is up: https://palletwise.oshadhakariyawasam.me"
