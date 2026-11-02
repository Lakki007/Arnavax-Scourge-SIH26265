"""The verifier -> portal trust boundary, attacked the three ways an assessor would:
1. inject "verified" facts through the internal interface (with the internal key, but without the verifier's key);
2. rewrite stored evidence or its decision as a database administrator;
3. attach a legitimate signed result to a different challenge, deposit or institution.
Runs against the real prover, the real signing verifier and the synthetic bank."""
import json
import os
import sqlite3
import subprocess
import sys

import httpx
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
B = os.environ.get("SCOURGE_URL", "http://127.0.0.1:8080")
VARDIR = os.environ.get("SCOURGE_VAR", os.path.join(ROOT, "var"))


def _up():
    try:
        return httpx.get(B + "/api/health", timeout=3).status_code == 200
    except httpx.HTTPError:
        return False


if os.environ.get("SCOURGE_REQUIRE_SERVER") == "1" and not _up():
    raise RuntimeError(f"server not reachable at {B}")
pytestmark = pytest.mark.skipif(not _up(), reason=f"needs the app running at {B}")

import test_acceptance as T  # noqa: E402


def _key():
    return open(os.path.join(VARDIR, "internal.key")).read().strip()


def _db():
    return sqlite3.connect(T.DB)


def _audit():
    env = {**os.environ, "SCOURGE_VAR": VARDIR}
    return subprocess.run([sys.executable, "scripts/audit_evidence.py"], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)


@pytest.fixture()
def off():
    o = T.S("officer"); o.post("/system/reset")
    for n in ("verifier", "genuine", "fake", "unlisted"):
        o.post("/system/service", name=n, op="start")
    return o


def test_b1_internal_key_alone_cannot_inject_evidence(off):
    off.post("/fdrs/FDR-002/challenge")
    cid, token, inst = _db().execute("SELECT id, token, institution_id FROM challenges WHERE fdr_id='FDR-002' ORDER BY issued_at DESC").fetchone()
    h = {"X-Internal-Key": _key()}
    assert httpx.post(B + "/internal/challenges/consume", json={"challenge_id": cid, "institution_id": inst, "token": token}, headers=h).status_code == 200
    fake = {"challenge_id": cid, "gate": "facts_ok", "fields": {"status": {"state": "proven", "value": "ACTIVE"},
            "balance": {"state": "proven", "value": "2500000.00"}, "maturity": {"state": "proven", "value": "2029-01-15"},
            "lien": {"state": "proven", "value": "AICTE NEW DELHI (SECURITY DEPOSIT)"}}}
    assert httpx.post(B + "/internal/evidence", json=fake, headers=h).status_code == 403                       # unsigned
    assert httpx.post(B + "/internal/evidence", json=fake, headers={**h, "X-Verifier-Signature": "ab" * 64}).status_code == 403
    assert _db().execute("SELECT count(*) FROM evidence WHERE challenge_id=?", (cid,)).fetchone()[0] == 0
    assert _db().execute("SELECT count(*) FROM events WHERE kind='evidence_refused'").fetchone()[0] >= 2


def test_b2_admin_rewrite_of_evidence_or_decision_is_detected(off):
    j, cid = T.prove(off, "FDR-001")
    assert j["decision"] == "verified"
    assert _audit().returncode == 0
    c = _db()
    c.execute("UPDATE evidence SET decision='discrepancy' WHERE challenge_id=?", (cid,)); c.commit()
    r = _audit(); assert r.returncode == 1 and "signed facts give 'verified'" in r.stdout, r.stdout
    c.execute("UPDATE evidence SET decision='verified', facts_raw=replace(facts_raw,'ACTIVE','CLOSED') WHERE challenge_id=?", (cid,)); c.commit()
    r = _audit(); assert r.returncode == 1 and "signature" in r.stdout, r.stdout


def test_b3_signed_result_cannot_be_moved_to_another_challenge(off):
    j, cid = T.prove(off, "FDR-001")
    raw, sig = _db().execute("SELECT facts_raw, verifier_sig FROM evidence WHERE challenge_id=?", (cid,)).fetchone()
    h = {"X-Internal-Key": _key(), "X-Verifier-Signature": sig}
    # replaying the same signed bytes: already recorded for that challenge
    assert httpx.post(B + "/internal/evidence", content=raw.encode(), headers=h).status_code == 409
    # re-pointing it at another consumed challenge breaks the signature
    off.post("/fdrs/FDR-002/challenge")
    cid2, token, inst = _db().execute("SELECT id, token, institution_id FROM challenges WHERE fdr_id='FDR-002' ORDER BY issued_at DESC").fetchone()
    httpx.post(B + "/internal/challenges/consume", json={"challenge_id": cid2, "institution_id": inst, "token": token}, headers={"X-Internal-Key": _key()})
    moved = json.loads(raw); moved["challenge_id"] = cid2
    assert httpx.post(B + "/internal/evidence", content=json.dumps(moved).encode(), headers=h).status_code == 403
    assert _db().execute("SELECT count(*) FROM evidence WHERE challenge_id=?", (cid2,)).fetchone()[0] == 0


def test_b4_verifier_key_is_pinned_and_audited(off):
    T.prove(off, "FDR-001")
    pin = open(os.path.join(VARDIR, "verifier_key.pin")).read().split()
    pub = open(os.path.join(VARDIR, "verifier_signing.pub")).read().strip()
    assert pin[1] == pub
    fp = _db().execute("SELECT detail_json FROM events WHERE kind='evidence_recorded' ORDER BY id DESC").fetchone()[0]
    assert json.loads(fp)["verifier_key"] == pin[0]


def test_b5_bridge_key_cannot_mint_verified(off):
    """b5 The extension bridge's key is not the verifier's: anything it signs is recorded as unproven and capped."""
    sys.path.insert(0, os.path.join(ROOT, "tools", "extension"))
    import bridge
    off.post("/fdrs/FDR-002/challenge")
    cid, token, inst = sqlite3.connect(T.DB).execute(
        "SELECT id, token, institution_id FROM challenges WHERE fdr_id='FDR-002' AND state='issued' ORDER BY issued_at DESC").fetchone()
    rec = {"depositNo": "FD-0000777777", "status": "ACTIVE", "currentBalance": "2500000.00", "principal": "2500000.00",
           "maturityDate": "2029-01-15", "holders": "SYNTHETIC / MEMBER SECRETARY AICTE", "lien": "AICTE NEW DELHI (SECURITY DEPOSIT)"}
    out = bridge.post_record(B, open(os.path.join(VARDIR, "internal.key")).read().strip(), cid, inst, token, "netbanking.mockbank.test", rec)
    assert out["decision"] != "verified"
    assert _audit().returncode == 0          # the audit re-verifies it under the bridge key and re-applies the cap
