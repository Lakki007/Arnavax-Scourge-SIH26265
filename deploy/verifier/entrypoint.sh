#!/bin/sh
# Independent AICTE verifier container. It holds no database and no documents: it co-runs the MPC-TLS session
# with the institution's prover, checks certificate, host and record, and reports FACTS to the portal.
#   SCOURGE_INTERNAL_KEY  shared secret with the portal (required)
#   SCOURGE_APP_INTERNAL  host:port of the portal's internal endpoint on the private network (required)
#   VERIFIER_LISTEN       default 0.0.0.0:7047 (provers connect here)
set -eu
: "${SCOURGE_INTERNAL_KEY:?set SCOURGE_INTERNAL_KEY}"
: "${SCOURGE_APP_INTERNAL:?set SCOURGE_APP_INTERNAL, e.g. portal:8081}"
python3 - <<'PY'
import json, os
c = json.load(open("config/scourge.json", encoding="utf-8"))
c["app_internal"] = os.environ["SCOURGE_APP_INTERNAL"]
c["verifier_listen"] = os.environ.get("VERIFIER_LISTEN", "0.0.0.0:7047")
json.dump(c, open("/tmp/verifier.json", "w", encoding="utf-8"))
PY
# Start the verifier, then publish ONLY its public key for the portal to pin (the private key stays here).
./bin/scourge_verifierd /tmp/verifier.json &
pid=$!
if [ -n "${SCOURGE_VERIFIER_PUB_OUT:-}" ] && [ -n "${SCOURGE_VERIFIER_KEY:-}" ]; then
  pub="${SCOURGE_VERIFIER_KEY%.key}.pub"
  for i in $(seq 1 50); do [ -s "$pub" ] && break; sleep 0.2; done
  mkdir -p "$SCOURGE_VERIFIER_PUB_OUT" && cp "$pub" "$SCOURGE_VERIFIER_PUB_OUT/verifier_signing.pub"
fi
trap 'kill -TERM $pid' TERM INT
wait $pid
