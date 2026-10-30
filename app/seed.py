"""Demo seed: SAMPLE institutions and FDRs, plus the synthetic bank's state.
Bank session tokens are random per reset and never shown in the UI."""
import json
import os
import secrets
import time

from . import db
from .settings import BANK_STATE

INSTITUTIONS = [
    ("INST-42", "Synthetic Institute of Technology", "Hyderabad"),
    ("INST-77", "Demo College of Engineering", "Pune"),
]
FDRS = [
    # id, institution, fd number, principal, issue date, maturity
    ("FDR-001", "INST-42", "FD-0000123456", "1000000.00", "2021-07-01", "2031-07-01"),
    ("FDR-002", "INST-42", "FD-0000777777", "2500000.00", "2019-01-15", "2029-01-15"),
    ("FDR-004", "INST-42", "FD-0000444444", "500000.00", "2022-04-01", "2032-04-01"),
    ("FDR-003", "INST-77", "FD-0000555555", "1500000.00", "2020-03-31", "2030-03-31"),
]
# programmes offered (APH Table 1.4 keys) and land status, for deposit adequacy
PROFILES = {
    "INST-42": {"programmes": [["engineering", "ug"], ["management", "pg"]], "leased": 0, "exempt": None},  # requires 35 + 15 = 50 lakh
    "INST-77": {"programmes": [["computer_applications", "pg"]], "leased": 0, "exempt": None},          # requires 15 lakh
}
BANK_HOST = "netbanking.mockbank.test"
CUSTOMERS = {
    "INST-42": {"name": "SYNTHETIC INSTITUTE OF TECHNOLOGY TRUST", "pan": "AAATS0000X", "customer_id": "CIF-SYN-0042"},
    "INST-77": {"name": "DEMO COLLEGE OF ENGINEERING SOCIETY", "pan": "AAADS7777Y", "customer_id": "CIF-SYN-0077"},
}
BEHAVIOURS = {
    "active": "Normal: deposit active, lien to AICTE",
    "closed": "Closed early (balance 0)",
    "reduced": "Partly withdrawn (balance 60%)",
    "lien_loan": "Loan raised against the deposit",
    "maturity_changed": "Maturity date changed at the bank",
    "no_lien": "Bank page does not show lien details",
    "no_status": "Bank page omits the status field",
    "duplicate_status": "Status field appears twice (ambiguous)",
    "malformed": "Broken response (truncated JSON)",
    "escaped_ws": "Unusual but valid formatting",
    "chunked": "Chunked transfer encoding",
    "redirect": "Session expired: redirects to login",
    "login": "Returns the HTML login page",
    "two_fdrs": "Response lists every deposit of the customer",
    "unrelated": "Returns a different deposit",
    "slow": "Bank responds after 8 seconds",
}


def bank_state_initial():
    st = {"sessions": {}, "customers": {}}
    for iid, c in CUSTOMERS.items():
        st["sessions"][secrets.token_hex(16)] = iid
        st["customers"][iid] = dict(c, deposits=[])
    for fid, iid, fd, p, issue, mat in FDRS:
        st["customers"][iid]["deposits"].append({
            "fd_number": fd, "principal": p, "maturity": mat, "behaviour": "active",
            "holders": f"{CUSTOMERS[iid]['name']} / MEMBER SECRETARY AICTE",
            "lien": "AICTE NEW DELHI (SECURITY DEPOSIT)"})
    return st


def write_bank_state(st):
    tmp = BANK_STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1))
    tmp.replace(BANK_STATE)


def read_bank_state():
    return json.loads(BANK_STATE.read_text())


# Named demo accounts (DEMO MODE ONLY; SCOURGE_AUTH=password deployments create accounts with scripts/create_account.py).
DEMO_PASSWORD = os.environ.get("SCOURGE_DEMO_PASSWORD", "Scourge-demo-2026")
DEMO_ACCOUNTS = [("rb01.scrutiny", "officer", None, "RB-01"), ("rb02.approver", "officer", None, "RB-02"),
                 ("rb03.authority", "officer", None, "RB-03"), ("inst42.registrar", "institution", "INST-42", None),
                 ("inst77.registrar", "institution", "INST-77", None)]


def seed():
    db.wipe()
    now = time.time()
    for i in INSTITUTIONS:
        db.x("INSERT INTO institutions VALUES(?,?,?)", i)
    for iid, pr in PROFILES.items():
        db.x("INSERT INTO institution_profile(institution_id,programmes_json,leased,exempt) VALUES(?,?,?,?)",
             (iid, json.dumps(pr["programmes"]), pr["leased"], pr["exempt"]))
    for fid, iid, fd, p, issue, mat in FDRS:
        db.x("INSERT INTO fdrs(id,institution_id,fd_number,bank_host,principal,issue_date,maturity,created_at,created_by) VALUES(?,?,?,?,?,?,?,?,?)",
             (fid, iid, fd, BANK_HOST, p, issue, mat, now, "seed"))
    write_bank_state(bank_state_initial())
    db.event("system", "demo_reset", note="sample data loaded: 2 institutions, 4 FDRs; synthetic bank reset")
    from . import accounts
    if accounts.role_picker_allowed():
        for u, role, inst, off in DEMO_ACCOUNTS:
            accounts.create(u, DEMO_PASSWORD, role, inst, off)
