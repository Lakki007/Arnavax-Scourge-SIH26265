"""Production-mode journey (SCOURGE_AUTH=password): every person signs in with a named account; officers need
TOTP; the demo role chooser is refused. Runs the complete deposit lifecycle through the real prover and verifier:
challenge -> institution proof -> accept -> propose -> three department clearances -> approval with a fresh 2FA
code -> bank acknowledgement -> released -> public verification of the signed letter.

Needs a server started in production mode on a fresh data directory, e.g.
  SCOURGE_AUTH=password SCOURGE_VAR=var_prod ./scripts/run.sh &
  SCOURGE_AUTH=password SCOURGE_VAR=var_prod python -m pytest -q tests/test_production_mode.py
Skipped when the running server is in demo mode."""
import base64
import json
import os
import re
import sqlite3
import sys
import time

import httpx
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
B = os.environ.get("SCOURGE_URL", "http://127.0.0.1:8080")
PW = "Prod-journey-2026!"
sys.path.insert(0, ROOT)


def _mode():
    try:
        r = httpx.post(B + "/signin", data={"role": "officer", "next": "/"}, timeout=3)
    except httpx.HTTPError:
        return None
    return "password" if r.status_code == 403 and "switched off" in r.text else "demo"


MODE = _mode()
if os.environ.get("SCOURGE_REQUIRE_PROD") == "1" and MODE != "password":
    raise RuntimeError(f"SCOURGE_REQUIRE_PROD=1 but the server at {B} is not in password mode (got {MODE})")
pytestmark = pytest.mark.skipif(MODE != "password", reason="needs a server started with SCOURGE_AUTH=password")

OFFICERS = {"rb01.prod": "RB-01", "rb02.prod": "RB-02", "rb03.prod": "RB-03", "pg01.prod": "PG-01", "vg01.prod": "VG-01",
            "fn01.prod": "FN-01", "rb04.prod": "RB-04"}
SECRETS = {}


@pytest.fixture(scope="module", autouse=True)
def accounts_enrolled():
    os.environ.setdefault("SCOURGE_AUTH", "password")
    from app import accounts, db, totp
    for user, oid in OFFICERS.items():
        if not db.q1("SELECT 1 AS x FROM accounts WHERE username=?", (user,)):
            accounts.create(user, PW, "officer", None, oid)
        row = db.q1("SELECT secret FROM officer_mfa WHERE officer=?", (oid,))
        if not row:
            sec = totp.new_secret()
            db.x("INSERT INTO officer_mfa(officer,secret,enabled,created_at,last_step) VALUES(?,?,1,?,0)", (oid, sec, time.time()))
        SECRETS[oid] = db.q1("SELECT secret FROM officer_mfa WHERE officer=?", (oid,))["secret"]
    if not db.q1("SELECT 1 AS x FROM accounts WHERE username='inst42.prod'"):
        accounts.create("inst42.prod", PW, "institution", "INST-42", None)
    if not db.q1("SELECT 1 AS x FROM accounts WHERE username='inst77.prod'"):
        accounts.create("inst77.prod", PW, "institution", "INST-77", None)
    if not db.q1("SELECT 1 AS x FROM accounts WHERE username='ros1.nomfa'"):
        accounts.create("ros1.nomfa", PW, "officer", None, "RO-S1")       # deliberately not enrolled in 2FA
    yield


def code(oid, ahead=0):
    from app import totp
    return totp.code_at(SECRETS[oid], totp.now_step() + ahead)


def fresh_code(oid):
    """A code the server will accept: a step after the last one used (replay protection) and inside the +-1 window."""
    from app import totp
    last = dbq("SELECT last_step FROM officer_mfa WHERE officer=?", oid)[0]
    step = max(totp.now_step() - 1, last + 1)
    while step > totp.now_step() + 1:
        time.sleep(1)
    return totp.code_at(SECRETS[oid], step)


class P:
    """A signed-in browser session for a named account."""
    def __init__(self, user, otp=""):
        self.c = httpx.Client(base_url=B, follow_redirects=False, timeout=120)
        self.r = self.c.post("/signin/password", data={"username": user, "password": PW, "otp": otp, "next": "/"})
        raw = self.c.cookies.get("scourge_session")
        self.csrf = json.loads(base64.urlsafe_b64decode(raw.rsplit(".", 1)[0]))["csrf"] if raw else None

    def post(self, url, files=None, **data):
        return self.c.post(url, data={"csrf": self.csrf, **data}, files=files)


def officer(user):
    oid = OFFICERS[user]
    s = P(user, fresh_code(oid))
    assert s.r.status_code == 303, s.r.text
    return s


def dbq(sql, *a):
    from app.settings import DB_PATH
    return sqlite3.connect(DB_PATH).execute(sql, a).fetchone()


def test_demo_role_chooser_is_refused():
    r = httpx.post(B + "/signin", data={"role": "officer:RB-02", "next": "/"})
    assert r.status_code == 403
    assert httpx.post(B + "/signin", data={"role": "institution:INST-42", "next": "/"}).status_code == 403


def test_officer_without_2fa_cannot_sign_in_and_wrong_code_is_refused():
    assert P("ros1.nomfa").r.status_code == 403
    assert P("rb02.prod", "000000").r.status_code == 403
    assert P("rb02.prod", "").r.status_code == 403
    assert P("inst42.prod", "").r.status_code == 303                        # institutions: password only


def test_full_lifecycle_under_production_sign_in():
    rb01 = officer("rb01.prod")
    inst = P("inst42.prod")
    # a fresh deposit for this run
    fd = f"FD-{int(time.time()) % 10**10:010d}"
    assert rb01.post("/fdrs/new", institution_id="INST-42", fd_number=fd, bank_host="netbanking.mockbank.test", principal="750000",
                     issue_date="2024-01-01", maturity="2034-01-01").status_code == 303
    fid = dbq("SELECT id FROM fdrs WHERE fd_number=?", fd)[0]
    assert rb01.post("/mockbank/open", fd_number=fd, bank_principal="750000").status_code == 303
    # 1. challenge and proof from the institution's own (synthetic) bank session
    assert rb01.post(f"/fdrs/{fid}/challenge").status_code == 303
    cid = dbq("SELECT id FROM challenges WHERE fdr_id=? ORDER BY issued_at DESC", fid)[0]
    r = inst.post(f"/challenges/{cid}/prove")
    assert r.status_code == 303, r.text
    jid = r.headers["location"].rsplit("/", 1)[1]
    for _ in range(240):
        j = inst.c.get(f"/api/jobs/{jid}").json()
        if j["state"] != "running":
            break
        time.sleep(0.25)
    assert j.get("decision") == "verified", j
    eid = dbq("SELECT id FROM evidence WHERE challenge_id=?", cid)[0]
    # 2. accept and propose (scrutiny officer)
    assert rb01.post(f"/fdrs/{fid}/decide", action="accept", evidence_id=str(eid)).status_code == 303
    assert rb01.post(f"/fdrs/{fid}/decide", action="release", evidence_id=str(eid), note="RB/REL/2026/0900 term completed",
                     release_type="term_completed").status_code == 303
    # 3. every department clears with its own named, 2FA-signed-in reviewer and a memo
    memo = b"%PDF-1.4\n% clearance memo\n%%EOF\n"
    for user, dept in (("pg01.prod", "PGRC"), ("vg01.prod", "VIG"), ("fn01.prod", "FIN")):
        s = officer(user)
        r = s.c.post(f"/fdrs/{fid}/release/clear", data={"csrf": s.csrf, "dept": dept, "decision": "clear", "note": "Register checked: nothing pending"},
                     files={"memo": ("m.pdf", memo + dept.encode(), "application/pdf")})
        assert r.status_code == 303, r.text
    # 4. approval needs a FRESH authenticator code (step-up), not the sign-in one
    rb02 = officer("rb02.prod")
    version = re.search(r'name="version" value="([^"]+)"', rb02.c.get(f"/fdrs/{fid}").text).group(1)
    assert rb02.post(f"/fdrs/{fid}/release/approve", version=version, otp="").status_code == 403
    assert rb02.post(f"/fdrs/{fid}/release/approve", version=version, otp=fresh_code("RB-02")).status_code == 303
    letter = dbq("SELECT letter_no FROM release_requests WHERE fdr_id=? AND state='approved'", fid)[0]
    assert dbq("SELECT released_at FROM fdrs WHERE id=?", fid)[0] is None            # issued is not released
    # 5. the bank's acknowledgement completes it
    rb04 = officer("rb04.prod")
    ack = f"%PDF-1.4\n% Bank acknowledgement: pledge released per {letter} for {fd}, amount 750000.00\n%%EOF\n".encode()
    r = rb04.c.post(f"/fdrs/{fid}/release/complete", data={"csrf": rb04.csrf, "letter_no": letter, "fd_number": fd, "amount": "750000.00",
                    "bank_ref": "BANK/ACK/2026/0001"}, files={"ack": ("ack.pdf", ack, "application/pdf")})
    assert r.status_code == 303, r.text
    assert dbq("SELECT released_at FROM fdrs WHERE id=?", fid)[0] is not None
    # 6. anyone can check the signed letter
    page = rb01.c.get(f"/fdrs/{fid}/release-letter").text
    url = re.search(r'href="(http[^"]*/verify\?d=[^"]+)"', page).group(1).replace("&amp;", "&")
    pub = httpx.get(B + url[url.index("/verify"):])
    assert pub.status_code == 200 and letter in pub.text and "Revoked" not in pub.text
    # every actor in the audit log is a named officer or the institution, never a demo persona
    actors = {r[0] for r in sqlite3.connect(__import__("app.settings", fromlist=["DB_PATH"]).DB_PATH).execute(
        "SELECT DISTINCT actor FROM events WHERE fdr_id=?", (fid,))}
    assert actors <= {"officer:RB-01", "officer:RB-02", "officer:PG-01", "officer:VG-01", "officer:FN-01", "officer:RB-04",
                      "institution:INST-42", "officer", "verifier", "system"}, actors



# ---------------------------------------------------------------- production negative cases (separation of duties, replay, forgery)
_N = [0]


def _fd():
    _N[0] += 1
    return f"FD-{(int(time.time()) * 10 + _N[0]) % 10**10:010d}"


def _confirmed_case(rb01, inst, principal="750000", inst_id="INST-42"):
    """A fresh deposit, proved from the (synthetic) bank, accepted and proposed for release. Returns (fid, fd)."""
    fd = _fd()
    assert rb01.post("/fdrs/new", institution_id=inst_id, fd_number=fd, bank_host="netbanking.mockbank.test", principal=principal,
                     issue_date="2024-01-01", maturity="2034-01-01").status_code == 303
    fid = dbq("SELECT id FROM fdrs WHERE fd_number=?", fd)[0]
    assert rb01.post("/mockbank/open", fd_number=fd, bank_principal=principal).status_code == 303
    assert rb01.post(f"/fdrs/{fid}/challenge").status_code == 303
    cid = dbq("SELECT id FROM challenges WHERE fdr_id=? ORDER BY issued_at DESC", fid)[0]
    r = inst.post(f"/challenges/{cid}/prove")
    assert r.status_code == 303, r.text
    jid = r.headers["location"].rsplit("/", 1)[1]
    for _ in range(240):
        j = inst.c.get(f"/api/jobs/{jid}").json()
        if j["state"] != "running":
            break
        time.sleep(0.25)
    assert j.get("decision") == "verified", j
    eid = dbq("SELECT id FROM evidence WHERE challenge_id=?", cid)[0]
    assert rb01.post(f"/fdrs/{fid}/decide", action="accept", evidence_id=str(eid)).status_code == 303
    assert rb01.post(f"/fdrs/{fid}/decide", action="release", evidence_id=str(eid), note="RB/REL/2026/0950 term completed",
                     release_type="term_completed").status_code == 303
    memo = b"%PDF-1.4\n% clearance memo\n%%EOF\n"
    for user, dept in (("pg01.prod", "PGRC"), ("vg01.prod", "VIG"), ("fn01.prod", "FIN")):
        o = officer(user)
        r = o.c.post(f"/fdrs/{fid}/release/clear", data={"csrf": o.csrf, "dept": dept, "decision": "clear", "note": "Register checked: nothing pending"},
                     files={"memo": ("m.pdf", memo + dept.encode() + fd.encode(), "application/pdf")})
        assert r.status_code == 303, r.text
    return fid, fd


def _version(sess, fid):
    return re.search(r'name="version" value="([^"]+)"', sess.c.get(f"/fdrs/{fid}").text).group(1)


def test_p_institution_cannot_reach_another_institutions_deposit():
    rb01, inst42, inst77 = officer("rb01.prod"), P("inst42.prod"), P("inst77.prod")
    fd = _fd()
    rb01.post("/fdrs/new", institution_id="INST-42", fd_number=fd, bank_host="netbanking.mockbank.test", principal="500000",
              issue_date="2024-01-01", maturity="2034-01-01")
    fid = dbq("SELECT id FROM fdrs WHERE fd_number=?", fd)[0]
    rb01.post(f"/fdrs/{fid}/challenge")
    cid = dbq("SELECT id FROM challenges WHERE fdr_id=? ORDER BY issued_at DESC", fid)[0]
    assert inst77.c.get(f"/fdrs/{fid}").status_code == 404
    assert inst77.c.get(f"/challenges/{cid}").status_code == 404
    assert inst77.post(f"/challenges/{cid}/prove").status_code in (403, 404)
    assert dbq("SELECT state FROM challenges WHERE id=?", cid)[0] == "issued"          # untouched by the other institution
    assert inst42.c.get(f"/fdrs/{fid}").status_code == 200


def test_p_proposer_cannot_approve_and_stale_version_is_refused():
    rb01, inst = officer("rb01.prod"), P("inst42.prod")
    fid, _ = _confirmed_case(rb01, inst)
    v = _version(officer("rb02.prod"), fid)
    assert rb01.post(f"/fdrs/{fid}/release/approve", version=v, otp=fresh_code("RB-01")).status_code == 403   # maker != checker
    rb02 = officer("rb02.prod")
    assert rb02.post(f"/fdrs/{fid}/release/approve", version="0" * 64 + ":0", otp=fresh_code("RB-02")).status_code == 409
    assert dbq("SELECT count(*) FROM release_approvals a JOIN release_requests r ON r.id=a.release_id WHERE r.fdr_id=?", fid)[0] == 0


def test_p_replayed_otp_is_refused():
    c = fresh_code("RB-02")
    assert P("rb02.prod", c).r.status_code == 303
    assert P("rb02.prod", c).r.status_code == 403                                      # same code a second time


def test_p_forged_internal_evidence_is_refused():
    fake = {"challenge_id": "CH-FORGED", "gate": "facts_ok", "fields": {"status": {"state": "proven", "value": "ACTIVE"}}}
    assert httpx.post(B + "/internal/evidence", json=fake).status_code == 403
    assert httpx.post(B + "/internal/evidence", json=fake, headers={"X-Internal-Key": "0" * 64}).status_code == 403
    assert httpx.post(B + "/internal/challenges/consume", json={"challenge_id": "x"}, headers={"X-Internal-Key": "nope"}).status_code == 403


def test_p_only_the_completion_recorder_can_complete():
    rb01, inst = officer("rb01.prod"), P("inst42.prod")
    fid, fd = _confirmed_case(rb01, inst)
    rb02 = officer("rb02.prod")
    assert rb02.post(f"/fdrs/{fid}/release/approve", version=_version(rb02, fid), otp=fresh_code("RB-02")).status_code == 303
    letter = dbq("SELECT letter_no FROM release_requests WHERE fdr_id=? AND state='approved'", fid)[0]
    ack = f"%PDF-1.4\n% pledge released per {letter} for {fd}, amount 750000.00\n%%EOF\n".encode()
    data = {"letter_no": letter, "fd_number": fd, "amount": "750000.00", "bank_ref": "BANK/ACK/2026/0950"}
    for who in (officer("rb02.prod"), rb01, inst):                                     # signer, proposer, institution
        r = who.c.post(f"/fdrs/{fid}/release/complete", data={"csrf": who.csrf, **data}, files={"ack": ("a.pdf", ack, "application/pdf")})
        assert r.status_code in (401, 403), (r.status_code, r.text[:200])
    assert dbq("SELECT released_at FROM fdrs WHERE id=?", fid)[0] is None
    rb04 = officer("rb04.prod")
    r = rb04.c.post(f"/fdrs/{fid}/release/complete", data={"csrf": rb04.csrf, **data}, files={"ack": ("a.pdf", ack, "application/pdf")})
    assert r.status_code == 303 and dbq("SELECT released_at FROM fdrs WHERE id=?", fid)[0] is not None


def test_p_concurrent_signatures_on_one_version_record_exactly_one():
    import threading
    rb01, inst = officer("rb01.prod"), P("inst42.prod")
    fid, _ = _confirmed_case(rb01, inst, principal="15000000")                          # >= Rs 1 crore: two signers needed
    rb02, rb03 = officer("rb02.prod"), officer("rb03.prod")
    v = _version(rb02, fid)
    c2, c3 = fresh_code("RB-02"), fresh_code("RB-03")
    out = {}
    t = [threading.Thread(target=lambda: out.__setitem__("rb02", rb02.post(f"/fdrs/{fid}/release/approve", version=v, otp=c2).status_code)),
         threading.Thread(target=lambda: out.__setitem__("rb03", rb03.post(f"/fdrs/{fid}/release/approve", version=v, otp=c3).status_code))]
    [x.start() for x in t]; [x.join() for x in t]
    assert sorted(out.values()) == [303, 409], out                                     # one signs; the other saw a stale version
    n = dbq("SELECT count(*) FROM release_approvals a JOIN release_requests r ON r.id=a.release_id WHERE r.fdr_id=?", fid)[0]
    assert n == 1
    assert dbq("SELECT state FROM release_requests WHERE fdr_id=?", fid)[0] == "pending"   # still needs the second signature


def test_p_failure_mid_approval_rolls_everything_back():
    from app.settings import VAR
    rb01, inst = officer("rb01.prod"), P("inst42.prod")
    fid, _ = _confirmed_case(rb01, inst)
    key = (VAR / "internal.key").read_text().strip()
    assert httpx.post(B + "/internal/fault", data={"point": "after_state"}, headers={"x-internal-key": key}).status_code == 200
    rb02 = officer("rb02.prod")
    v = _version(rb02, fid)
    events0 = dbq("SELECT count(*) FROM events")[0]
    try:                                     # the server answers 500 (and may drop the keep-alive connection while doing so)
        status = rb02.post(f"/fdrs/{fid}/release/approve", version=v, otp=fresh_code("RB-02")).status_code
    except httpx.TransportError:
        status = 500
        rb02 = officer("rb02.prod")
    assert status == 500
    assert httpx.get(B + "/api/health").status_code == 200                          # the server stays up
    assert dbq("SELECT state FROM release_requests WHERE fdr_id=?", fid)[0] == "pending"
    assert dbq("SELECT count(*) FROM release_approvals a JOIN release_requests r ON r.id=a.release_id WHERE r.fdr_id=?", fid)[0] == 0
    assert dbq("SELECT count(*) FROM events WHERE kind='release_instruction_issued' AND fdr_id=?", fid)[0] == 0
    assert dbq("SELECT count(*) FROM events")[0] >= events0                             # only sign-in style events may be added
    assert rb02.post(f"/fdrs/{fid}/release/approve", version=_version(rb02, fid), otp=fresh_code("RB-02")).status_code == 303
