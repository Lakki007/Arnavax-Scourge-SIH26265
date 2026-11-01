"""The browser-extension path's portal contract: a decrypted FD record, bridged to /internal/evidence,
must produce exactly the verdict the CLI verifier would. No bank needed; the extension's MPC core is out of scope
here — this tests the Scourge glue (field mapping + internal post) end to end against the running portal."""
import os
import sqlite3
import sys

import httpx
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools", "extension"))
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
import bridge  # noqa: E402

def _key():
    return open(os.path.join(VARDIR, "internal.key")).read().strip()
HOST = "netbanking.mockbank.test"


def _challenge(off, fdr):
    off.post(f"/fdrs/{fdr}/challenge")
    return sqlite3.connect(T.DB).execute(
        "SELECT id, token, institution_id FROM challenges WHERE fdr_id=? AND state='issued' ORDER BY issued_at DESC", (fdr,)).fetchone()


def _record(fd, **over):
    r = {"depositNo": fd, "status": "ACTIVE", "currentBalance": "2500000.00", "principal": "2500000.00",
         "maturityDate": "2029-01-15", "holders": "SYNTHETIC / MEMBER SECRETARY AICTE", "lien": "AICTE NEW DELHI (SECURITY DEPOSIT)"}
    r.update(over)
    return r


@pytest.fixture()
def off():
    o = T.S("officer"); o.post("/system/reset")
    return o


def _decision(cid):
    return sqlite3.connect(T.DB).execute("SELECT decision, reason FROM evidence WHERE challenge_id=?", (cid,)).fetchone()


def test_bridged_good_record_verifies(off):
    cid, token, inst = _challenge(off, "FDR-002")
    out = bridge.post_record(B, _key(), cid, inst, token, HOST, _record("FD-0000777777"))
    assert out["decision"] == "verified", out
    assert _decision(cid)[0] == "verified"


def test_bridged_loan_lien_is_discrepancy(off):
    cid, token, inst = _challenge(off, "FDR-002")
    rec = _record("FD-0000777777", lien="LOAN 2125000.00 AGAINST DEPOSIT")
    out = bridge.post_record(B, _key(), cid, inst, token, HOST, rec)
    assert out["decision"] == "discrepancy", out


def test_bridged_missing_lien_is_insufficient(off):
    cid, token, inst = _challenge(off, "FDR-002")
    rec = _record("FD-0000777777"); rec.pop("lien")
    out = bridge.post_record(B, _key(), cid, inst, token, HOST, rec)
    assert out["decision"] == "insufficient_evidence", out


def test_bridged_wrong_fd_is_rejected(off):
    cid, token, inst = _challenge(off, "FDR-002")
    out = bridge.post_record(B, _key(), cid, inst, token, HOST, _record("FD-0000999999"))
    assert out["decision"] == "rejected", out


def test_bridge_wraps_a_record_nested_in_a_list(off):
    # SBI-style: the decrypted body is {"accounts":[{...}, {...}]}; the bridge must find the right object.
    cid, token, inst = _challenge(off, "FDR-002")
    body = {"accounts": [_record("FD-0000123456", status="CLOSED"), _record("FD-0000777777")]}
    out = bridge.post_record(B, _key(), cid, inst, token, HOST, body)
    assert out["decision"] == "verified", out


def test_consumed_challenge_cannot_be_bridged_twice(off):
    cid, token, inst = _challenge(off, "FDR-002")
    bridge.post_record(B, _key(), cid, inst, token, HOST, _record("FD-0000777777"))
    with pytest.raises(RuntimeError):
        bridge.post_record(B, _key(), cid, inst, token, HOST, _record("FD-0000777777"))
