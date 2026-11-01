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

OFFICERS = {"rb01.prod": "RB-01", "rb02.prod": "RB-02", "pg01.prod": "PG-01", "vg01.prod": "VG-01",
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
    if not db.q1("SELECT 1 AS x FROM accounts WHERE username='rb03.nomfa'"):
        accounts.create("rb03.nomfa", PW, "officer", None, "RB-03")       # deliberately not enrolled in 2FA
    yield


def code(oid, ahead=0):
    from app import totp
    return totp.code_at(SECRETS[oid], totp.now_step() + ahead)


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
    s = P(user, code(oid))
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
    assert P("rb03.nomfa").r.status_code == 403
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
    assert rb02.post(f"/fdrs/{fid}/release/approve", version=version, otp=code("RB-02", ahead=1)).status_code == 303
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
