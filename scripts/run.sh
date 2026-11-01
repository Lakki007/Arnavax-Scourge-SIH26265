#!/usr/bin/env bash
# Starts the Scourge portal; the portal starts the verifier and synthetic banks itself.
# The app ALWAYS listens on the loopback address in config/scourge.json "app_internal" (the verifier reports there).
# Publish it with a reverse proxy (deploy/nginx.conf), never by binding the app to a public address.
# Environment (optional): SCOURGE_ACCESS_CODE (shared judges' code), SCOURGE_COOKIE_SECURE=1 (behind HTTPS)
set -euo pipefail
umask 077   # database, logs, uploads and institution copies are readable by this user only
cd "$(dirname "$0")/.."
DATA=${SCOURGE_VAR:-var}
if [ -d "$DATA" ]; then chmod -R go-rwx "$DATA"; fi
[ -f certs/test_ca.der ] || ./scripts/setup_certs.sh
PLAT="bin/$(uname -s | tr '[:upper:]' '[:lower:]')-$(uname -m)"
if [ -z "${SCOURGE_BIN:-}" ] && [ -x "$PLAT/scourge_verifierd" ]; then export SCOURGE_BIN="$PWD/$PLAT"; fi
BINDIR="${SCOURGE_BIN:-bin}"
for b in scourge_prove scourge_verifierd scourge_mockbank; do [ -x "$BINDIR/$b" ] || { echo "missing $BINDIR/$b - run scripts/build_rust.sh"; exit 1; }; done
ADDR=$(python3 -c "import json;print(json.load(open('config/scourge.json'))['app_internal'])")
HOST=${ADDR%:*}; PORT=${ADDR##*:}
case "$HOST" in 127.0.0.1|::1) ;; *) echo "app_internal must be a loopback address, got $HOST"; exit 1;; esac
# trust X-Forwarded-For only from the local reverse proxy
exec python3 -m uvicorn app.main:app --host "$HOST" --port "$PORT" --proxy-headers --forwarded-allow-ips 127.0.0.1 --no-server-header
