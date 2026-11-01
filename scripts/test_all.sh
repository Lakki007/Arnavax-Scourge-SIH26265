#!/usr/bin/env bash
# Everything, in one command, on a clean checkout (Linux x86-64 or macOS Apple Silicon):
#   pip install -r requirements.txt pytest httpx && ./scripts/test_all.sh
# 1. demo mode: the full suite (tests/conftest.py starts and stops the portal, verifier and synthetic banks)
# 2. production mode: named accounts + mandatory officer 2FA, on a fresh data directory (negative cases included)
# Exits non-zero if anything fails or a required service cannot start.
set -uo pipefail
cd "$(dirname "$0")/.."
PY=${PYTHON:-python3}
health() { curl -sf http://127.0.0.1:8080/api/health >/dev/null 2>&1; }
if health; then echo "A portal is already running on 127.0.0.1:8080; stop it first so both modes start clean."; exit 2; fi

echo "== 1/2 demo mode: full suite"
SCOURGE_REQUIRE_SERVER=1 "$PY" -m pytest -q tests --ignore=tests/test_production_mode.py; demo=$?

echo "== 2/2 production mode: named accounts, mandatory 2FA, negative cases"
PROD_VAR="$(mktemp -d)/var_prod"
SCOURGE_AUTH=password SCOURGE_VAR="$PROD_VAR" ./scripts/run.sh > "$PROD_VAR.log" 2>&1 &
pid=$!
for i in $(seq 1 90); do health && break; sleep 1; done
if ! health; then echo "production-mode portal did not start; see $PROD_VAR.log"; kill $pid 2>/dev/null; exit 1; fi
SCOURGE_AUTH=password SCOURGE_VAR="$PROD_VAR" SCOURGE_REQUIRE_PROD=1 SCOURGE_NO_AUTOSTART=1 "$PY" -m pytest -q tests/test_production_mode.py; prod=$?
kill -TERM $pid 2>/dev/null; wait $pid 2>/dev/null

echo "== demo mode: $([ $demo -eq 0 ] && echo PASS || echo FAIL)   production mode: $([ $prod -eq 0 ] && echo PASS || echo FAIL)"
[ $demo -eq 0 ] && [ $prod -eq 0 ]
