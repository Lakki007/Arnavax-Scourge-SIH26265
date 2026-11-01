"""Acceptance tests over HTTP and the real binaries. Requires the app running on 127.0.0.1:8080
(scripts/run.sh). Each test resets the demo first. Run: python3 -m pytest -q tests/test_acceptance.py
These exercise the real prover, the separate verifier and the synthetic bank. They do NOT show the real-bank gate."""
import base64
import json
import os
import re
import sqlite3
import subprocess
import time

import httpx
import pytest

B = os.environ.get("SCOURGE_URL", "http://127.0.0.1:8080")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VARDIR = os.environ.get("SCOURGE_VAR", os.path.join(ROOT, "var"))
DB = os.path.join(VARDIR, "scourge.db")
FD = {"FDR-001": "FD-0000123456", "FDR-002": "FD-0000777777", "FDR-003": "FD-0000555555", "FDR-004": "FD-0000444444"}


class S:
    def __init__(self, role):
        self.c = httpx.Client(base_url=B, follow_redirects=False, timeout=60)
        self.c.post("/signin", data={"role": role, "next": "/", "access_code": os.environ.get("SCOURGE_ACCESS_CODE", "")})
        raw = self.c.cookies["scourge_session"]
        self.csrf = json.loads(base64.urlsafe_b64decode(raw.rsplit(".", 1)[0]))["csrf"]

    def post(self, url, **data):
        return self.c.post(url, data={"csrf": self.csrf, **data})

    def get(self, url):
        return self.c.get(url)


def _server_up():
    try:
        return httpx.get(B + "/api/health", timeout=3).status_code == 200
    except httpx.HTTPError:
        return False


# These tests drive the running app; without it they are skipped (with the reason), not reported as errors.
# CI sets SCOURGE_REQUIRE_SERVER=1 so a dead server fails the build instead of skipping silently.
if os.environ.get("SCOURGE_REQUIRE_SERVER") == "1" and not _server_up():
    raise RuntimeError(f"SCOURGE_REQUIRE_SERVER=1 but the app is not reachable at {B}")
pytestmark = pytest.mark.skipif(not _server_up(), reason=f"app not running at {B}: start ./scripts/run.sh first")


def db():
    return sqlite3.connect(DB)


@pytest.fixture()
def off():
    o = S("officer")
    assert o.post("/system/reset").status_code == 303
    for n in ("verifier", "genuine", "fake", "unlisted"):
        o.post("/system/service", name=n, op="start")
    return o


def challenge(off, fdr):
    r = off.post(f"/fdrs/{fdr}/challenge")
    assert r.status_code == 303, r.text
    return db().execute("SELECT id, token, institution_id FROM challenges WHERE fdr_id=? ORDER BY issued_at DESC", (fdr,)).fetchone()


def bank(off, fdr, behaviour, lien_text=""):
    assert off.post("/mockbank/behaviour", fd_number=FD[fdr], behaviour=behaviour, lien_text=lien_text).status_code == 303


def prove(off, fdr, behaviour="active", inst="INST-42", lien_text="", **mode):
    bank(off, fdr, behaviour, lien_text)
    cid, _, _ = challenge(off, fdr)
    s = S(f"institution:{inst}")
    r = s.post(f"/challenges/{cid}/prove", **mode)
    assert r.status_code == 303, r.text
    jid = r.headers["location"].rsplit("/", 1)[1]
    for _ in range(240):
        j = s.get(f"/api/jobs/{jid}").json()
        if j["state"] != "running":
            return j, cid
        time.sleep(0.25)
    raise AssertionError("job did not finish")


def _approve(sess, url, **kw):
    """Approve as a browser would: send the case version shown on the page (mandatory)."""
    if "version" not in kw:
        m = re.search(r'name="version" value="([^"]+)"', sess.get(url.rsplit("/release/", 1)[0]).text)
        kw["version"] = m.group(1) if m else ""
    return sess.post(url, **kw)


def evidence(cid):
    r = db().execute("SELECT decision, reason, facts_json, match_json FROM evidence WHERE challenge_id=?", (cid,)).fetchone()
    return r[0], r[1], json.loads(r[2]), json.loads(r[3])


# ---------------------------------------------------------------- central journey + negative controls
@pytest.mark.parametrize("behaviour,mode,decision,why", [
    ("active", {}, "verified", None),
    ("active", {"endpoint": "summary"}, "verified", None),
    ("escaped_ws", {}, "verified", None),
    ("chunked", {}, "verified", None),
    ("closed", {}, "discrepancy", "status"),
    ("reduced", {}, "discrepancy", "balance"),
    ("maturity_changed", {}, "discrepancy", "maturity"),
    ("lien_loan", {}, "discrepancy", "lien"),
    ("no_status", {}, "insufficient_evidence", None),
    ("duplicate_status", {}, "insufficient_evidence", None),
    ("malformed", {}, "insufficient_evidence", "fd_record_not_disclosed"),  # prover cannot parse it, so discloses no record
    ("redirect", {}, "insufficient_evidence", "redirect"),
    ("login", {}, "insufficient_evidence", "unexpected_content_type"),
    ("unrelated", {}, "rejected", "fd_number_differs_from_challenge"),
])
def test_bank_behaviours(off, behaviour, mode, decision, why):
    j, cid = prove(off, "FDR-001", behaviour, **mode)
    assert j["state"] == "done", j
    d, reason, facts, match = evidence(cid)
    assert d == decision, (d, reason)
    if why in ("status", "balance", "maturity", "lien"):
        assert match[why]["result"] == "mismatch"
    elif why:
        assert reason == why


def test_lien_not_shown_is_unknown_not_clear(off):
    # A bank page that cannot show loans must not produce "Verified": the lien is the point of the check.
    j, cid = prove(off, "FDR-001", "no_lien")
    d, reason, _, match = evidence(cid)
    assert d == "insufficient_evidence" and match["lien"]["result"] == "not_exposed"
    assert reason == "required_field_unknown:lien"
    assert "lien or loan details" in off.get("/fdrs/FDR-001").text


def test_body_disclosure_privacy_guard(off):
    j, cid = prove(off, "FDR-001", "two_fdrs")
    assert "privacy guard" in (j["error"] or "")  # the institution is told why its own prover stopped
    d, reason, facts, _ = evidence(cid)
    assert d == "rejected" and reason == "tls_proof_failed"  # prover refused to disclose two deposits
    assert "FD-0000777777" not in json.dumps(facts)


def test_record_disclosure_hides_pan_and_other_accounts(off):
    j, cid = prove(off, "FDR-001", "active", endpoint="summary")
    _, _, facts, _ = evidence(cid)
    blob = json.dumps(facts)
    for secret in ("AAATS0000X", "SB-SYN-1111", "FD-0000777777", "CIF-SYN-0042", "NB_SESSION"):
        assert secret not in blob


def test_hidden_field_is_insufficient(off):
    j, cid = prove(off, "FDR-001", "active", endpoint="summary", tamper="hide_lien")
    d, reason, _, _ = evidence(cid)
    assert d == "insufficient_evidence", (d, reason)


# ---------------------------------------------------------------- attacks
def test_fake_bank_rejected_on_certificate(off):
    j, cid = prove(off, "FDR-001", target="fake")
    d, reason, facts, _ = evidence(cid)
    assert d == "rejected" and "certificate" in facts["stages"]["tls_proof"]


def test_unapproved_host_rejected(off):
    j, cid = prove(off, "FDR-001", target="unlisted")
    d, reason, _, _ = evidence(cid)
    assert d == "rejected" and reason == "host_not_allow_listed"


def _cli(cid, inst, token, fd="FD-0000123456"):
    hdr = os.path.join(VARDIR, "jobs", "test.headers")
    sess = next(k for k, v in json.load(open(os.path.join(VARDIR, "bank_state.json")))["sessions"].items() if v == "INST-42")
    open(hdr, "w").write(f"Cookie: NB_SESSION={sess}\n")
    out = subprocess.run([os.path.join(ROOT, "bin", "scourge_prove"), "--config", "config/scourge.json", "--challenge-id", cid, "--institution", inst,
                          "--token", token, "--fd-number", fd, "--url", f"https://netbanking.mockbank.test/api/fd/details?fd={fd}",
                          "--headers-file", hdr, "--connect-addr", "127.0.0.1:4400", "--extra-ca-der", "certs/test_ca.der",
                          "--max-sent", "2048", "--max-recv", "16384"], cwd=ROOT, capture_output=True, text=True, timeout=60)
    os.unlink(hdr)
    return out.stdout


def test_replay_wrong_institution_unknown(off):
    j, cid = prove(off, "FDR-001")
    token = db().execute("SELECT token FROM challenges WHERE id=?", (cid,)).fetchone()[0]
    assert "challenge_already_used" in _cli(cid, "INST-42", token)
    cid2, token2, _ = challenge(off, "FDR-002")
    assert "challenge_wrong_institution" in _cli(cid2, "INST-77", token2)
    assert "challenge_unknown" in _cli(cid2, "INST-42", "00" * 32)
    # the refused attempts did not consume the challenge
    assert db().execute("SELECT state FROM challenges WHERE id=?", (cid2,)).fetchone()[0] == "issued"


def test_expired_challenge(off):
    cid, token, _ = challenge(off, "FDR-001")
    with db() as c:
        c.execute("UPDATE challenges SET expires_at=? WHERE id=?", (time.time() - 1, cid))
    assert "challenge_expired" in _cli(cid, "INST-42", token)
    s = S("institution:INST-42")
    assert s.post(f"/challenges/{cid}/prove").status_code == 409


def test_proof_for_another_fdr_rejected(off):
    # challenge for FDR-002, prover discloses FDR-001's record
    cid, token, _ = challenge(off, "FDR-002")
    out = _cli(cid, "INST-42", token, fd="FD-0000123456")
    time.sleep(1)
    d, reason, _, _ = evidence(cid)
    assert d == "rejected" and reason in ("fd_number_differs_from_challenge", "host_differs_from_challenge_bank"), (d, reason, out)


# ---------------------------------------------------------------- failed dependencies
def test_verifier_down_keeps_challenge_and_retry_works(off):
    off.post("/system/service", name="verifier", op="stop")
    j, cid = prove(off, "FDR-001")
    assert j["state"] == "failed" and "unreachable" in j["error"]
    assert db().execute("SELECT state FROM challenges WHERE id=?", (cid,)).fetchone()[0] == "issued"
    off.post("/system/service", name="verifier", op="start")
    s = S("institution:INST-42")
    r = s.post(f"/challenges/{cid}/prove")
    jid = r.headers["location"].rsplit("/", 1)[1]
    for _ in range(200):
        j = s.get(f"/api/jobs/{jid}").json()
        if j["state"] != "running":
            break
        time.sleep(0.25)
    assert j["state"] == "done" and j["decision"] == "verified"


def test_bank_down_is_rejected_with_reason_not_faked(off):
    off.post("/system/service", name="genuine", op="stop")
    j, cid = prove(off, "FDR-001")
    d, reason, facts, _ = evidence(cid)
    assert d == "rejected" and reason == "tls_proof_failed"
    off.post("/system/service", name="genuine", op="start")


def test_cancel_running_proof(off):
    bank(off, "FDR-001", "slow")
    cid, _, _ = challenge(off, "FDR-001")
    s = S("institution:INST-42")
    jid = s.post(f"/challenges/{cid}/prove").headers["location"].rsplit("/", 1)[1]
    time.sleep(1.5)
    assert s.post(f"/jobs/{jid}/cancel").status_code == 303
    for _ in range(80):
        j = s.get(f"/api/jobs/{jid}").json()
        if j["state"] != "running":
            break
        time.sleep(0.25)
    assert j["state"] == "cancelled"


# ---------------------------------------------------------------- permissions, validation, gates
def test_permissions(off):
    i77 = S("institution:INST-77")
    assert i77.get("/fdrs/FDR-001").status_code == 404           # another institution's FDR
    assert i77.get("/register").status_code == 403
    assert i77.post("/fdrs/FDR-003/challenge").status_code == 403
    assert i77.post("/fdrs/FDR-003/decide", action="accept", evidence_id="1").status_code == 403
    anon = httpx.Client(base_url=B, follow_redirects=False)
    assert anon.get("/register").status_code == 303                # redirected to sign-in
    # 403 from the app; 404 when a deployed reverse proxy blocks /internal/ outright (deploy/nginx.conf)
    assert anon.post("/internal/evidence", json={}).status_code in (403, 404)
    assert anon.post("/internal/challenges/consume", json={}, headers={"x-internal-key": "wrong"}).status_code in (403, 404)
    r = off.c.post("/fdrs/FDR-001/challenge", data={"csrf": "forged"})
    assert r.status_code == 403                                       # CSRF


def test_fdr_form_validation(off):
    r = off.post("/fdrs/new", institution_id="INST-42", fd_number="bad no!", bank_host="evil.example", principal="-5", issue_date="2099-01-01", maturity="2000-01-01")
    assert r.status_code == 422
    for msg in ("letters, digits or hyphens", "approved", "principal in rupees", "cannot be in the future", "must mature in the future"):
        assert msg in r.text
    assert off.post("/fdrs/new", institution_id="INST-42", fd_number="FD-0000123456", bank_host="netbanking.mockbank.test", principal="1000", issue_date="2024-01-01", maturity="2034-01-01").status_code == 422
    r = off.post("/fdrs/new", institution_id="INST-42", fd_number="FD-0000888888", bank_host="netbanking.mockbank.test", principal="750000", issue_date="2024-01-01", maturity="2034-01-01")
    assert r.status_code == 303


def test_new_fdr_not_at_bank_then_opened(off):
    off.post("/fdrs/new", institution_id="INST-42", fd_number="FD-0000888888", bank_host="netbanking.mockbank.test", principal="750000", issue_date="2024-01-01", maturity="2034-01-01")
    fid = db().execute("SELECT id FROM fdrs WHERE fd_number='FD-0000888888'").fetchone()[0]
    FD[fid] = "FD-0000888888"
    cid, _, _ = challenge(off, fid)
    s = S("institution:INST-42")
    jid = s.post(f"/challenges/{cid}/prove").headers["location"].rsplit("/", 1)[1]
    for _ in range(200):
        j = s.get(f"/api/jobs/{jid}").json()
        if j["state"] != "running":
            break
        time.sleep(0.25)
    assert j["decision"] == "insufficient_evidence"                   # bank: no such deposit (404)
    assert off.post("/mockbank/open", fd_number="FD-0000888888").status_code == 303
    j, cid = prove(off, fid)
    assert j["decision"] == "verified"


def test_release_gate(off):
    assert off.post("/fdrs/FDR-001/decide", action="release", evidence_id="0", note="attempt without proof").status_code == 409
    j, cid = prove(off, "FDR-001", "closed")
    eid = db().execute("SELECT id FROM evidence WHERE challenge_id=?", (cid,)).fetchone()[0]
    assert off.post("/fdrs/FDR-001/decide", action="accept", evidence_id=str(eid)).status_code == 409   # discrepancy cannot be accepted
    assert off.post("/fdrs/FDR-001/decide", action="escalate", evidence_id=str(eid), note="short").status_code == 422
    assert off.post("/fdrs/FDR-001/decide", action="escalate", evidence_id=str(eid), note="Bank shows the deposit closed early").status_code == 303
    assert off.post("/fdrs/FDR-001/decide", action="release", evidence_id=str(eid), note="should be blocked here").status_code == 409


def test_baseline_upload_validation(off):
    def up(name, data, mime):
        return off.c.post("/baseline", data={"csrf": off.csrf, "fdr_id": "FDR-001"}, files={"file": (name, data, mime)})
    assert up("x.png", b"", "image/png").status_code == 422
    assert up("x.png", b"<script>alert(1)</script>", "image/png").status_code == 422      # content checked, not name
    assert up("big.png", b"\x89PNG\r\n\x1a\n" + b"0" * (2 * 1024 * 1024 + 10), "image/png").status_code == 422
    assert up("ok.png", b"\x89PNG\r\n\x1a\n" + b"0" * 100, "image/png").status_code == 303


def test_audit_append_only_and_secrets_not_stored(off):
    j, cid = prove(off, "FDR-001")
    c = db()
    with pytest.raises(sqlite3.DatabaseError):
        c.execute("UPDATE events SET actor='x'")
    with pytest.raises(sqlite3.DatabaseError):
        c.execute("DELETE FROM events")
    c.rollback()
    c.close()
    c = db()
    sessions = list(json.load(open(os.path.join(VARDIR, "bank_state.json")))["sessions"])
    blob = " ".join(str(r) for r in c.execute("SELECT * FROM events").fetchall()) + " ".join(str(r) for r in c.execute("SELECT facts_json FROM evidence").fetchall())
    logs = "".join(open(os.path.join(VARDIR, "logs", f)).read() for f in os.listdir(os.path.join(VARDIR, "logs")))
    for sess in sessions:
        assert sess not in blob and sess not in logs
    assert off.get("/audit.csv").status_code == 200
    exp = off.get("/evidence/1/export")
    assert exp.status_code == 200 and json.loads(exp.text)["decision"] == "verified"


def test_reset_repeat_cycle(off):
    j, _ = prove(off, "FDR-001")
    assert j["decision"] == "verified"
    assert off.post("/system/reset").status_code == 303
    assert db().execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 0
    j, _ = prove(off, "FDR-001")
    assert j["decision"] == "verified"


# ---------------------------------------------------------------- audit regressions (judge-driven inputs)
def test_input_drives_output_bank_principal_differs(off):
    """A fresh FDR with the judge's own numbers: the witness is the bank's value, not a copy of the register."""
    r = off.post("/fdrs/new", institution_id="INST-77", fd_number="FD-AUDIT-0001", bank_host="netbanking.mockbank.test",
                 principal="1234567.89", issue_date="2025-03-01", maturity="2031-03-01")
    fid = r.headers["location"].split("/")[2].split("?")[0]
    FD[fid] = "FD-AUDIT-0001"
    assert off.post("/mockbank/open", fd_number="FD-AUDIT-0001", bank_principal="1000000").status_code == 303
    j, cid = prove(off, fid, inst="INST-77")
    d, _, _, match = evidence(cid)
    assert d == "discrepancy"
    assert match["principal"]["result"] == "mismatch" and float(match["principal"]["proven"]) == 1000000.0
    assert match["balance"]["result"] == "mismatch"
    assert match["maturity"]["result"] == "match" and match["maturity"]["proven"] == "2031-03-01"
    assert off.post("/mockbank/open", fd_number="FD-AUDIT-0001").status_code == 409  # repeated action


def test_bank_principal_input_validated(off):
    off.post("/fdrs/new", institution_id="INST-77", fd_number="FD-AUDIT-0002", bank_host="netbanking.mockbank.test",
             principal="500000", issue_date="2025-03-01", maturity="2031-03-01")
    assert off.post("/mockbank/open", fd_number="FD-AUDIT-0002", bank_principal="-5").status_code == 422


def test_no_proof_bytes_for_anonymous_or_other_institution(off):
    j, cid = prove(off, "FDR-001")
    anon = httpx.Client(base_url=B)
    h = anon.get("/").text
    assert "currentBalance" not in h and "/evidence/" not in h and "AICTE received" not in h
    h77 = S("institution:INST-77").get("/").text
    assert "currentBalance" not in h77 and "/evidence/" not in h77


def test_only_officer_controls_verifier_and_reset(off):
    i = S("institution:INST-77")
    assert i.post("/system/service", name="verifier", op="stop").status_code == 403
    assert i.post("/system/reset").status_code == 403
    assert off.get("/api/health").json()["services"]["verifier"] is True


def test_malformed_ids_render_error_page(off):
    r = off.get("/evidence/abc")
    assert r.status_code == 422 and "<html" in r.text.lower() and "Invalid or missing value" in r.text


def test_stale_confirmation_blocks_release(off):
    j, cid = prove(off, "FDR-001")
    eid = db().execute("SELECT id FROM evidence WHERE challenge_id=?", (cid,)).fetchone()[0]
    assert off.post("/fdrs/FDR-001/decide", action="accept", evidence_id=str(eid)).status_code == 303
    c = db(); c.execute("UPDATE evidence SET received_at=received_at-31*86400 WHERE id=?", (eid,)); c.commit()
    assert "Confirmation expired" in off.get("/fdrs/FDR-001").text
    assert off.post("/fdrs/FDR-001/decide", action="release", evidence_id=str(eid), note="RB/REL/2026/0001").status_code == 409


def test_later_contradiction_revokes_release(off):
    j, cid = prove(off, "FDR-001")
    eid = db().execute("SELECT id FROM evidence WHERE challenge_id=?", (cid,)).fetchone()[0]
    off.post("/fdrs/FDR-001/decide", action="accept", evidence_id=str(eid))
    j, cid2 = prove(off, "FDR-001", "closed")
    assert evidence(cid2)[0] == "discrepancy"
    assert off.post("/fdrs/FDR-001/decide", action="release", evidence_id=str(eid), note="RB/REL/2026/0001").status_code == 409
    assert off.post("/fdrs/FDR-001/decide", action="accept", evidence_id=str(eid)).status_code == 409  # stale evidence id


def test_concurrent_prove_clicks_start_one_prover(off):
    import threading
    cid, _, _ = challenge(off, "FDR-002")
    i = S("institution:INST-42")
    codes = []
    ts = [threading.Thread(target=lambda: codes.append(i.post(f"/challenges/{cid}/prove").status_code)) for _ in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(codes).count(303) == 1, codes
    assert db().execute("SELECT COUNT(*) FROM jobs WHERE challenge_id=?", (cid,)).fetchone()[0] == 1
    for _ in range(240):  # let it finish so the next test's reset is not refused
        if db().execute("SELECT state FROM jobs WHERE challenge_id=?", (cid,)).fetchone()[0] != "running":
            break
        time.sleep(0.25)


def test_challenge_page_mid_run_says_running_not_used(off):
    bank(off, "FDR-001", "slow")
    cid, _, _ = challenge(off, "FDR-001")
    i = S("institution:INST-42")
    jid = i.post(f"/challenges/{cid}/prove").headers["location"].rsplit("/", 1)[1]
    time.sleep(1.5)
    assert "running now" in i.get(f"/challenges/{cid}").text
    for _ in range(240):
        if i.get(f"/api/jobs/{jid}").json()["state"] != "running":
            break
        time.sleep(0.25)


def test_orphaned_running_job_does_not_block_reset(off):
    """A proof interrupted by an app restart must not stay 'running' forever (it blocked reset)."""
    c = db()
    c.execute("INSERT INTO jobs(id,challenge_id,fdr_id,institution_id,mode_json,state,started_at,stages_json) VALUES('JOB-orphan','CH-X','FDR-001','INST-42','{}','running',?,'[]')", (time.time(),))
    c.commit()
    assert off.post("/system/reset").status_code == 303
    # the reset wiped jobs; recreate and check the job page path also reaps it
    c = db()
    c.execute("INSERT INTO jobs(id,challenge_id,fdr_id,institution_id,mode_json,state,started_at,stages_json) VALUES('JOB-orphan2','CH-X','FDR-001','INST-42','{}','running',?,'[]')", (time.time(),))
    c.commit()
    cid, _, _ = challenge(off, "FDR-001")
    S("institution:INST-42").get(f"/challenges/{cid}")
    st, err = db().execute("SELECT state, error FROM jobs WHERE id='JOB-orphan2'").fetchone()
    assert st == "failed" and "restarted" in err


def test_oversized_request_refused_before_reading(off):
    r = off.c.post("/baseline", data={"csrf": off.csrf, "fdr_id": "FDR-001"}, files={"file": ("x.png", b"\x89PNG\r\n\x1a\n" + b"0" * (4 * 1024 * 1024), "image/png")})
    assert r.status_code == 413


def test_signin_next_cannot_redirect_off_site(off):
    c = httpx.Client(base_url=B, follow_redirects=False)
    for nxt in ("//evil.example/x", "/\\evil.example"):
        r = c.post("/signin", data={"role": "officer", "next": nxt, "access_code": os.environ.get("SCOURGE_ACCESS_CODE", "")})
        assert r.headers["location"] == "/register", r.headers["location"]


def test_large_register_is_paginated(off):
    c = db()
    c.executemany("INSERT INTO fdrs(id,institution_id,fd_number,bank_host,principal,issue_date,maturity,created_at,created_by) VALUES(?,?,?,?,?,?,?,?,?)",
                  [(f"FDR-P{k:04d}", "INST-42", f"FD-P{k:08d}", "netbanking.mockbank.test", "500000.00", "2025-01-01", "2032-01-01", time.time(), "test") for k in range(450)])
    c.commit()
    r = off.get("/register")
    body = r.text[r.text.index("<tbody>"):]  # the register table; the Needs-attention panel links a few more
    assert "Showing 1–200 of 454" in r.text and body.count('href="/fdrs/FDR-') <= 200
    assert "Showing 401–454 of 454" in off.get("/register?page=3").text
    assert "454 of 454 deposits rest on paper alone" in r.text  # headline counts the whole book, not the page


@pytest.mark.skipif(not os.environ.get("SCOURGE_ACCESS_CODE"), reason="deployment gate is off (SCOURGE_ACCESS_CODE unset)")
def test_deployment_access_code_gate():
    c = httpx.Client(base_url=B, follow_redirects=False)
    assert c.post("/signin", data={"role": "officer"}).status_code == 403
    assert c.post("/signin", data={"role": "officer", "access_code": "wrong"}).status_code == 403
    assert c.get("/register").status_code == 303  # still signed out
    ok = c.post("/signin", data={"role": "officer", "access_code": os.environ["SCOURGE_ACCESS_CODE"]})
    assert ok.status_code == 303 and "secure" in ok.headers["set-cookie"].lower() if os.environ.get("SCOURGE_COOKIE_SECURE") == "1" else ok.status_code == 303


@pytest.mark.skipif(not os.environ.get("SCOURGE_ACCESS_CODE"), reason="deployment gate is off (SCOURGE_ACCESS_CODE unset)")
def test_signed_in_role_switch_needs_no_code_again(off):
    # the officer's "Switch to INST-42 to answer it" button posts a sign-in without the code
    r = off.c.post("/signin", data={"role": "institution:INST-42", "next": "/portal"})
    assert r.status_code == 303 and r.headers["location"] == "/portal"



def test_institution_files_its_own_fdr(off):
    """Workflow step 1: the institution files its own claim; the server pins it to the signed-in institution."""
    inst = S("institution:INST-42")
    r = inst.post("/fdrs/new", institution_id="INST-77", fd_number="FD-0000999111", bank_host="netbanking.mockbank.test",
                  principal="500000", issue_date="2024-01-01", maturity="2034-01-01")
    assert r.status_code == 303, r.text
    row = db().execute("SELECT institution_id, created_by FROM fdrs WHERE fd_number='FD-0000999111'").fetchone()
    assert row == ("INST-42", "institution")          # cannot file for another institution
    assert inst.get(r.headers["location"]).status_code == 200
    ev = db().execute("SELECT actor FROM events WHERE kind='fdr_registered' ORDER BY rowid DESC").fetchone()
    assert ev[0] == "institution:INST-42"


def test_register_monitors_maturity_and_rechecks(off):
    """Lifecycle monitoring: maturing, matured and re-check-due deposits are surfaced on the register."""
    soon = (__import__("datetime").date.today() + __import__("datetime").timedelta(days=30)).isoformat()
    r = off.post("/fdrs/new", institution_id="INST-42", fd_number="FD-0000555999", bank_host="netbanking.mockbank.test",
                 principal="400000", issue_date="2024-01-01", maturity=soon)
    assert r.status_code == 303
    page = off.get("/register").text
    assert "Needs attention" in page and "Maturing within 90 days" in page and "Re-check due" in page
    i = page.index("Maturing within 90 days")
    assert "FD-0000555999" in page[i:i + 2000]


def test_audit_log_is_hash_chained_and_tamper_evident(off):
    prove(off, "FDR-001")
    v = off.get("/api/audit/verify").json()
    assert v["ok"] and v["count"] > 3 and len(v["head"]) == 64
    assert "Chain intact" in off.get("/audit").text
    # an attacker with direct file access drops the append-only trigger and edits an old entry
    c = db()
    c.execute("DROP TRIGGER events_no_update")
    c.execute("UPDATE events SET detail_json='{\"note\":\"edited\"}' WHERE id=2")
    c.commit()
    v = off.get("/api/audit/verify").json()
    assert not v["ok"] and v["broken_at"] == 2
    assert "Chain broken at entry #2" in off.get("/audit").text


def _confirmed(off, fdr="FDR-001"):
    j, cid = prove(off, fdr)
    eid = db().execute("SELECT id FROM evidence WHERE challenge_id=?", (cid,)).fetchone()[0]
    assert off.post(f"/fdrs/{fdr}/decide", action="accept", evidence_id=str(eid)).status_code == 303
    return eid


MEMO = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
DEPT_REVIEWER = {"PGRC": "PG-01", "VIG": "VG-01", "FIN": "FN-01"}


def _clear(sess, fdr, dept, decision="clear", note="Register checked: nothing pending", memo=MEMO, **extra):
    files = {"memo": ("memo.pdf", memo, "application/pdf")} if memo else None
    return sess.c.post(f"/fdrs/{fdr}/release/clear", data={"csrf": sess.csrf, "dept": dept, "decision": decision, "note": note, **extra}, files=files)


def _clear_all(sess=None, fdr="FDR-001"):
    for d, who in DEPT_REVIEWER.items():
        r = _clear(S(f"officer:{who}"), fdr, d)
        assert r.status_code == 303, r.text


def test_release_needs_two_different_officers(off):
    eid = _confirmed(off)
    assert _approve(off, "/fdrs/FDR-001/release/approve").status_code == 403                # scrutiny officer cannot approve at all
    assert off.post("/fdrs/FDR-001/decide", action="release", evidence_id=str(eid), note="RB/REL/2026/0142 term completed").status_code == 303
    assert "Release proposed" in off.get("/fdrs/FDR-001").text
    assert off.post("/fdrs/FDR-001/decide", action="release", evidence_id=str(eid), note="second proposal attempt").status_code == 409
    assert _approve(off, "/fdrs/FDR-001/release/approve").status_code == 403                # maker cannot be checker
    assert db().execute("SELECT released_at FROM fdrs WHERE id='FDR-001'").fetchone()[0] is None
    inst = S("institution:INST-42")
    assert _approve(inst, "/fdrs/FDR-001/release/approve").status_code == 403               # institutions cannot approve
    rb2 = S("officer:RB-02")
    assert _approve(rb2, "/fdrs/FDR-001/release/approve").status_code == 409                # departments have not cleared yet
    _clear_all(off)
    assert _approve(rb2, "/fdrs/FDR-001/release/approve").status_code == 303
    assert db().execute("SELECT released_at FROM fdrs WHERE id='FDR-001'").fetchone()[0] is None           # issued, not yet executed
    assert db().execute("SELECT state FROM release_requests WHERE fdr_id='FDR-001'").fetchone()[0] == "approved"
    assert "Release instruction issued" in rb2.get("/fdrs/FDR-001").text
    letter = rb2.get("/fdrs/FDR-001/release-letter").text
    assert "AICTE/RB/REL/" in letter and "RB-01" in letter and "RB-02" in letter
    assert inst.get("/fdrs/FDR-001/release-letter").status_code == 200                 # the institution gets its letter
    assert off.get("/api/audit/verify").json()["ok"]


def test_release_approval_blocked_when_confirmation_goes_stale(off):
    eid = _confirmed(off)
    off.post("/fdrs/FDR-001/decide", action="release", evidence_id=str(eid), note="RB/REL/2026/0143 term completed")
    c = db(); c.execute("UPDATE evidence SET received_at=received_at-31*86400 WHERE id=?", (eid,)); c.commit()
    rb2 = S("officer:RB-02")
    assert _approve(rb2, "/fdrs/FDR-001/release/approve").status_code == 409
    assert rb2.post("/fdrs/FDR-001/release/reject", note="short").status_code == 422
    assert rb2.post("/fdrs/FDR-001/release/reject", note="Confirmation expired; ask for a fresh proof").status_code == 303
    assert db().execute("SELECT state FROM release_requests WHERE fdr_id='FDR-001'").fetchone()[0] == "rejected"


def test_renewal_links_old_and_new_fdr(off):
    inst = S("institution:INST-42")
    r = inst.post("/fdrs/new", renews="FDR-001", institution_id="INST-42", fd_number="FD-0000123999", bank_host="netbanking.mockbank.test",
                  principal="1000000", issue_date="2025-01-01", maturity="2035-01-01")
    assert r.status_code == 303, r.text
    new = db().execute("SELECT id, renews_fdr_id FROM fdrs WHERE fd_number='FD-0000123999'").fetchone()
    assert new[1] == "FDR-001"
    FD[new[0]] = "FD-0000123999"
    page = off.get("/fdrs/FDR-001").text
    assert "Renewed into" in page and "FD-0000123999" in page
    assert "Renews" in off.get(f"/fdrs/{new[0]}").text
    # a second renewal of the same FDR, or renewing another institution's FDR, is refused
    assert inst.post("/fdrs/new", renews="FDR-001", institution_id="INST-42", fd_number="FD-0000124000", bank_host="netbanking.mockbank.test",
                     principal="1000000", issue_date="2025-01-01", maturity="2035-01-01").status_code == 422
    assert inst.post("/fdrs/new", renews="FDR-003", institution_id="INST-42", fd_number="FD-0000124001", bank_host="netbanking.mockbank.test",
                     principal="1000000", issue_date="2025-01-01", maturity="2035-01-01").status_code == 422
    # once the bank confirms the renewed deposit, the old one counts as renewed and cannot be released separately
    assert off.post("/mockbank/open", fd_number="FD-0000123999").status_code == 303
    _confirmed(off, new[0])
    assert "Renewed" in off.get("/fdrs/FDR-001").text
    eid_old = db().execute("SELECT COALESCE(MAX(id),0) FROM evidence WHERE fdr_id='FDR-001'").fetchone()[0]
    assert off.post("/fdrs/FDR-001/decide", action="release", evidence_id=str(eid_old), note="should not release old").status_code == 409


def _upload_eadvice(sess, fdr, doc):
    return sess.c.post(f"/fdrs/{fdr}/eadvice", data={"csrf": sess.csrf},
                       files={"file": ("e-advice.json", json.dumps(doc).encode(), "application/json")})


def test_eadvice_fallback_completes_missing_lien(off):
    j, cid = prove(off, "FDR-002", "no_lien")
    assert j["decision"] == "insufficient_evidence"
    inst = S("institution:INST-42")
    bank(off, "FDR-002", "active")                                      # the bank's e-advice desk reports the true lien
    doc = inst.get(f"/mockbank/eadvice/{FD['FDR-002']}").json()
    assert _upload_eadvice(inst, "FDR-002", doc).status_code == 303
    page = off.get("/fdrs/FDR-002").text
    assert "E-advice verified" in page and "Fills: lien" in page
    eid = db().execute("SELECT id FROM evidence WHERE challenge_id=?", (cid,)).fetchone()[0]
    assert off.post("/fdrs/FDR-002/decide", action="accept", evidence_id=str(eid)).status_code == 303
    assert "Confirmed by bank session" in off.get("/fdrs/FDR-002").text
    note = db().execute("SELECT note FROM actions WHERE fdr_id='FDR-002' AND action='accept'").fetchone()[0]
    assert "bank-signed e-advice" in note


def test_eadvice_showing_a_loan_is_a_discrepancy_not_acceptable(off):
    j, cid = prove(off, "FDR-002", "no_lien")
    inst = S("institution:INST-42")
    bank(off, "FDR-002", "lien_loan")
    doc = inst.get(f"/mockbank/eadvice/{FD['FDR-002']}").json()
    assert "LOAN" in doc["payload"]["fields"]["lien"]
    _upload_eadvice(inst, "FDR-002", doc)
    assert "the result is <b>Discrepancy</b>" in off.get("/fdrs/FDR-002").text
    eid = db().execute("SELECT id FROM evidence WHERE challenge_id=?", (cid,)).fetchone()[0]
    assert off.post("/fdrs/FDR-002/decide", action="accept", evidence_id=str(eid)).status_code == 409


def test_forged_or_altered_eadvice_is_rejected(off):
    from cryptography.hazmat.primitives import serialization  # noqa: F401
    j, cid = prove(off, "FDR-002", "no_lien")
    inst = S("institution:INST-42")
    bank(off, "FDR-002", "lien_loan")
    doc = inst.get(f"/mockbank/eadvice/{FD['FDR-002']}").json()
    doc["payload"]["fields"]["lien"] = "AICTE NEW DELHI (SECURITY DEPOSIT)"   # institution edits out the loan
    _upload_eadvice(inst, "FDR-002", doc)
    assert "E-advice rejected" in off.get("/fdrs/FDR-002").text and "altered or forged" in off.get("/fdrs/FDR-002").text
    # signed by the institution's own fake CA instead of a trusted bank root
    import sys; sys.path.insert(0, ROOT)
    from app import eadvice as ea
    fake = ea.issue("netbanking.mockbank.test", FD["FDR-002"], {"lien": "AICTE NEW DELHI"},
                    open(os.path.join(ROOT, "certs/bank_fake.key.pem"), "rb").read(), open(os.path.join(ROOT, "certs/bank_fake.pem"), "rb").read())
    _upload_eadvice(inst, "FDR-002", fake)
    assert "not issued by a trusted bank root" in off.get("/fdrs/FDR-002").text
    eid = db().execute("SELECT id FROM evidence WHERE challenge_id=?", (cid,)).fetchone()[0]
    assert off.post("/fdrs/FDR-002/decide", action="accept", evidence_id=str(eid)).status_code == 409


def test_regulatory_hold_freezes_release(off):
    eid = _confirmed(off)
    assert off.post("/fdrs/FDR-001/hold", reason="short").status_code == 422
    assert off.post("/fdrs/FDR-001/hold", reason="Faculty salary grievance pending at RO").status_code == 303
    assert "On hold" in off.get("/fdrs/FDR-001").text
    assert off.post("/fdrs/FDR-001/decide", action="release", evidence_id=str(eid), note="RB/REL/2026/0150 term completed").status_code == 409
    assert off.post("/fdrs/FDR-001/hold/lift", note="Grievance resolved, RO letter 12/2026").status_code == 303
    assert off.post("/fdrs/FDR-001/decide", action="release", evidence_id=str(eid), note="RB/REL/2026/0150 term completed").status_code == 303
    _clear_all(off)
    off.post("/fdrs/FDR-001/hold", reason="New court case filed against the trust")
    assert _approve(S("officer:RB-02"), "/fdrs/FDR-001/release/approve").status_code == 409   # hold also blocks approval


def test_release_letter_is_signed_and_verifiable_offline(off):
    import base64 as b64
    from cryptography.hazmat.primitives import serialization
    eid = _confirmed(off)
    off.post("/fdrs/FDR-001/decide", action="release", evidence_id=str(eid), note="RB/REL/2026/0160 term completed")
    _clear_all(off)
    rb2 = S("officer:RB-02")
    assert _approve(rb2, "/fdrs/FDR-001/release/approve").status_code == 303
    body = rb2.get("/fdrs/FDR-001/release-letter.json").json()
    pub = serialization.load_pem_public_key(httpx.get(B + "/api/aicte-release-key").text.encode())
    pub.verify(b64.b64decode(body["signature_b64"]), body["signed_bytes"].encode())          # raises if invalid
    assert body["payload"]["approved_by"] == "RB-02" and body["payload"]["proposed_by"] == "RB-01"
    import pytest as _p
    from cryptography.exceptions import InvalidSignature
    with _p.raises(InvalidSignature):
        pub.verify(b64.b64decode(body["signature_b64"]), body["signed_bytes"].replace("RB-02", "RB-01").encode())



def test_deposit_adequacy_table_1_4(off):
    page = off.get("/institutions").text
    # INST-42: Engineering UG 35 L + Management PG 15 L = 50 L required; filed 10 + 25 + 5 = 40 L
    assert "₹50,00,000" in page and "Shortfall ₹10,00,000" in page
    assert "Adequate as filed" in page                                     # INST-77: Computer Applications PG 15 L, filed 15 L
    reg = off.get("/register").text
    assert "Deposit shortfall" in reg and "INST-42" in reg[reg.index("mon-shortfall"):reg.index("mon-termgap")]
    # leased land doubles the requirement
    assert off.c.post("/institutions/INST-77/profile", data={"csrf": off.csrf, "prog": "computer_applications:pg", "leased": "1"}).status_code == 303
    assert "Shortfall ₹15,00,000" in off.get("/institutions").text
    # exemption brings it to zero
    off.c.post("/institutions/INST-77/profile", data={"csrf": off.csrf, "prog": "computer_applications:pg", "exempt": "government"})
    inst77 = off.get("/institutions").text.split('id="INST-77"')[1]
    assert "Adequate as filed" in inst77[:2000]
    # the institution sees its own position
    assert "Shortfall ₹10,00,000" in S("institution:INST-42").get("/portal").text


def test_term_gap_and_reminder_ladder(off):
    import datetime as dt
    off.post("/fdrs/new", institution_id="INST-42", fd_number="FD-0000555123", bank_host="netbanking.mockbank.test",
             principal="300000", issue_date="2024-01-01", maturity="2029-01-01")          # 5-year FD: term gap
    assert "Term shorter than 10 years" in off.get("/register").text and "FD-0000555123" in off.get("/register").text
    # FDR-002 matures 2029-01-15: run the daily check as of 20 days before, then after maturity
    r = off.post("/notifications/run", as_of="2028-12-26")
    assert r.status_code == 303
    page = off.get("/notifications").text
    assert "Maturity reminder T-30" in page
    n1 = db().execute("SELECT COUNT(*) FROM notifications").fetchone()[0]
    off.post("/notifications/run", as_of="2028-12-26")                                 # idempotent
    assert db().execute("SELECT COUNT(*) FROM notifications").fetchone()[0] == n1
    off.post("/notifications/run", as_of="2029-02-10")                                 # 26 days overdue
    esc = {r[0] for r in db().execute("SELECT audience FROM notifications WHERE kind='overdue_escalation' AND fdr_id='FDR-002'")}
    assert esc == {"officer", "regional_head", "authority"}
    assert db().execute("SELECT COUNT(*) FROM notifications WHERE channel='email' AND status LIKE 'queued%'").fetchone()[0] > 0
    inst = S("institution:INST-42").get("/notifications").text
    assert "Maturity reminder" in inst and "Escalated to" not in inst              # escalations are internal to AICTE


def test_clearance_objection_blocks_and_second_signer_above_one_crore(off):
    # a deposit of 1.2 crore needs two signatures besides the proposer
    off.post("/fdrs/new", institution_id="INST-77", fd_number="FD-0000990001", bank_host="netbanking.mockbank.test",
             principal="12000000", issue_date="2024-01-01", maturity="2034-01-01")
    fid = db().execute("SELECT id FROM fdrs WHERE fd_number='FD-0000990001'").fetchone()[0]
    FD[fid] = "FD-0000990001"
    assert off.post("/mockbank/open", fd_number="FD-0000990001").status_code == 303
    j, cid = prove(off, fid, inst="INST-77")
    eid = db().execute("SELECT id FROM evidence WHERE challenge_id=?", (cid,)).fetchone()[0]
    assert off.post(f"/fdrs/{fid}/decide", action="accept", evidence_id=str(eid)).status_code == 303
    assert off.post(f"/fdrs/{fid}/decide", action="release", evidence_id=str(eid), note="RB/REL/2026/0200 term completed").status_code == 303
    vg = S("officer:VG-01")
    assert _clear(vg, fid, "VIG", "object", note="short").status_code == 422
    assert _clear(vg, fid, "VIG", "object", note="Vigilance case pending against trust").status_code == 303
    _clear(S("officer:PG-01"), fid, "PGRC"); _clear(S("officer:FN-01"), fid, "FIN")
    rb2, rb3 = S("officer:RB-02"), S("officer:RB-03")
    assert _approve(rb2, f"/fdrs/{fid}/release/approve").status_code == 409                # objection blocks
    rb2.post(f"/fdrs/{fid}/release/reject", note="Vigilance objection; resubmit later")
    # second proposal, all clear
    off.post(f"/fdrs/{fid}/decide", action="release", evidence_id=str(eid), note="RB/REL/2026/0201 term completed")
    _clear_all(off, fid)
    assert _approve(rb2, f"/fdrs/{fid}/release/approve").status_code == 303                # first signature only
    assert db().execute("SELECT released_at FROM fdrs WHERE id=?", (fid,)).fetchone()[0] is None
    assert _approve(rb2, f"/fdrs/{fid}/release/approve").status_code == 409                # same officer cannot sign twice
    assert _approve(rb3, f"/fdrs/{fid}/release/approve").status_code == 303
    assert db().execute("SELECT state FROM release_requests WHERE fdr_id=? ORDER BY id DESC", (fid,)).fetchone()[0] == "approved"
    body = rb3.get(f"/fdrs/{fid}/release-letter.json").json()
    assert body["payload"]["signed_by"] == ["RB-02", "RB-03"] and body["payload"]["clearances"] == ["FIN", "PGRC", "VIG"]


def test_release_letter_qr_and_public_verify(off):
    import re as _re, base64 as b64
    eid = _confirmed(off)
    off.post("/fdrs/FDR-001/decide", action="release", evidence_id=str(eid), note="RB/REL/2026/0300 term completed")
    _clear_all(off)
    _approve(S("officer:RB-02"), "/fdrs/FDR-001/release/approve")
    letter = off.get("/fdrs/FDR-001/release-letter").text
    assert 'id="letter-qr"' in letter
    url = _re.search(r'href="(http[^"]*/verify\?d=[^"]+)"', letter).group(1).replace("&amp;", "&")
    pub = httpx.get(url)                                                              # public: no sign-in
    assert pub.status_code == 200 and "Valid AICTE signature" in pub.text
    d = _re.search(r"d=([^&]+)", url).group(1)
    raw = b64.urlsafe_b64decode(d + "=" * (-len(d) % 4)).replace(b"RB-02", b"RB-09")
    forged = url.replace(d, b64.urlsafe_b64encode(raw).decode().rstrip("="))
    assert "Invalid." in httpx.get(forged).text


def _branch_link_and_code():
    rows = db().execute("SELECT body FROM branch_outbox ORDER BY id DESC LIMIT 2").fetchall()
    link = next(r[0] for r in rows if "http" in r[0]).split(": ", 1)[1]
    code = next(r[0] for r in rows if "One-time code" in r[0]).split(": ")[1]
    return link, code


def test_branch_confirmation_tier3(off):
    j, cid = prove(off, "FDR-002", "login")                                  # session proof cannot be completed
    assert j["decision"] in ("insufficient_evidence", "rejected")
    assert "Request branch confirmation" in off.get("/fdrs/FDR-002").text
    assert off.post("/fdrs/FDR-002/branch/request").status_code == 303
    assert off.post("/fdrs/FDR-002/branch/request").status_code == 409       # one open request at a time
    link, code = _branch_link_and_code()
    assert "fd-desk.hyderabad@mockbank.test" in off.get("/mockbank/branch-inbox").text
    assert S("institution:INST-42").get("/mockbank/branch-inbox").status_code == 403
    path = link.split("8080", 1)[1]
    anon = httpx.Client(base_url=B)
    assert anon.get(path.split("?")[0] + "?t=wrong").status_code == 404     # bad token
    form = {"t": path.split("t=")[1], "status": "ACTIVE", "balance": "2500000.00", "maturity": "2029-01-15",
            "holders": "SYNTHETIC INSTITUTE OF TECHNOLOGY TRUST / MEMBER SECRETARY AICTE", "lien": "AICTE NEW DELHI (SECURITY DEPOSIT)",
            "officer_name": "R. Rao, Branch Manager"}
    assert anon.post(path.split("?")[0], data={**form, "code": "000000" if code != "000000" else "111111"}).status_code == 403
    r = anon.post(path.split("?")[0], data={**form, "code": code})
    assert r.status_code == 200 and "Thank you" in r.text
    assert anon.post(path.split("?")[0], data={**form, "code": code}).status_code == 409   # single use
    e = db().execute("SELECT id, decision, facts_json FROM evidence WHERE fdr_id='FDR-002' ORDER BY id DESC").fetchone()
    assert e[1] == "verified" and json.loads(e[2])["tier"] == "branch_confirmation"
    assert "Bank branch (tier 3)" in off.get("/register").text
    assert off.post("/fdrs/FDR-002/decide", action="accept", evidence_id=str(e[0])).status_code == 303
    assert "Confirmed by bank branch" in off.get("/fdrs/FDR-002").text


def test_branch_cannot_override_session_discrepancy(off):
    j, cid = prove(off, "FDR-002", "lien_loan")
    assert j["decision"] == "discrepancy"
    j, cid = prove(off, "FDR-002", "login")
    off.post("/fdrs/FDR-002/branch/request")
    link, code = _branch_link_and_code()
    path = link.split("8080", 1)[1]
    r = httpx.post(B + path.split("?")[0], data={"t": path.split("t=")[1], "code": code, "status": "ACTIVE", "balance": "2500000.00",
                   "maturity": "2029-01-15", "holders": "X / MEMBER SECRETARY AICTE", "lien": "AICTE NEW DELHI", "officer_name": "Colluding clerk"})
    assert r.status_code == 200
    e = db().execute("SELECT decision, reason FROM evidence WHERE fdr_id='FDR-002' ORDER BY id DESC").fetchone()
    assert e == ("discrepancy", "session_proof_outranks")


def test_document_vault_encrypts_checks_and_detects_tampering(off):
    import glob
    inst = S("institution:INST-42")
    pdf = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
    up = lambda sess, data, name="fdr.pdf": sess.c.post("/fdrs/FDR-001/documents", data={"csrf": sess.csrf, "kind": "fdr_receipt"},
                                                         files={"file": (name, data, "application/pdf")})
    assert up(inst, pdf).status_code == 303
    assert up(inst, b"%PDF-1.4\n/JavaScript (alert(1))\n%%EOF").status_code == 422          # active content refused
    assert up(inst, b"MZ\x90\x00 not a pdf", "x.pdf").status_code == 422                   # checked by content
    row = db().execute("SELECT id, stored_as, sha256 FROM documents WHERE fdr_id='FDR-001'").fetchone()
    stored = os.path.join(VARDIR, "vault", row[1])
    assert pdf not in open(stored, "rb").read()                                            # ciphertext on disk
    assert off.get(f"/documents/{row[0]}").status_code == 422                              # officer must give a reason
    r = off.get(f"/documents/{row[0]}?reason=Scrutiny%20of%20renewal%20file")
    assert r.status_code == 200 and r.content == pdf
    assert inst.get(f"/documents/{row[0]}").content == pdf                                 # owner, no reason needed
    assert S("institution:INST-77").get(f"/documents/{row[0]}").status_code == 404         # other institution
    data = bytearray(open(stored, "rb").read()); data[5] ^= 1; open(stored, "wb").write(bytes(data))   # tamper on disk
    assert off.get(f"/documents/{row[0]}?reason=Scrutiny%20of%20renewal%20file").status_code == 409
    assert db().execute("SELECT COUNT(*) FROM events WHERE kind='document_integrity_failed'").fetchone()[0] == 1
    assert db().execute("SELECT COUNT(*) FROM notifications WHERE kind='document_integrity_failed'").fetchone()[0] == 1


def _signin_raw(role, otp=""):
    c = httpx.Client(base_url=B, follow_redirects=False, timeout=60)
    r = c.post("/signin", data={"role": role, "next": "/", "access_code": os.environ.get("SCOURGE_ACCESS_CODE", ""), "otp": otp})
    return c, r


def test_two_factor_signin_lockout_and_step_up(off):
    import sys; sys.path.insert(0, ROOT)
    from app import totp
    rb3 = S("officer:RB-03")
    assert rb3.get("/account/mfa").status_code == 200
    secret = db().execute("SELECT secret FROM officer_mfa WHERE officer='RB-03'").fetchone()[0]
    assert rb3.post("/account/mfa/enable", otp="000000" if totp.code_at(secret, totp.now_step()) != "000000" else "111111").status_code == 422
    assert rb3.post("/account/mfa/enable", otp=totp.code_at(secret, totp.now_step())).status_code == 303
    assert "Two-factor sign-in is on" in rb3.get("/account/mfa?enabled=1").text
    # sign-in now needs the code; a replayed code is refused
    c, r = _signin_raw("officer:RB-03")
    assert r.status_code == 403 and "two-factor" in r.text
    c, r = _signin_raw("officer:RB-03", totp.code_at(secret, totp.now_step() + 1))
    assert r.status_code == 303
    # step-up: release approval needs a fresh code
    eid = _confirmed(off)
    off.post("/fdrs/FDR-001/decide", action="release", evidence_id=str(eid), note="RB/REL/2026/0400 term completed")
    _clear_all(off)
    csrf = json.loads(base64.urlsafe_b64decode(c.cookies["scourge_session"].rsplit(".", 1)[0]))["csrf"]
    assert c.post("/fdrs/FDR-001/release/approve", data={"csrf": csrf}).status_code == 403
    db().execute("UPDATE officer_mfa SET last_step=last_step-5 WHERE officer='RB-03'").connection.commit()
    ver = re.search(r'name="version" value="([^"]+)"', c.get("/fdrs/FDR-001").text).group(1)
    assert c.post("/fdrs/FDR-001/release/approve", data={"csrf": csrf, "version": ver, "otp": totp.code_at(secret, totp.now_step())}).status_code == 303
    # lockout after five wrong codes (the missing code above already counted as one)
    for _ in range(4):
        assert _signin_raw("officer:RB-03", "999999")[1].status_code == 403
    assert _signin_raw("officer:RB-03", totp.code_at(secret, totp.now_step()))[1].status_code == 429


def test_hindi_for_institutions(off):
    inst = S("institution:INST-42")
    assert "My deposits" in inst.get("/portal").text
    inst.c.get("/lang/hi")
    page = inst.get("/portal").text
    assert "मेरी जमा राशियाँ" in page and "पंजीकृत जमा राशियाँ" in page and 'lang="hi"' in page
    assert "सुरक्षा जमा में कमी" in inst.get("/notifications").text
    inst.c.get("/lang/en")
    assert "Registered deposits" in inst.get("/portal").text
    off.c.cookies.set("lang", "hi")
    assert 'lang="en"' in off.get("/register").text                       # officer pages stay English


def test_branch_link_works_for_a_signed_in_browser_and_hindi_statuses(off):
    j, cid = prove(off, "FDR-002", "login")
    off.post("/fdrs/FDR-002/branch/request")
    link, code = _branch_link_and_code()
    assert off.get(link.split("8080", 1)[1]).status_code == 200                       # opened while signed in as an officer
    inst = S("institution:INST-42"); inst.c.get("/lang/hi")
    assert "केवल कागज़" in inst.get("/portal").text and "संस्थान द्वारा प्रस्तुत" in inst.get("/portal").text


def test_reports_exports_and_calendar(off):
    r = off.get("/reports")
    assert r.status_code == 200 and 'id="kpis"' in r.text and 'id="calendar"' in r.text
    x = off.get("/reports/register.xlsx")
    assert x.status_code == 200 and x.content[:2] == b"PK"
    c = off.get("/reports/register.csv")
    assert c.status_code == 200 and "FD-0000123456" in c.text
    assert S("institution:INST-42").get("/reports").status_code in (303, 403)
    with db() as con:
        assert con.execute("SELECT count(*) FROM events WHERE kind='register_exported'").fetchone()[0] == 2


def test_reconcile_bank_files_flags_but_never_decides(off):
    samples = os.path.join(ROOT, "app", "static", "samples")
    before = off.get("/fdrs/FDR-002").text.count("Discrepancy")
    for name, expect in (("bank_fd_register.csv", "FD-0000777777"), ("statement.camt053.xml", "FD-0000555555"), ("statement.mt940", "FD-0000777777")):
        with open(os.path.join(samples, name), "rb") as fh:
            r = off.c.post("/reconcile", data={"csrf": off.csrf}, files={"file": (name, fh.read())})
        assert r.status_code == 200 and 'id="recon"' in r.text and expect in r.text, name
    assert off.get("/fdrs/FDR-002").text.count("Discrepancy") == before
    bad = off.c.post("/reconcile", data={"csrf": off.csrf}, files={"file": ("x.xml", b"<!DOCTYPE a [<!ENTITY e 'x'>]><a/>")})
    assert bad.status_code == 400
    nocsrf = off.c.post("/reconcile", data={"csrf": "x"}, files={"file": ("a.csv", b"fd_number\nFD-1\n")})
    assert nocsrf.status_code == 403
    with db() as con:
        assert con.execute("SELECT count(*) FROM events WHERE kind='bank_file_reconciled'").fetchone()[0] == 3


def test_signature_withdrawn_when_case_changes_after_signing(off):
    off.post("/fdrs/new", institution_id="INST-77", fd_number="FD-0000990002", bank_host="netbanking.mockbank.test",
             principal="15000000", issue_date="2024-01-01", maturity="2034-01-01")
    fid = db().execute("SELECT id FROM fdrs WHERE fd_number='FD-0000990002'").fetchone()[0]
    FD[fid] = "FD-0000990002"
    assert off.post("/mockbank/open", fd_number="FD-0000990002").status_code == 303
    j, cid = prove(off, fid, inst="INST-77")
    eid = db().execute("SELECT id FROM evidence WHERE challenge_id=?", (cid,)).fetchone()[0]
    off.post(f"/fdrs/{fid}/decide", action="accept", evidence_id=str(eid))
    off.post(f"/fdrs/{fid}/decide", action="release", evidence_id=str(eid), note="RB/REL/2026/0300 term completed")
    _clear_all(off, fid)
    rb2, rb3 = S("officer:RB-02"), S("officer:RB-03")
    assert _approve(rb2, f"/fdrs/{fid}/release/approve").status_code == 303                # first signature
    # a document is added to the case after RB-02 signed
    inst = S("institution:INST-77")
    pdf = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
    assert inst.c.post(f"/fdrs/{fid}/documents", data={"csrf": inst.csrf, "kind": "fdr_receipt"},
                       files={"file": ("late.pdf", pdf, "application/pdf")}).status_code == 303
    assert "stale-signatures" in off.get(f"/fdrs/{fid}").text
    assert _approve(rb3, f"/fdrs/{fid}/release/approve").status_code == 303                # counts as the first valid signature now
    assert db().execute("SELECT released_at FROM fdrs WHERE id=?", (fid,)).fetchone()[0] is None
    assert _approve(rb2, f"/fdrs/{fid}/release/approve").status_code == 303                # RB-02 signs again, on the current case
    assert db().execute("SELECT state FROM release_requests WHERE fdr_id=? ORDER BY id DESC", (fid,)).fetchone()[0] == "approved"
    body = rb2.get(f"/fdrs/{fid}/release-letter.json").json()
    assert body["payload"]["signed_by"] == ["RB-03", "RB-02"] and len(body["payload"]["case_fingerprint"]) == 64


def _pw(user, pw, otp=""):
    c = httpx.Client(base_url=B, follow_redirects=False, timeout=60)
    return c, c.post("/signin/password", data={"username": user, "password": pw, "otp": otp, "next": "/"})


def test_named_accounts_password_signin_and_lockout(off):
    c, r = _pw("rb02.approver", "Scourge-demo-2026")
    assert r.status_code == 303 and r.headers["location"] == "/register"
    assert c.get("/register").status_code == 200
    raw = c.cookies["scourge_session"]
    assert json.loads(base64.urlsafe_b64decode(raw.rsplit(".", 1)[0]))["who"] == "RB-02"
    c2, r2 = _pw("inst42.registrar", "Scourge-demo-2026")
    assert r2.headers["location"] == "/portal" and c2.get("/register").status_code == 403
    assert _pw("nobody.here", "Scourge-demo-2026")[1].status_code == 403
    for _ in range(5):
        assert _pw("rb01.scrutiny", "wrong-password-xyz")[1].status_code == 403
    assert _pw("rb01.scrutiny", "Scourge-demo-2026")[1].status_code == 429                # locked even with the right password
    with db() as con:
        h = con.execute("SELECT pw_hash, salt FROM accounts WHERE username='rb02.approver'").fetchone()
        assert len(h[0]) == 64 and "Scourge" not in h[0]                                    # scrypt hash, not the password
        assert con.execute("SELECT count(*) FROM events WHERE detail_json LIKE '%Scourge-demo%'").fetchone()[0] == 0


def test_email_alerts_delivered_over_smtp_when_configured(off):
    # the running app has no SMTP configured: e-mail is queued, never claimed as sent
    r = off.get("/notifications")
    assert "once SMTP is configured" in r.text
    assert db().execute("SELECT count(*) FROM notifications WHERE channel='email' AND status='sent'").fetchone()[0] == 0



# ---------------------------------------------------------------- V-series through the REAL prover/verifier (review contract)
@pytest.mark.parametrize("lien,decision", [
    ("AICTE NEW DELHI (SECURITY DEPOSIT)", "verified"),                 # V01 exact listed positive
    ("NO LIEN IN FAVOUR OF AICTE", "discrepancy"),                      # V02 listed negative
    ("AICTE LIEN REMOVED", "discrepancy"),                              # V03 listed negative
    ("AICTE LIEN CANCELLED ON 12-10-2026", "insufficient_evidence"),    # V06 unseen negation: never verified
    ("AICTE", "insufficient_evidence"),                                 # V06 bare mention is not a lien
    ("PLEDGED (SEE BRANCH)", "insufficient_evidence"),                  # V06 unknown wording
    ("LOAN 50000.00 AGAINST DEPOSIT", "discrepancy"),                   # V05 listed encumbrance
])
def test_lien_semantics_through_real_proofs(off, lien, decision):
    j, cid = prove(off, "FDR-001", "custom_lien", lien_text=lien)
    d, reason, facts, match = evidence(cid)
    assert facts["fields"]["lien"]["state"] == "proven" and facts["fields"]["lien"]["value"] == lien   # authentic bytes...
    assert d == decision, (lien, d, match.get("lien"))                                                 # ...read conservatively


def test_custom_lien_input_is_validated(off):
    assert off.post("/mockbank/behaviour", fd_number=FD["FDR-001"], behaviour="custom_lien", lien_text='X" , "status":"ACTIVE').status_code == 422
    assert off.post("/mockbank/behaviour", fd_number=FD["FDR-001"], behaviour="custom_lien", lien_text="").status_code == 422


def test_branch_lien_answer_is_structured(off):
    j, cid = prove(off, "FDR-001", "login")
    off.post("/fdrs/FDR-001/branch/request")
    link, code = _branch_link_and_code()
    path = link.split("8080", 1)[1]
    base = {"t": path.split("t=")[1], "code": code, "status": "ACTIVE", "balance": "1000000.00", "maturity": "2031-07-01",
            "holders": "SYNTHETIC INSTITUTE OF TECHNOLOGY TRUST / MEMBER SECRETARY AICTE", "officer_name": "Branch Manager, Main"}
    r = httpx.post(B + path.split("?")[0], data=dict(base, lien_choice="none"))
    assert r.status_code == 200
    e = db().execute("SELECT decision, facts_json FROM evidence WHERE fdr_id='FDR-001' ORDER BY id DESC").fetchone()
    assert e[0] == "discrepancy" and json.loads(e[1])["fields"]["lien"]["value"] == "NO LIEN"



# ---------------------------------------------------------------- P-series: departmental authority (review contract)
def _proposed(off, fdr="FDR-001"):
    eid = _confirmed(off, fdr)
    assert off.post(f"/fdrs/{fdr}/decide", action="release", evidence_id=str(eid), note="RB/REL/2026/0400 term completed").status_code == 303
    return eid


def test_p01_reviewer_cannot_decide_another_department(off):
    _proposed(off)
    fin = S("officer:FN-01")
    n0 = db().execute("SELECT count(*) FROM events WHERE kind='release_cleared'").fetchone()[0]
    assert _clear(fin, "FDR-001", "VIG").status_code == 403
    assert db().execute("SELECT count(*) FROM release_clearances").fetchone()[0] == 0
    assert db().execute("SELECT count(*) FROM events WHERE kind='release_cleared'").fetchone()[0] == n0
    assert db().execute("SELECT count(*) FROM events WHERE kind='release_clearance_refused'").fetchone()[0] == 1


def test_p02_proposer_and_approvers_cannot_clear(off):
    _proposed(off)
    for who in ("RB-01", "RB-02", "RB-03"):
        for d in DEPT_REVIEWER:
            assert _clear(S(f"officer:{who}"), "FDR-001", d).status_code == 403
    assert _approve(S("officer:PG-01"), "/fdrs/FDR-001/release/approve").status_code == 403     # reviewers cannot sign


def test_p03_clearance_needs_reason_and_memo(off):
    _proposed(off)
    pg = S("officer:PG-01")
    assert _clear(pg, "FDR-001", "PGRC", memo=None).status_code == 422
    assert _clear(pg, "FDR-001", "PGRC", note="ok").status_code == 422
    assert _clear(pg, "FDR-001", "PGRC", memo=b"MZ not a pdf").status_code == 422
    assert _clear(pg, "FDR-001", "PGRC").status_code == 303
    row = db().execute("SELECT kind, uploaded_by FROM documents WHERE fdr_id='FDR-001'").fetchone()
    assert row == ("clearance_memo", "officer:PG-01")


def test_p04_memo_of_another_case_or_officer_refused(off):
    _proposed(off)
    _proposed(off, "FDR-004")
    pg, vg = S("officer:PG-01"), S("officer:VG-01")
    assert _clear(pg, "FDR-004", "PGRC").status_code == 303
    other = db().execute("SELECT id FROM documents WHERE fdr_id='FDR-004'").fetchone()[0]
    assert _clear(pg, "FDR-001", "PGRC", memo=None, doc_id=str(other)).status_code == 422          # another case's memo
    up = vg.c.post("/fdrs/FDR-001/documents", data={"csrf": vg.csrf, "kind": "clearance_memo"}, files={"file": ("v.pdf", MEMO, "application/pdf")})
    assert up.status_code == 303
    vmemo = db().execute("SELECT id FROM documents WHERE fdr_id='FDR-001' AND uploaded_by='officer:VG-01'").fetchone()[0]
    assert _clear(pg, "FDR-001", "PGRC", memo=None, doc_id=str(vmemo)).status_code == 422          # someone else's memo
    assert _clear(vg, "FDR-001", "VIG", memo=None, doc_id=str(vmemo)).status_code == 303           # own memo of this case


def test_p05_same_officer_cannot_give_both_signatures(off):
    off.post("/fdrs/new", institution_id="INST-77", fd_number="FD-0000990005", bank_host="netbanking.mockbank.test",
             principal="20000000", issue_date="2024-01-01", maturity="2034-01-01")
    fid = db().execute("SELECT id FROM fdrs WHERE fd_number='FD-0000990005'").fetchone()[0]
    FD[fid] = "FD-0000990005"
    off.post("/mockbank/open", fd_number="FD-0000990005")
    j, cid = prove(off, fid, inst="INST-77")
    eid = db().execute("SELECT id FROM evidence WHERE challenge_id=?", (cid,)).fetchone()[0]
    off.post(f"/fdrs/{fid}/decide", action="accept", evidence_id=str(eid))
    off.post(f"/fdrs/{fid}/decide", action="release", evidence_id=str(eid), note="RB/REL/2026/0500 term completed")
    _clear_all(fdr=fid)
    rb2 = S("officer:RB-02")
    assert _approve(rb2, f"/fdrs/{fid}/release/approve").status_code == 303
    assert _approve(rb2, f"/fdrs/{fid}/release/approve").status_code == 409
    assert db().execute("SELECT count(*) FROM release_approvals").fetchone()[0] == 1


# ---------------------------------------------------------------- G-series: atomic, version-aware approval (review contract)
def _two_signer_case(off, fd, principal="15000000"):
    off.post("/fdrs/new", institution_id="INST-77", fd_number=fd, bank_host="netbanking.mockbank.test",
             principal=principal, issue_date="2024-01-01", maturity="2034-01-01")
    fid = db().execute("SELECT id FROM fdrs WHERE fd_number=?", (fd,)).fetchone()[0]
    FD[fid] = fd
    off.post("/mockbank/open", fd_number=fd)
    j, cid = prove(off, fid, inst="INST-77")
    eid = db().execute("SELECT id FROM evidence WHERE challenge_id=?", (cid,)).fetchone()[0]
    off.post(f"/fdrs/{fid}/decide", action="accept", evidence_id=str(eid))
    off.post(f"/fdrs/{fid}/decide", action="release", evidence_id=str(eid), note="RB/REL/2026/0600 term completed")
    _clear_all(fdr=fid)
    return fid


def _version(sess, fid):
    import re as _re
    m = _re.search(r'name="version" value="([^"]+)"', sess.get(f"/fdrs/{fid}").text)
    return m.group(1)


def _arm(point):
    key = open(os.path.join(VARDIR, "internal.key")).read().strip()
    assert httpx.post(B + "/internal/fault", data={"point": point}, headers={"x-internal-key": key}).status_code == 200


def _counts(fid):
    c = db()
    return (c.execute("SELECT count(*) FROM release_approvals").fetchone()[0],
            c.execute("SELECT state FROM release_requests WHERE fdr_id=? ORDER BY id DESC", (fid,)).fetchone()[0],
            c.execute("SELECT count(*) FROM events WHERE fdr_id=? AND kind IN ('release_signed','release_instruction_issued')", (fid,)).fetchone()[0],
            c.execute("SELECT count(*) FROM actions WHERE fdr_id=? AND action='release'", (fid,)).fetchone()[0])


def test_g03_hold_placed_before_approval_commits_blocks_it(off):
    fid = _two_signer_case(off, "FD-0000990101")
    rb2 = S("officer:RB-02")
    v = _version(rb2, fid)
    assert off.post(f"/fdrs/{fid}/hold", reason="Court order pending in WP 123/2026").status_code == 303
    assert _approve(rb2, f"/fdrs/{fid}/release/approve", version=v).status_code == 409
    assert _counts(fid)[:3] == (0, "pending", 0)


def test_g05_g06_stale_version_and_duplicate_requests(off):
    fid = _two_signer_case(off, "FD-0000990102")
    rb2, rb3 = S("officer:RB-02"), S("officer:RB-03")
    v = _version(rb2, fid)
    assert _approve(rb2, f"/fdrs/{fid}/release/approve", version=v).status_code == 303          # signature 1 of 2
    assert _approve(rb3, f"/fdrs/{fid}/release/approve", version=v).status_code == 409          # saw the case before that signature
    v2 = _version(rb3, fid)
    assert _approve(rb3, f"/fdrs/{fid}/release/approve", version=v2).status_code == 303
    assert _approve(rb3, f"/fdrs/{fid}/release/approve", version=v2).status_code == 409          # duplicate: already decided
    a, state, ev, act = _counts(fid)
    assert (a, state, ev, act) == (2, "approved", 2, 1)                                     # one transition, one letter


@pytest.mark.parametrize("point", ["after_signature", "after_state", "before_audit", "after_audit"])
def test_g07_g08_failure_mid_approval_rolls_everything_back(off, point):
    fid = _two_signer_case(off, f"FD-00009902{['after_signature','after_state','before_audit','after_audit'].index(point)}0")
    rb2, rb3 = S("officer:RB-02"), S("officer:RB-03")
    assert _approve(rb2, f"/fdrs/{fid}/release/approve").status_code == 303
    before = _counts(fid)
    head = off.get("/api/audit/verify").json()
    _arm(point)
    assert _approve(rb3, f"/fdrs/{fid}/release/approve").status_code == 500
    assert _counts(fid) == before                                                           # no signature, state, action or audit
    assert off.get("/api/audit/verify").json() == head
    assert _approve(rb3, f"/fdrs/{fid}/release/approve").status_code == 303                     # and it works once the fault is gone
    assert _counts(fid)[1] == "approved"


# ---------------------------------------------------------------- C-series: issued instruction vs completed release
def _issued(off, fd="FD-0000990300"):
    fid = _two_signer_case(off, fd, principal="12000000")
    _approve(S("officer:RB-02"), f"/fdrs/{fid}/release/approve")
    _approve(S("officer:RB-03"), f"/fdrs/{fid}/release/approve")
    letter = db().execute("SELECT letter_no FROM release_requests WHERE fdr_id=? AND state='approved'", (fid,)).fetchone()[0]
    return fid, letter


def _ack(letter, fd, amount="12000000.00", n=1):
    return f"%PDF-1.4\n% Bank acknowledgement {n}: pledge released per {letter} for {fd}, amount {amount}\n%%EOF\n".encode()


def _complete(sess, fid, letter, fd, amount="12000000.00", ack="auto", ref="SBN/ACK/2026/0091", **extra):
    if ack == "auto":
        ack = _ack(letter, fd, amount)
    files = {"ack": ("ack.pdf", ack, "application/pdf")} if ack else None
    return sess.c.post(f"/fdrs/{fid}/release/complete", data={"csrf": sess.csrf, "letter_no": letter, "fd_number": fd, "amount": amount, "bank_ref": ref, **extra}, files=files)


def test_c01_issued_instruction_is_not_completion(off):
    fid, letter = _issued(off)
    assert db().execute("SELECT released_at FROM fdrs WHERE id=?", (fid,)).fetchone()[0] is None
    page = off.get(f"/fdrs/{fid}").text
    assert "awaiting the bank" in page and "Release instruction issued" in page
    assert off.get(f"/fdrs/{fid}/release-letter").status_code == 200                       # the instruction exists


def test_c02_c03_completion_evidence_must_match_and_be_new(off):
    fid, letter = _issued(off, "FD-0000990301")
    rec = S("officer:RB-04")
    fd = FD[fid]
    assert _complete(S("officer:RB-02"), fid, letter, fd).status_code == 403                # a signer cannot complete
    assert _complete(off, fid, letter, fd).status_code == 403                               # nor the proposer
    assert _complete(rec, fid, letter, fd, ack=None).status_code == 422
    assert _complete(rec, fid, "AICTE/RB/REL/2026/9999", fd).status_code == 422             # another letter
    assert _complete(rec, fid, letter, "FD-0000123456").status_code == 422                  # another FD
    assert _complete(rec, fid, letter, fd, amount="1.00").status_code == 422                 # another amount
    assert _complete(rec, fid, letter, fd, ack=MEMO).status_code == 422                      # MEMO bytes already on record
    assert db().execute("SELECT released_at FROM fdrs WHERE id=?", (fid,)).fetchone()[0] is None


def test_c04_c06_valid_completion_once(off):
    fid, letter = _issued(off, "FD-0000990302")
    rec = S("officer:RB-04")
    assert _complete(rec, fid, letter, FD[fid]).status_code == 303
    assert db().execute("SELECT released_at FROM fdrs WHERE id=?", (fid,)).fetchone()[0] is not None
    assert _complete(rec, fid, letter, FD[fid], ack=_ack(letter, FD[fid], n=2)).status_code == 409   # retried: no duplicate
    c = db()
    assert c.execute("SELECT count(*) FROM events WHERE fdr_id=? AND kind='release_completed'", (fid,)).fetchone()[0] == 1
    assert c.execute("SELECT kind, uploaded_by FROM documents WHERE fdr_id=? AND kind='bank_acknowledgement'", (fid,)).fetchall() == [("bank_acknowledgement", "officer:RB-04")]
    assert "Released" in rec.get(f"/fdrs/{fid}").text


def test_c05_changed_basis_needs_review_not_completion(off):
    fid, letter = _issued(off, "FD-0000990303")
    inst = S("institution:INST-77")
    assert inst.c.post(f"/fdrs/{fid}/documents", data={"csrf": inst.csrf, "kind": "other"},
                       files={"file": ("late.pdf", b"%PDF-1.4\n% late\n%%EOF\n", "application/pdf")}).status_code == 303
    assert _complete(S("officer:RB-04"), fid, letter, FD[fid]).status_code == 409
    assert db().execute("SELECT released_at FROM fdrs WHERE id=?", (fid,)).fetchone()[0] is None



# ---------------------------------------------------------------- second review round: P0 + P1 safeguards
def test_adverse_bank_evidence_after_issue_blocks_completion(off):
    fid, letter = _issued(off, "FD-0000990401")
    j, cid = prove(off, fid, "lien_loan", inst="INST-77")                                 # bank now shows a loan
    assert j["decision"] == "discrepancy"
    assert _complete(S("officer:RB-04"), fid, letter, FD[fid]).status_code == 409
    assert db().execute("SELECT released_at FROM fdrs WHERE id=?", (fid,)).fetchone()[0] is None
    assert "Discrepancy after instruction" in off.get(f"/fdrs/{fid}").text


def test_approval_version_is_mandatory(off):
    fid = _two_signer_case(off, "FD-0000990402")
    rb2 = S("officer:RB-02")
    assert rb2.post(f"/fdrs/{fid}/release/approve").status_code == 409                  # missing
    assert rb2.post(f"/fdrs/{fid}/release/approve", version="").status_code == 409      # empty
    assert rb2.post(f"/fdrs/{fid}/release/approve", version="0" * 64 + ":0").status_code == 409   # forged / stale
    assert db().execute("SELECT count(*) FROM release_approvals").fetchone()[0] == 0


def test_acknowledgement_must_name_case_or_be_attested(off):
    fid, letter = _issued(off, "FD-0000990403")
    rec = S("officer:RB-04")
    scan = b"%PDF-1.4\n% scanned image, no text\n%%EOF\n"
    assert _complete(rec, fid, letter, FD[fid], ack=scan).status_code == 422             # names nothing, not attested
    wrong = _ack("AICTE/RB/REL/2026/9999", FD[fid])
    assert _complete(rec, fid, letter, FD[fid], ack=wrong).status_code == 422            # names another letter
    assert _complete(rec, fid, letter, FD[fid], ack=scan, attest="yes").status_code == 303
    ev = db().execute("SELECT detail_json FROM events WHERE kind='release_completed' AND fdr_id=?", (fid,)).fetchone()[0]
    assert json.loads(ev)["evidence_basis"] == "recorder_attested_manual_review"


def test_one_outstanding_release_and_revoke_to_supersede(off):
    fid, letter = _issued(off, "FD-0000990404")
    eid = db().execute("SELECT evidence_id FROM release_requests WHERE fdr_id=?", (fid,)).fetchone()[0]
    assert off.post(f"/fdrs/{fid}/decide", action="release", evidence_id=str(eid), note="RB/REL/2026/0700 duplicate proposal").status_code == 409
    rb2 = S("officer:RB-02")
    assert off.post(f"/fdrs/{fid}/release/revoke", reason="Superseded by corrected instruction").status_code == 403   # not an approver
    assert rb2.post(f"/fdrs/{fid}/release/revoke", reason="short").status_code == 422
    assert rb2.post(f"/fdrs/{fid}/release/revoke", reason="Superseded by corrected instruction").status_code == 303
    assert db().execute("SELECT state FROM release_requests WHERE letter_no=?", (letter,)).fetchone()[0] == "revoked"
    assert _complete(S("officer:RB-04"), fid, letter, FD[fid]).status_code == 409         # revoked: nothing to complete
    assert off.post(f"/fdrs/{fid}/decide", action="release", evidence_id=str(eid), note="RB/REL/2026/0701 replacement").status_code == 303


def test_revoked_letter_fails_public_verification(off):
    fid, letter = _issued(off, "FD-0000990405")
    body = S("officer:RB-02").get(f"/fdrs/{fid}/release-letter.json").json()
    import base64 as b64
    d = b64.urlsafe_b64encode(body["signed_bytes"].encode()).decode().rstrip("=")
    sig = b64.urlsafe_b64encode(b64.b64decode(body["signature_b64"])).decode().rstrip("=")
    assert "Valid AICTE signature" in httpx.get(B + f"/verify?d={d}&sig={sig}").text
    S("officer:RB-02").post(f"/fdrs/{fid}/release/revoke", reason="Superseded by corrected instruction")
    assert "Revoked by AICTE" in httpx.get(B + f"/verify?d={d}&sig={sig}").text


@pytest.mark.parametrize("who", ["PG-01", "VG-01", "FN-01", "RB-02", "RB-03", "RB-04"])
def test_only_scrutiny_officer_accepts_and_proposes(off, who):
    j, cid = prove(off, "FDR-001")
    eid = db().execute("SELECT id FROM evidence WHERE challenge_id=?", (cid,)).fetchone()[0]
    o = S(f"officer:{who}")
    assert o.post("/fdrs/FDR-001/decide", action="accept", evidence_id=str(eid)).status_code == 403
    assert off.post("/fdrs/FDR-001/decide", action="accept", evidence_id=str(eid)).status_code == 303
    assert o.post("/fdrs/FDR-001/decide", action="release", evidence_id=str(eid), note="RB/REL/2026/0800 not my role").status_code == 403
    if who not in ("RB-02", "RB-03"):
        assert _approve(o, "/fdrs/FDR-001/release/approve").status_code == 403


# ---------------------------------------------------------------- L series: release types, settlement, regional scoping
def _propose_type(off, rtype, fdr="FDR-001"):
    eid = _confirmed(off, fdr)
    return off.post(f"/fdrs/{fdr}/decide", action="release", evidence_id=str(eid), note="RB/REL/2026/0700 test release", release_type=rtype)


def _ded(sess, fdr="FDR-001", kind="student_refund", payee="214 students, 2025 batch", amount="125000", basis="PGRC order 14/2026, list attached"):
    return sess.post(f"/fdrs/{fdr}/release/deduction", kind=kind, payee=payee, amount=amount, basis=basis)


def test_l01_release_type_is_required_to_be_known(off):
    assert _propose_type(off, "liquidation").status_code == 422
    assert db().execute("SELECT count(*) FROM release_requests").fetchone()[0] == 0


def test_l02_closure_needs_its_documents_and_only_its_departments(off):
    assert _propose_type(off, "closure").status_code == 303
    _clear_all()
    page = off.get("/fdrs/FDR-001").text
    assert "Closure order" in page and "missing" in page
    r = _approve(S("officer:RB-02"), "/fdrs/FDR-001/release/approve")
    assert r.status_code == 409 and "Closure order" in r.text
    for kind in ("closure_order", "dues_statement"):
        assert off.c.post("/fdrs/FDR-001/documents", data={"csrf": off.csrf, "kind": kind},
                          files={"file": (f"{kind}.pdf", MEMO, "application/pdf")}).status_code == 303
    assert _approve(S("officer:RB-02"), "/fdrs/FDR-001/release/approve").status_code == 303
    rt = db().execute("SELECT state, release_type FROM release_requests").fetchone()
    assert rt == ("approved", "closure")


def test_l03_merger_does_not_wait_for_grievance_cell(off):
    assert _propose_type(off, "merger").status_code == 303
    for d in ("VIG", "FIN"):
        assert _clear(S(f"officer:{DEPT_REVIEWER[d]}"), "FDR-001", d).status_code == 303
    assert off.c.post("/fdrs/FDR-001/documents", data={"csrf": off.csrf, "kind": "merger_order"},
                      files={"file": ("m.pdf", MEMO, "application/pdf")}).status_code == 303
    assert _approve(S("officer:RB-02"), "/fdrs/FDR-001/release/approve").status_code == 303


def test_l04_settlement_in_paise_is_signed_into_the_instruction(off):
    _propose_type(off, "term_completed")
    principal = db().execute("SELECT principal FROM fdrs WHERE id='FDR-001'").fetchone()[0]
    fin = S("officer:FN-01")
    assert _ded(off, amount="125000.50").status_code == 303
    assert _ded(fin, kind="staff_dues", payee="Faculty salary arrears (12 staff)", amount="40000", basis="Finance note FN/2026/33").status_code == 303
    _clear_all()
    assert _approve(S("officer:RB-02"), "/fdrs/FDR-001/release/approve").status_code == 303
    sj = json.loads(db().execute("SELECT signed_json FROM release_requests WHERE state='approved'").fetchone()[0])
    st = sj["settlement"]
    p = round(float(principal) * 100)
    assert st["principal_paise"] == p and st["deductions_paise"] == 12500050 + 4000000
    assert st["refund_to_institution_paise"] == p - 16500050 and len(st["lines"]) == 2
    assert sj["release_type"] == "term_completed"
    letter = off.get("/fdrs/FDR-001/release-letter").text
    assert "Refund to the institution" in letter and "Faculty salary arrears" in letter


def test_l05_claims_validated_and_role_restricted(off):
    _propose_type(off, "term_completed")
    principal = db().execute("SELECT principal FROM fdrs WHERE id='FDR-001'").fetchone()[0]
    assert _ded(S("officer:RB-02")).status_code == 403            # approvers do not write claims
    assert _ded(S("officer:VG-01")).status_code == 403
    assert _ded(off, amount="-5").status_code == 422
    assert _ded(off, amount="12.345").status_code == 422
    assert _ded(off, amount="0").status_code == 422
    assert _ded(off, kind="bonus").status_code == 422
    assert _ded(off, basis="short").status_code == 422
    over = f"{float(principal) + 1:.2f}"
    assert _ded(off, amount=over).status_code == 409             # cannot exceed the deposit
    assert db().execute("SELECT count(*) FROM release_deductions").fetchone()[0] == 0
    inst = S("institution:INST-42")
    assert _ded(inst).status_code in (401, 403)


def test_l06_changing_claims_withdraws_signatures(off):
    fid = _two_signer_case(off, "FD-0000990700")
    _approve(S("officer:RB-02"), f"/fdrs/{fid}/release/approve")
    assert db().execute("SELECT count(*) FROM release_approvals").fetchone()[0] == 1
    assert _ded(off, fdr=fid).status_code == 303
    page = off.get(f"/fdrs/{fid}").text
    assert 'id="stale-signatures"' in page
    # the second officer alone cannot now complete it: one valid signature of two
    assert _approve(S("officer:RB-03"), f"/fdrs/{fid}/release/approve").status_code == 303
    assert db().execute("SELECT state FROM release_requests WHERE fdr_id=?", (fid,)).fetchone()[0] == "pending"
    did = db().execute("SELECT id FROM release_deductions").fetchone()[0]
    assert off.post(f"/fdrs/{fid}/release/deduction/{did}/remove", reason="x").status_code == 422
    assert off.post(f"/fdrs/{fid}/release/deduction/{did}/remove", reason="Entered against the wrong case").status_code == 303
    kinds = [r[0] for r in db().execute("SELECT kind FROM events WHERE kind LIKE 'release_deduction_%'")]
    assert kinds == ["release_deduction_added", "release_deduction_removed"]


def test_l07_regional_officer_sees_only_its_region(off):
    ro = S("officer:RO-S1")                                       # Southern RO: INST-42 (Hyderabad) only
    reg = ro.get("/register").text
    assert "FD-0000123456" in reg                                 # INST-42 deposit
    inst77 = [k for k, v in FD.items() if db().execute("SELECT institution_id FROM fdrs WHERE id=?", (k,)).fetchone()
              and db().execute("SELECT institution_id FROM fdrs WHERE id=?", (k,)).fetchone()[0] == "INST-77"]
    assert inst77
    for fid in inst77:
        assert FD[fid] not in reg
        assert ro.get(f"/fdrs/{fid}").status_code == 404
    assert S("officer").get(f"/fdrs/{inst77[0]}").status_code == 200  # headquarters sees every region
    assert ro.post("/fdrs/FDR-001/decide", action="accept", evidence_id="1").status_code == 403  # view-only role
