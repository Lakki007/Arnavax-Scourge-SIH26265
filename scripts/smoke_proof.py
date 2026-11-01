#!/usr/bin/env python3
"""One end-to-end bank-session proof through the HTTP API (demo mode): officer requests, institution proves,
the verifier (local or independent) reports, and the result must be Verified. CI uses it for the two-host deployment.
  python3 scripts/smoke_proof.py http://127.0.0.1:8000"""
import base64
import json
import sys
import time

import httpx

B = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080"


def session(role):
    c = httpx.Client(base_url=B, follow_redirects=False, timeout=120)
    r = c.post("/signin", data={"role": role, "next": "/"})
    assert r.status_code == 303, (r.status_code, r.text[:300])
    csrf = json.loads(base64.urlsafe_b64decode(c.cookies["scourge_session"].rsplit(".", 1)[0]))["csrf"]
    return c, csrf


h = None
for _ in range(60):
    try:
        h = httpx.get(B + "/api/health", timeout=10).json()
        if h["services"]["verifier"]:
            break
    except httpx.HTTPError:
        pass
    time.sleep(2)
print("health:", h)
assert h and h["services"]["verifier"], "verifier not reachable from the portal"
off, oc = session("officer")
r = off.post("/fdrs/FDR-001/challenge", data={"csrf": oc})
assert r.status_code == 303, r.text[:300]
inst, ic = session("institution:INST-42")
import re  # noqa: E402
m = re.search(r'/challenges/([A-Za-z0-9_-]+)', inst.get("/portal").text)
assert m, "no open challenge visible to the institution"
cid = m.group(1)
r = inst.post(f"/challenges/{cid}/prove", data={"csrf": ic})
assert r.status_code == 303, r.text[:300]
jid = r.headers["location"].rsplit("/", 1)[1]
for _ in range(480):
    j = inst.get(f"/api/jobs/{jid}").json()
    if j["state"] != "running":
        break
    time.sleep(0.25)
print("job:", {k: j.get(k) for k in ("state", "decision", "reason")})
assert j.get("decision") == "verified", j
print("OK: proof verified" + (" through the independently deployed verifier" if h.get("verifier_mode") == "external" else ""))
