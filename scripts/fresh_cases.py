"""Fresh-case check of the central result. Each case changes what the SYNTHETIC bank's server says
(as text, in formats Indian net-banking pages use), runs the REAL prover + verifier, and compares the
decision with an answer worked out by hand, independently of app/policy.py.
Register for FDR-002: principal 2500000.00, maturity 2029-01-15, lien to AICTE.
Run with the app up:  python3 scripts/fresh_cases.py"""
import json, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from test_acceptance import S, prove, evidence  # noqa: E402

import os  # noqa: E402
VARDIR = Path(os.environ.get("SCOURGE_VAR", ROOT / "var"))
STATE = VARDIR / "bank_state.json"
# (name, deposit overrides, behaviour, expected decision, why a careful officer would conclude that)
CASES = [
    ("plain",            {}, "active", "verified", "baseline: bank agrees"),
    ("rs_dot_lakh",      {"principal": "Rs. 25,00,000.00"}, "active", "verified", "same amount, Indian grouping, 'Rs.' prefix"),
    ("inr_prefix",       {"principal": "INR 25,00,000.00"}, "active", "verified", "same amount"),
    ("slash_dash",       {"principal": "25,00,000/-"}, "active", "verified", "'/-' is the usual Indian 'and no paise'"),
    ("western_grouping", {"principal": "2,500,000.00"}, "active", "verified", "same amount"),
    ("dr_balance",       {"principal": "25,00,000.00 Dr"}, "active", "insufficient_evidence", "'Dr' is a debit, not a deposit balance: cannot be read as the deposit"),
    ("lakh_words",       {"principal": "25 Lakh"}, "active", "insufficient_evidence", "words, not a number: unreadable, must NOT read as Rs 25"),
    ("date_dd_mm",       {"maturity": "15-01-2029"}, "active", "verified", "dd-mm-yyyy, same date"),
    ("date_slash",       {"maturity": "15/01/2029"}, "active", "verified", "same date"),
    ("date_mon",         {"maturity": "15-Jan-2029"}, "active", "verified", "same date, month name"),
    ("date_mon_space",   {"maturity": "15 Jan 2029"}, "active", "verified", "same date"),
    ("date_mon_upper",   {"maturity": "15-JAN-2029"}, "active", "verified", "same date (Finacle style)"),
    ("date_wrong",       {"maturity": "15-Jan-2027"}, "active", "discrepancy", "really a different date"),
    ("amount_wrong",     {"principal": "Rs. 20,00,000.00"}, "active", "discrepancy", "really less money"),
]
# Held out: written after the fix, not used while fixing.
HOLDOUT = [
    ("h_rupee_no_paise", {"principal": "₹25,00,000"}, "active", "verified", "same amount"),
    ("h_rs_one_decimal", {"principal": "Rs 25,00,000.0"}, "active", "verified", "same amount"),
    # Changed from "verified" after h_crore_words showed "Cr" is ambiguous (credit vs crore): now unknown.
    ("h_cr_attached", {"principal": "25,00,000.00CR"}, "active", "insufficient_evidence", "'Cr' is ambiguous: credit or crore"),
    ("h_one_paisa_short", {"principal": "Rs. 24,99,999.99"}, "active", "discrepancy", "really one paisa less"),
    ("h_dr_upper", {"principal": "25,00,000.00 DR"}, "active", "insufficient_evidence", "debit"),
    ("h_crore_words", {"principal": "2.5 Cr"}, "active", "insufficient_evidence", "'2.5 Cr' is crores in words: unreadable"),
    ("h_dotted_date", {"maturity": "15.01.2029"}, "active", "verified", "same date"),
    ("h_iso_slash", {"maturity": "2029/01/15"}, "active", "verified", "same date"),
    ("h_two_digit_year", {"maturity": "15-01-29"}, "active", "insufficient_evidence", "century unknown"),
    ("h_ordinal", {"maturity": "15th Jan 2029"}, "active", "insufficient_evidence", "not a format the reader declares"),
    ("h_full_month_wrong", {"maturity": "15 January 2030"}, "active", "discrepancy", "really a different date"),
]
STATUS_HOLDOUT = [
    ("h_closed_mixed", "Closed ", "discrepancy", "known inactive, odd case/space"),
    ("h_lien_marked", "Lien Marked", "insufficient_evidence", "unlisted wording"),
]
STATUS_CASES = [  # status text as banks show it; patched into the record
    ("status_Active", "Active", "verified", "case only"),
    # Changed from "verified" after the first run: no source shows any bank uses OPEN for an active FD,
    # so adding it to the vocabulary would tune to this test. Unlisted wording must be unknown, not an accusation.
    ("status_open", "OPEN", "insufficient_evidence", "not in this bank's declared vocabulary: unknown"),
    ("status_matured", "MATURED", "discrepancy", "known not-active state"),
    ("status_prematurely_closed", "PREMATURELY CLOSED", "discrepancy", "known not-active state"),
    ("status_weird", "ACTIVE - LIEN MARKED", "insufficient_evidence", "unrecognised wording: unknown, must not be an accusation"),
]


def set_deposit(fd, **over):
    st = json.loads(STATE.read_text())
    for c in st["customers"].values():
        for d in c["deposits"]:
            if d["fd_number"] == fd:
                d.update(over)
    STATE.write_text(json.dumps(st))


def main():
    o = S("officer")
    rows, bad = [], 0
    if "--holdout" in sys.argv:
        cases = HOLDOUT + [(n, {"status_text": s}, "active", e, w) for n, s, e, w in STATUS_HOLDOUT]
    else:
        cases = CASES + [(n, {"status_text": s}, "active", e, w) for n, s, e, w in STATUS_CASES]
    for name, over, beh, expect, why in cases:
        assert o.post("/system/reset").status_code == 303
        set_deposit("FD-0000777777", **over)
        j, cid = prove(o, "FDR-002", beh)
        d = evidence(cid)[0] if j.get("evidence_id") else f"job {j['state']}: {j['error']}"
        m = evidence(cid)[3] if j.get("evidence_id") else {}
        ok = d == expect
        bad += not ok
        rows.append((name, expect, d, ok, {k: v["result"] for k, v in m.items()}))
        print(f"{'PASS' if ok else 'FAIL'}  {name:28s} expected {expect:22s} got {d:22s} {why}", flush=True)
    print(f"\n{len(rows) - bad}/{len(rows)} match the hand-worked answer")
    (VARDIR / "fresh_cases.json").write_text(json.dumps(rows, indent=1))
    return bad


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
