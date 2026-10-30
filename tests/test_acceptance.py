"""Acceptance tests over HTTP and the real binaries. Requires the app running on 127.0.0.1:8080
(scripts/run.sh). Each test resets the demo first. Run: python3 -m pytest -q tests/test_acceptance.py
These exercise the real prover, the separate verifier and the synthetic bank. They do NOT show the real-bank gate."""
import base64
import json
import os
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


def bank(off, fdr, behaviour):
    assert off.post("/mockbank/behaviour", fd_number=FD[fdr], behaviour=behaviour).status_code == 303


def prove(off, fdr, behaviour="active", inst="INST-42", **mode):
    bank(off, fdr, behaviour)
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


def test_release_needs_two_different_officers(off):
    eid = _confirmed(off)
    assert off.post("/fdrs/FDR-001/release/approve").status_code == 409                # nothing proposed yet
    assert off.post("/fdrs/FDR-001/decide", action="release", evidence_id=str(eid), note="RB/REL/2026/0142 term completed").status_code == 303
    assert "Release proposed" in off.get("/fdrs/FDR-001").text
    assert off.post("/fdrs/FDR-001/decide", action="release", evidence_id=str(eid), note="second proposal attempt").status_code == 409
    assert off.post("/fdrs/FDR-001/release/approve").status_code == 409                # maker cannot be checker
    assert db().execute("SELECT released_at FROM fdrs WHERE id='FDR-001'").fetchone()[0] is None
    inst = S("institution:INST-42")
    assert inst.post("/fdrs/FDR-001/release/approve").status_code == 403               # institutions cannot approve
    rb2 = S("officer:RB-02")
    assert rb2.post("/fdrs/FDR-001/release/approve").status_code == 303
    assert db().execute("SELECT released_at FROM fdrs WHERE id='FDR-001'").fetchone()[0] is not None
    letter = rb2.get("/fdrs/FDR-001/release-letter").text
    assert "AICTE/RB/REL/" in letter and "RB-01" in letter and "RB-02" in letter
    assert inst.get("/fdrs/FDR-001/release-letter").status_code == 200                 # the institution gets its letter
    assert off.get("/api/audit/verify").json()["ok"]


def test_release_approval_blocked_when_confirmation_goes_stale(off):
    eid = _confirmed(off)
    off.post("/fdrs/FDR-001/decide", action="release", evidence_id=str(eid), note="RB/REL/2026/0143 term completed")
    c = db(); c.execute("UPDATE evidence SET received_at=received_at-31*86400 WHERE id=?", (eid,)); c.commit()
    rb2 = S("officer:RB-02")
    assert rb2.post("/fdrs/FDR-001/release/approve").status_code == 409
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
    off.post("/fdrs/FDR-001/hold", reason="New court case filed against the trust")
    assert S("officer:RB-02").post("/fdrs/FDR-001/release/approve").status_code == 409   # hold also blocks approval


def test_release_letter_is_signed_and_verifiable_offline(off):
    import base64 as b64
    from cryptography.hazmat.primitives import serialization
    eid = _confirmed(off)
    off.post("/fdrs/FDR-001/decide", action="release", evidence_id=str(eid), note="RB/REL/2026/0160 term completed")
    rb2 = S("officer:RB-02")
    assert rb2.post("/fdrs/FDR-001/release/approve").status_code == 303
    body = rb2.get("/fdrs/FDR-001/release-letter.json").json()
    pub = serialization.load_pem_public_key(httpx.get(B + "/api/aicte-release-key").text.encode())
    pub.verify(b64.b64decode(body["signature_b64"]), body["signed_bytes"].encode())          # raises if invalid
    assert body["payload"]["approved_by"] == "RB-02" and body["payload"]["proposed_by"] == "RB-01"
    import pytest as _p
    from cryptography.exceptions import InvalidSignature
    with _p.raises(InvalidSignature):
        pub.verify(b64.b64decode(body["signature_b64"]), body["signed_bytes"].replace("RB-02", "RB-01").encode())
