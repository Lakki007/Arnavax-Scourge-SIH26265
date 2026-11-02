"""Inconvenient lifecycle cases, through the real prover, verifier and synthetic bank:
a renewal arriving while a release awaits approval, a bank acknowledgement that names an obsolete (revoked)
instruction, and a scheduler that was offline for a week."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import test_acceptance as T  # noqa: E402

pytestmark = T.pytestmark
db, S = T.db, T.S


@pytest.fixture()
def off():
    o = S("officer"); o.post("/system/reset")
    for n in ("verifier", "genuine", "fake", "unlisted"):
        o.post("/system/service", name=n, op="start")
    return o


def test_e1_renewal_confirmed_while_release_pending_blocks_the_release(off):
    T._proposed(off, "FDR-001")
    T._clear_all(fdr="FDR-001")
    inst = S("institution:INST-42")
    r = inst.post("/fdrs/new", renews="FDR-001", institution_id="INST-42", fd_number="FD-0000123777", bank_host="netbanking.mockbank.test",
                  principal="1000000", issue_date="2025-01-01", maturity="2035-01-01")
    assert r.status_code == 303, r.text
    new = db().execute("SELECT id FROM fdrs WHERE fd_number='FD-0000123777'").fetchone()[0]
    T.FD[new] = "FD-0000123777"
    off.post("/mockbank/open", fd_number="FD-0000123777")
    T._confirmed(off, new)                                     # the successor is now the deposit actually held
    r = T._approve(S("officer:RB-02"), "/fdrs/FDR-001/release/approve")
    assert r.status_code == 409, r.status_code               # releasing the old pledge now would release the wrong deposit
    assert db().execute("SELECT state FROM release_requests WHERE fdr_id='FDR-001'").fetchone()[0] == "pending"
    assert db().execute("SELECT released_at FROM fdrs WHERE id='FDR-001'").fetchone()[0] is None


def test_e2_acknowledgement_of_a_revoked_instruction_cannot_complete(off):
    fid, old_letter = T._issued(off, "FD-0000990610")
    rb2 = S("officer:RB-02")
    assert rb2.post(f"/fdrs/{fid}/release/revoke", reason="Superseded: amount corrected").status_code == 303
    eid = db().execute("SELECT evidence_id FROM release_requests WHERE fdr_id=? ORDER BY id DESC", (fid,)).fetchone()[0]
    assert off.post(f"/fdrs/{fid}/decide", action="release", evidence_id=str(eid), note="RB/REL/2026/0611 replacement").status_code == 303
    T._clear_all(fdr=fid)
    T._approve(S("officer:RB-02"), f"/fdrs/{fid}/release/approve")
    T._approve(S("officer:RB-03"), f"/fdrs/{fid}/release/approve")
    new_letter = db().execute("SELECT letter_no FROM release_requests WHERE fdr_id=? AND state='approved'", (fid,)).fetchone()[0]
    assert new_letter != old_letter
    rec = S("officer:RB-04")
    r = T._complete(rec, fid, old_letter, T.FD[fid], ack=T._ack(old_letter, T.FD[fid], n=7))
    assert r.status_code in (409, 422), r.status_code         # the bank acted on the obsolete letter
    assert db().execute("SELECT released_at FROM fdrs WHERE id=?", (fid,)).fetchone()[0] is None
    assert T._complete(rec, fid, new_letter, T.FD[fid], ack=T._ack(new_letter, T.FD[fid], n=8)).status_code == 303


def test_e3_scheduler_offline_for_a_week_catches_up_once(off):
    # FDR-002 matures 2029-01-15. Last run 5 days after maturity, then nothing for a week+.
    off.post("/notifications/run", as_of="2029-01-20")
    esc = lambda: dict(db().execute("SELECT step, COUNT(*) FROM notifications WHERE fdr_id='FDR-002' AND kind='overdue_escalation' GROUP BY step").fetchall())
    assert esc() == {"officer": 2}                            # in-app + e-mail
    off.post("/notifications/run", as_of="2029-02-10")        # back online 26 days after maturity
    assert esc() == {"officer": 2, "regional_head": 2, "authority": 2}
    n = db().execute("SELECT COUNT(*) FROM notifications").fetchone()[0]
    off.post("/notifications/run", as_of="2029-02-10")        # a second catch-up run (or two schedulers) adds nothing
    off.post("/notifications/run", as_of="2029-02-11")
    assert esc() == {"officer": 2, "regional_head": 2, "authority": 2}
    assert db().execute("SELECT COUNT(*) FROM notifications WHERE fdr_id='FDR-002' AND kind='overdue_escalation'").fetchone()[0] == 6
    assert db().execute("SELECT COUNT(*) FROM notifications").fetchone()[0] >= n


def _released(off, fd):
    fid, letter = T._issued(off, fd)
    assert T._complete(S("officer:RB-04"), fid, letter, T.FD[fid], ack=T._ack(letter, T.FD[fid], n=9)).status_code == 303
    return fid


def test_e4_retention_legal_hold_and_archive(off):
    """A closed record is kept for the retention period, a legal hold blocks archiving, an archived record is frozen."""
    fid = _released(off, "FD-0000990620")
    assert off.post(f"/fdrs/{fid}/archive").status_code == 409                 # still inside the retention period
    c = db(); c.execute("UPDATE fdrs SET released_at=released_at-11*365.25*86400 WHERE id=?", (fid,)); c.commit()
    assert off.post(f"/fdrs/{fid}/hold", reason="Court case W.P. 1234/2036 pending").status_code == 303
    assert off.post(f"/fdrs/{fid}/archive").status_code == 409                 # legal hold blocks archiving
    assert off.post(f"/fdrs/{fid}/hold/lift", note="Case disposed of on 1 Feb 2037").status_code == 303
    assert off.post(f"/fdrs/{fid}/archive").status_code == 303
    assert db().execute("SELECT archived_at IS NOT NULL FROM fdrs WHERE id=?", (fid,)).fetchone()[0] == 1
    assert off.post(f"/fdrs/{fid}/hold", reason="Another attempt to change it").status_code == 409   # frozen
    assert off.post(f"/fdrs/{fid}/challenge").status_code == 409
    assert "record_archived" in [r[0] for r in db().execute("SELECT kind FROM events WHERE fdr_id=?", (fid,))]


def test_e5_open_deposit_cannot_be_archived(off):
    assert off.post("/fdrs/FDR-001/archive").status_code == 409


def test_e6_evidence_matrix_says_what_is_known_and_how(off):
    T._confirmed(off, "FDR-001")
    m = off.c.get(T.B + "/api/fdrs/FDR-001/evidence-matrix").json()
    rows = {r["key"]: r for r in m["rows"]}
    assert rows["principal"]["kind"] == "crypto" and rows["principal"]["state"] == "Confirmed"
    assert rows["encumbrance"]["result"] == "match"
    assert m["fresh"] is True
    inst2 = S("institution:INST-77")
    assert inst2.c.get(T.B + "/api/fdrs/FDR-001/evidence-matrix").status_code == 404   # scoped like the page
