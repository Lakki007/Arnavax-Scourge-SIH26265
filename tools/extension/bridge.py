#!/usr/bin/env python3
"""Verifier -> portal bridge for the browser-extension prover.

The extension's verifier co-runs the MPC-TLS session with the bank and ends up holding the proven FD
response (decrypted with the page's own app-layer key for banks that encrypt it). This bridge turns that
decrypted record into the same FACTS the command-line verifier posts, and sends it to the portal's internal
endpoint. Everything downstream (register match, four verdicts, release gate) is unchanged.

It is the one piece of the extension that is pure Scourge code and can be built and tested without a bank:
feed it a decrypted record + a consumed challenge and the portal records evidence exactly as for a CLI proof.

  from bridge import post_record
  post_record(app="http://127.0.0.1:8080", internal_key=..., challenge_id=..., institution_id=..., token=...,
              bank_host="netbanking.mockbank.test", record={...decrypted bank JSON...})
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
CFG = json.loads((ROOT / "config" / "scourge.json").read_text(encoding="utf-8"))


def _find_fd(record, select_key, fd_number):
    """Mirror common.rs find_objects: every object in the decrypted JSON whose select_key == the challenged FD."""
    out = []

    def walk(n):
        if isinstance(n, dict):
            if str(n.get(select_key, "")) == fd_number:
                out.append(n)
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)

    walk(record)
    return out


def build_facts(bank_host, fd_number, record):
    """Replicate rust/common.rs::facts for an already-decrypted record. gate in facts_ok|insufficient|rejected."""
    schema = CFG["banks"].get(bank_host)
    if schema is None:
        return {"gate": "rejected", "reason": "host_not_allow_listed", "fields": {}}
    objs = _find_fd(record, schema["select_key"], fd_number)
    if not objs:
        return {"gate": "rejected", "reason": "no_fd_record", "fields": {}}
    if len(objs) > 1:
        return {"gate": "insufficient_evidence", "reason": "fd_record_ambiguous", "fields": {}}
    obj = objs[0]
    fields = {}
    for logical, key in schema["fields"].items():
        if key in obj and not isinstance(obj[key], (dict, list)):
            fields[logical] = {"state": "proven", "key": key, "value": str(obj[key])}
        elif isinstance(obj.get(key), (dict, list)):
            fields[logical] = {"state": "unsupported_type", "key": key}
        else:
            fields[logical] = {"state": "not_exposed", "key": key}
    return {"gate": "facts_ok", "reason": None, "fields": fields}


def _post(url, key, path, payload):
    req = urllib.request.Request(url + path, data=json.dumps(payload).encode(),
                                headers={"Content-Type": "application/json", "X-Internal-Key": key}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:            # 4xx/5xx carry a JSON reason we want to surface, not raise raw
        body = e.read() or b"{}"
        try:
            return e.code, json.loads(body)
        except ValueError:
            return e.code, {"detail": body.decode(errors="replace")[:200]}


def post_record(app, internal_key, challenge_id, institution_id, token, bank_host, record, metrics=None):
    """Consume the challenge (as the extension's handshake does) then post the facts. Returns the portal's reply."""
    code, body = _post(app, internal_key, "/internal/challenges/consume",
                        {"challenge_id": challenge_id, "institution_id": institution_id, "token": token})
    if code != 200:
        raise RuntimeError(f"challenge not accepted: {code} {body}")
    facts = build_facts(bank_host, body.get("fd_number", ""), record)
    facts["challenge_id"] = challenge_id
    facts["metrics"] = metrics or {"source": "browser_extension"}
    code, body = _post(app, internal_key, "/internal/evidence", facts)
    if code != 200:
        raise RuntimeError(f"evidence rejected: {code} {body}")
    return body


if __name__ == "__main__":
    # demo: bridge a decrypted record from a JSON file. Args: challenge_id institution_id token bank_host record.json
    a = sys.argv[1:]
    if len(a) != 5:
        sys.exit("usage: bridge.py <challenge_id> <institution_id> <token> <bank_host> <record.json>")
    key = os.environ.get("SCOURGE_INTERNAL_KEY") or (ROOT / "var" / "internal.key").read_text().strip()
    rec = json.loads(Path(a[4]).read_text(encoding="utf-8"))
    print(post_record("http://127.0.0.1:8080", key, a[0], a[1], a[2], a[3], rec))
