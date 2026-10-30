"""Fast unit tests (no server): TOTP, vault, e-advice, deposit adequacy, term gaps, rupee format, audit hash chain."""
import base64
import os
import sys
import time
from datetime import date

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("SCOURGE_VAR", os.path.join(ROOT, "var_unit"))
sys.path.insert(0, ROOT)

from app import eadvice, lifecycle, totp, vault  # noqa: E402
from app.db import GENESIS, chain_hash  # noqa: E402


# ---------------------------------------------------------------- TOTP (RFC 6238 Appendix B vectors, SHA-1, 8 digits → we check 6-digit truncation)
RFC_SECRET = base64.b32encode(b"12345678901234567890").decode().rstrip("=")


@pytest.mark.parametrize("t,expected8", [(59, "94287082"), (1111111109, "07081804"), (1111111111, "14050471"),
                                         (1234567890, "89005924"), (2000000000, "69279037"), (20000000000, "65353130")])
def test_totp_rfc6238_vectors(t, expected8):
    assert totp.code_at(RFC_SECRET, t // 30) == expected8[-6:]


def test_totp_verify_window_and_replay():
    s = totp.new_secret()
    t = 1_800_000_000
    st = totp.now_step(t)
    assert totp.verify(s, totp.code_at(s, st), 0, t) == st
    assert totp.verify(s, totp.code_at(s, st - 1), 0, t) == st - 1
    assert totp.verify(s, totp.code_at(s, st + 1), 0, t) == st + 1
    assert totp.verify(s, totp.code_at(s, st - 2), 0, t) is None          # outside window
    assert totp.verify(s, totp.code_at(s, st), st, t) is None              # replay
    assert totp.verify(s, "12a456", 0, t) is None and totp.verify(s, "", 0, t) is None


# ---------------------------------------------------------------- vault
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


@pytest.mark.parametrize("data,ok", [(PDF, True), (b"\x89PNG\r\n\x1a\n" + b"0" * 10, True), (b"\xff\xd8\xff\xe0" + b"0" * 10, True),
                                     (b"", False), (b"GIF89a", False), (b"MZ\x90", False), (b"%PDF-1.4 no eof", False),
                                     (b"%PDF-1.4\n/JavaScript\n%%EOF", False), (b"%PDF-1.4\n/Launch\n%%EOF", False),
                                     (b"%PDF-1.4\n/EmbeddedFile\n%%EOF", False), (b"%PDF-1.4\n/Encrypt\n%%EOF", False),
                                     (b"%PDF-1.4\n/RichMedia\n%%EOF", False), (b"%PDF-" + b"0" * (2 * 1024 * 1024) + b"%%EOF", False)])
def test_vault_content_checks(data, ok):
    if ok:
        assert vault.check(data)
    else:
        with pytest.raises(vault.Refused):
            vault.check(data)


def test_vault_roundtrip_and_tamper_detection():
    name, wk, nonce, sha = vault.seal("FDR-T", PDF)
    row = {"fdr_id": "FDR-T", "sha256": sha, "stored_as": name, "wrapped_key": wk, "nonce": nonce}
    assert vault.open_doc(row) == PDF
    assert PDF not in (vault.VAULT / name).read_bytes()
    with pytest.raises(vault.IntegrityFailure):                                     # bound to its FDR
        vault.open_doc({**row, "fdr_id": "FDR-OTHER"})
    with pytest.raises(vault.IntegrityFailure):                                     # bound to its hash
        vault.open_doc({**row, "sha256": "0" * 64})
    p = vault.VAULT / name
    b = bytearray(p.read_bytes()); b[0] ^= 1; p.write_bytes(bytes(b))
    with pytest.raises(vault.IntegrityFailure):
        vault.open_doc(row)


# ---------------------------------------------------------------- deposit adequacy (APH Table 1.4)
@pytest.mark.parametrize("progs,leased,exempt,lakh", [
    ([["engineering", "ug"]], 0, None, 35), ([["engineering", "diploma"]], 0, None, 15), ([["engineering", "pg"]], 0, None, 15),
    ([["management", "ug"]], 0, None, 5), ([["management", "pg"]], 0, None, 15), ([["computer_applications", "ug"]], 0, None, 5),
    ([["computer_applications", "diploma"]], 0, None, 0), ([["design", "ug"]], 0, None, 15), ([["hmct", "pg"]], 0, None, 15),
    ([["engineering", "ug"], ["management", "pg"]], 0, None, 50), ([["engineering", "ug"]], 1, None, 70),
    ([["engineering", "ug"]], 1, "government", 0), ([], 0, None, 0)])
def test_required_amount(progs, leased, exempt, lakh):
    req, _ = lifecycle.required_rupees({"programmes": progs, "leased": leased, "exempt": exempt})
    assert req == lakh * 100000


@pytest.mark.parametrize("issue,mat,gap", [("2020-01-01", "2030-01-01", None), ("2020-01-01", "2025-01-01", 1826),
                                           ("2020-02-29", "2030-02-28", None), ("bad", "2030-01-01", None)])
def test_term_gap(issue, mat, gap):
    assert lifecycle.term_gap({"issue_date": issue, "maturity": mat}) == gap


@pytest.mark.parametrize("x,s", [(0, "₹0"), (500, "₹500"), (150000, "₹1,50,000"), (5000000, "₹50,00,000"), (12000000, "₹1,20,00,000"), (999999999, "₹99,99,99,999")])
def test_indian_rupee_format(x, s):
    assert lifecycle.rupees(x) == s


# ---------------------------------------------------------------- audit hash chain
def test_chain_hash_is_deterministic_and_sensitive():
    h1 = chain_hash(GENESIS, 1.5, "officer", "x", "FDR-1", "{}")
    assert h1 == chain_hash(GENESIS, 1.5, "officer", "x", "FDR-1", "{}") and len(h1) == 64
    for args in [(GENESIS, 1.6, "officer", "x", "FDR-1", "{}"), (GENESIS, 1.5, "officeR", "x", "FDR-1", "{}"),
                 (GENESIS, 1.5, "officer", "y", "FDR-1", "{}"), (GENESIS, 1.5, "officer", "x", "FDR-2", "{}"),
                 (GENESIS, 1.5, "officer", "x", "FDR-1", "{ }"), ("1" * 64, 1.5, "officer", "x", "FDR-1", "{}")]:
        assert chain_hash(*args) != h1


# ---------------------------------------------------------------- e-advice
CA = open(os.path.join(ROOT, "certs/test_ca.der"), "rb").read()
KEY, CERT = open(os.path.join(ROOT, "certs/bank_ok.key.pem"), "rb").read(), open(os.path.join(ROOT, "certs/bank_ok.pem"), "rb").read()
FDR = {"bank_host": "netbanking.mockbank.test", "fd_number": "FD-0000777777"}
ALLOW = ["netbanking.mockbank.test"]


def test_eadvice_valid():
    doc = eadvice.issue("netbanking.mockbank.test", "FD-0000777777", {"lien": "AICTE NEW DELHI", "status": "ACTIVE"}, KEY, CERT)
    r = eadvice.verify(doc, FDR, CA, ALLOW)
    assert r["ok"] and r["fields"] == {"lien": "AICTE NEW DELHI", "status": "ACTIVE"}


@pytest.mark.parametrize("mutate,reason", [
    (lambda d: d["payload"]["fields"].update(lien="EDITED"), "altered or forged"),
    (lambda d: d["payload"].update(fd_number="FD-OTHER"), "altered or forged"),
    (lambda d: d.update(signature="!!!"), "not a readable"),
    (lambda d: d["payload"].update(type="x"), "unknown type"),
    (lambda d: d.update(certificate="junk"), "not a readable")])
def test_eadvice_rejections(mutate, reason):
    doc = eadvice.issue("netbanking.mockbank.test", "FD-0000777777", {"lien": "AICTE"}, KEY, CERT)
    mutate(doc)
    r = eadvice.verify(doc, FDR, CA, ALLOW)
    assert not r["ok"] and reason in r["reason"]


def test_eadvice_wrong_fd_old_and_unapproved():
    old = eadvice.issue("netbanking.mockbank.test", "FD-0000777777", {"lien": "AICTE"}, KEY, CERT, issued_at=time.time() - 40 * 86400)
    assert "older than" in eadvice.verify(old, FDR, CA, ALLOW)["reason"]
    future = eadvice.issue("netbanking.mockbank.test", "FD-0000777777", {"lien": "AICTE"}, KEY, CERT, issued_at=time.time() + 3600)
    assert "future" in eadvice.verify(future, FDR, CA, ALLOW)["reason"]
    other = eadvice.issue("netbanking.mockbank.test", "FD-1", {"lien": "AICTE"}, KEY, CERT)
    assert "different deposit" in eadvice.verify(other, FDR, CA, ALLOW)["reason"]
    assert "approved list" in eadvice.verify(eadvice.issue("netbanking.mockbank.test", "FD-0000777777", {"lien": "A"}, KEY, CERT), FDR, CA, [])["reason"]
    fake = eadvice.issue("netbanking.mockbank.test", "FD-0000777777", {"lien": "A"},
                         open(os.path.join(ROOT, "certs/bank_fake.key.pem"), "rb").read(), open(os.path.join(ROOT, "certs/bank_fake.pem"), "rb").read())
    assert "trusted bank root" in eadvice.verify(fake, FDR, CA, ALLOW)["reason"]


# ---------------------------------------------------------------- reports / reconciliation
from app import reports as _rep

SAMPLES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "static", "samples")


def _row(fid, fd, principal="1000000.00", maturity="2031-07-01", st="confirmed"):
    return {"f": {"id": fid, "fd_number": fd, "principal": principal, "maturity": maturity, "institution_id": "I", "inst_name": "X",
                  "bank_host": "netbanking.mockbank.test", "issue_date": "2021-07-01"}, "status": (st, st.title(), ""), "evidence": []}


REG = [_row("FDR-001", "FD-0000123456"), _row("FDR-002", "FD-0000777777", "2500000.00", "2029-01-15"),
       _row("FDR-003", "FD-0000555555", "1500000.00", "2030-03-31"), _row("FDR-004", "FD-0000444444", "500000.00", "2032-04-01")]


def _sample(n):
    with open(os.path.join(SAMPLES, n), "rb") as fh:
        return fh.read()


def test_formula_injection_neutralised():
    assert _rep.safe_cell("=HYPERLINK(1)") == "'=HYPERLINK(1)"
    assert _rep.safe_cell("+91") == "'+91" and _rep.safe_cell("@x") == "'@x" and _rep.safe_cell("ok") == "ok"
    csvtxt = _rep.to_csv([_row("FDR-9", "=CMD()")])
    assert "'=CMD()" in csvtxt


def test_xlsx_export_opens_and_has_summary():
    import openpyxl, io as _io
    rows = [dict(r, f=dict(r["f"])) for r in REG]
    wb = openpyxl.load_workbook(_io.BytesIO(_rep.to_xlsx(rows, _rep.kpis(rows))))
    assert wb.sheetnames == ["Register", "Summary"] and wb["Register"].max_row >= 5


def test_reconcile_csv_register():
    kind, items = _rep.parse("r.csv", _sample("bank_fd_register.csv"))
    assert kind == "register" and len(items) == 4
    f = _rep.reconcile(kind, items, REG)
    texts = " | ".join(x["text"] for x in f)
    assert any(x["fdr"] == "FDR-002" and "Lien" in x["text"] for x in f)
    assert any(x["fdr"] == "FDR-004" and "missing" in x["text"] for x in f)
    assert any(x["fd"] == "FD-0000999999" and x["level"] == "info" for x in f)
    assert not any(x["fdr"] == "FDR-001" for x in f), texts


def test_reconcile_camt053_and_mt940():
    k1, e1 = _rep.parse("s.xml", _sample("statement.camt053.xml"))
    f1 = _rep.reconcile(k1, e1, REG)
    assert k1 == "statement" and len(e1) == 2 and [x["fdr"] for x in f1] == ["FDR-003"]
    k2, e2 = _rep.parse("s.940", _sample("statement.mt940"))
    f2 = _rep.reconcile(k2, e2, REG)
    assert len(e2) == 2 and [x["fdr"] for x in f2] == ["FDR-002"] and "2,125,000.00" in f2[0]["text"]


def test_reconcile_rejects_bad_files():
    for data in (b"<!DOCTYPE x [<!ENTITY a 'b'>]><x/>", b"<notcamt/>", b"\xff\xfe\x00", b"a,b\n1,2\n", b"x" * (_rep.MAX_BYTES + 1)):
        with pytest.raises(_rep.BadFile):
            _rep.parse("f", data)


def test_released_deposits_ignored_by_reconcile():
    reg = [_row("FDR-003", "FD-0000555555", st="released")]
    k, e = _rep.parse("s.xml", _sample("statement.camt053.xml"))
    assert _rep.reconcile(k, e, reg) == []
