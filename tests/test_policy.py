"""Unit tests for the deterministic registry-match policy (no server needed)."""
import pytest
import os

os.environ.setdefault("SCOURGE_VAR", os.path.join(os.path.dirname(__file__), "..", "var", "unit"))

from app import policy  # noqa: E402

FDR = {"bank_host": "netbanking.mockbank.test", "principal": "1000000.00", "maturity": "2031-07-01"}


def facts(**fields):
    return {"gate": "facts_ok", "fields": {k: ({"state": "proven", "value": v} if v is not None else {"state": "not_exposed"}) for k, v in fields.items()}}


BASE = dict(status="ACTIVE", balance="1000000.00", principal="1000000.00", maturity="2031-07-01",
            holders="TRUST / MEMBER SECRETARY AICTE", lien="AICTE NEW DELHI")


def test_verified():
    assert policy.apply(facts(**BASE), FDR)[0] == "verified"


def test_gate_passthrough():
    assert policy.apply({"gate": "rejected"}, FDR)[0] == "rejected"
    assert policy.apply({"gate": "insufficient_evidence"}, FDR)[0] == "insufficient_evidence"
    assert policy.apply({"gate": "weird"}, FDR)[0] == "rejected"


def test_mismatches():
    for k, v in [("status", "CLOSED"), ("balance", "999999.99"), ("maturity", "2030-07-01"), ("holders", "TRUST ONLY"), ("lien", "LOAN 1 AGAINST DEPOSIT")]:
        d, m = policy.apply(facts(**dict(BASE, **{k: v})), FDR)
        assert d == "discrepancy" and m[k]["result"] == "mismatch", k


def test_required_missing_is_insufficient():
    for k in ("status", "balance", "maturity"):
        assert policy.apply(facts(**dict(BASE, **{k: None})), FDR)[0] == "insufficient_evidence", k


def test_lien_absent_is_unknown_not_clear():
    d, m = policy.apply(facts(**dict(BASE, lien=None)), FDR)
    assert d == "insufficient_evidence" and m["lien"]["result"] == "not_exposed" and "unknown" in m["lien"]["note"]


def test_formats():
    assert policy.money("₹10,00,000.00") == 1000000.0
    assert policy.money("1.2.3") is None
    assert policy.norm_date("01/07/2031") == "2031-07-01"
    assert policy.norm_date("2031-02-30") is None
    d, _ = policy.apply(facts(**dict(BASE, balance="10,00,000.00", maturity="01-07-2031")), FDR)
    assert d == "verified"


def test_unparseable_required_is_insufficient():
    assert policy.apply(facts(**dict(BASE, balance="ten lakh")), FDR)[0] == "insufficient_evidence"


def test_status_derivation():
    f = {"released_at": None}
    assert policy.fdr_status(f, [], [], None)[0] == "submitted"
    ev = [{"id": 1, "decision": "verified", "received_at": 2e9}]
    assert policy.fdr_status(f, ev, [], None)[0] == "verified"
    assert policy.fdr_status(f, ev, [{"evidence_id": 1, "action": "accept"}], None)[0] == "confirmed"
    old = [{"id": 1, "decision": "verified", "received_at": 1.0}]
    assert policy.fdr_status(f, old, [{"evidence_id": 1, "action": "accept"}], None)[0] == "stale"


def test_contradiction_outranks_unknown():
    d, m = policy.apply(facts(**dict(BASE, status="CLOSED", lien=None)), FDR)
    assert d == "discrepancy" and m["status"]["result"] == "mismatch" and m["lien"]["result"] == "not_exposed"


# Readers of the bank's text: unknown is None, never a guess (see scripts/fresh_cases.py for end-to-end runs)
@pytest.mark.parametrize("text,value", [
    ("2500000.00", 2500000.0), ("₹25,00,000.00", 2500000.0), ("Rs. 25,00,000.00", 2500000.0), ("INR 25,00,000.00", 2500000.0),
    ("25,00,000/-", 2500000.0), ("2,500,000.00", 2500000.0), ("Rs 24,99,999.99", 2499999.99),
    ("25 Lakh", None), ("2.5 Cr", None), ("25,00,000.00 Cr", None), ("25,00,000.00 Dr", None), ("-2500000", None),
    ("25,0,0000", None), ("1.2.3", None), ("", None), (None, None),
])
def test_money_reader(text, value):
    assert policy.money(text) == value


@pytest.mark.parametrize("text,value", [
    ("2029-01-15", "2029-01-15"), ("15-01-2029", "2029-01-15"), ("15/01/2029", "2029-01-15"), ("15.01.2029", "2029-01-15"),
    ("15-Jan-2029", "2029-01-15"), ("15 JAN 2029", "2029-01-15"), ("15 January 2029", "2029-01-15"), ("2029/01/15", "2029-01-15"),
    ("01/15/2029", None), ("31-Feb-2029", None), ("15-01-29", None), ("15th Jan 2029", None), ("15-Janx-2029", None),
])
def test_date_reader(text, value):
    assert policy.norm_date(text) == value


@pytest.mark.parametrize("status,decision", [
    ("ACTIVE", "verified"), ("Active", "verified"), ("CLOSED", "discrepancy"), ("Prematurely Closed", "discrepancy"),
    ("MATURED", "discrepancy"), ("OPEN", "insufficient_evidence"), ("ACTIVE - LIEN MARKED", "insufficient_evidence"),
])
def test_status_vocabulary_unknown_is_not_an_accusation(status, decision):
    d, m = policy.apply(facts(**dict(BASE, status=status)), FDR)
    assert d == decision, (status, d, m["status"])


def test_unreadable_amount_is_not_an_accusation():
    d, m = policy.apply(facts(**dict(BASE, balance="10 Lakh", principal="10 Lakh")), FDR)
    assert d == "insufficient_evidence" and m["balance"]["result"] == "unparseable"
