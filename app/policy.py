"""Registry-match policy (deterministic). Applied by the app to verifier FACTS.
Decision ∈ verified | discrepancy | insufficient_evidence | rejected.
Rule: a field the response does not expose is UNKNOWN, never treated as clear; a required unknown blocks
"verified", and any proven contradiction makes the result a discrepancy."""
import re
import time
from datetime import date

from .settings import DEMO, VERIFIER_CFG

DECISIONS = ("verified", "discrepancy", "insufficient_evidence", "rejected")


# Reading the bank's text. Anything not read with certainty is None (unknown), never a guess:
# a guess can turn "25 Lakh" into Rs 25 and accuse an honest institution.
# No "Cr"/"Dr" suffix is accepted: "Cr" is credit on a statement but crore in ordinary Indian writing
# ("2.5 Cr"), and the two cannot be told apart from the text, so either is unknown.
_MONEY = re.compile(r"^(?:₹|RS\.?|INR)?\s*(\d{1,3}(?:,\d{2,3})*|\d+)(\.\d{1,2})?\s*(?:/-)?$")


def money(s):
    """Amount in integer PAISE (exact; no floating point). '2500000.00', '₹25,00,000.00', 'Rs. 25,00,000.00',
    'INR 2,500,000', '25,00,000/-'. 'Cr'/'Dr' suffixes, a sign, words ('Lakh') or anything else -> None."""
    if s is None:
        return None
    t = " ".join(str(s).strip().upper().split())
    m = _MONEY.match(t)
    if not m:
        return None
    frac = (m.group(2) or ".")[1:].ljust(2, "0")
    return int(m.group(1).replace(",", "")) * 100 + int(frac)


_NEGATION = re.compile(r"\b(NO|NOT|NIL|NONE|REMOVED|RELEASED|REVOKED|CANCELL?ED|WITHDRAWN|LIFTED|MARKED OFF|EXPIRED|DELETED|DISCHARGED)\b")


def _norm(s):
    return " ".join(str(s).upper().split())


def read_lien(text, rules):
    """Deterministic, bank-specific reading of the lien field. Returns (result, note):
    'match' only for an exact listed 'in favour of AICTE' value; 'encumbrance' / 'no_lien' only for listed meanings;
    everything else 'unrecognised' (unknown: never a confirmation, never an accusation)."""
    if not rules:
        return "unrecognised", "this bank has no lien schema: unknown"
    u = _norm(text)
    if u in {_norm(v) for v in rules.get("in_favour_of_aicte", [])}:
        return "match", ""
    if any(re.fullmatch(pat, u) for pat in rules.get("encumbrance_patterns", [])):
        return "encumbrance", "the bank shows a loan or other encumbrance on the deposit"
    if u in {_norm(v) for v in rules.get("no_lien_values", [])}:
        return "no_lien", "the bank shows no lien in favour of AICTE"
    if _NEGATION.search(u):
        return "unrecognised", "the wording negates or removes a lien but is not in this bank's schema: unknown, never a confirmation"
    return "unrecognised", "lien wording not in this bank's schema: unknown, never a confirmation"


def holder_ok(text, need):
    """The required holder must be one of the listed holders, compared whole (not a substring)."""
    parts = {_norm(p) for p in re.split(r"[/,;&]|\bAND\b", str(text)) if p.strip()}
    return _norm(need) in parts


_FULL = ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"]
_MONTHS = {**{n: i for i, n in enumerate(_FULL, 1)}, **{n[:3]: i for i, n in enumerate(_FULL, 1)}, "SEPT": 9}


def norm_date(s):
    """yyyy-mm-dd, dd-mm-yyyy, dd/mm/yyyy, dd.mm.yyyy (Indian day-first), dd-Mon-yyyy, dd Mon yyyy.
    Anything else, or an impossible date, -> None."""
    if not s:
        return None
    p = [x for x in re.split(r"[-/. ]+", str(s).strip().upper()) if x]
    if len(p) != 3:
        return None
    if re.fullmatch(r"\d{4}", p[0]):
        y, m, d = p
    elif re.fullmatch(r"\d{4}", p[2]):
        d, m, y = p
    else:
        return None
    if m.isalpha():
        if m not in _MONTHS:
            return None
        m = _MONTHS[m]
    try:
        return date(int(y), int(m), int(d)).isoformat()
    except ValueError:
        return None


def _status_key(s):
    return " ".join(re.sub(r"[^A-Z ]", " ", str(s).upper()).split())


def apply(facts: dict, fdr: dict):
    """Return (decision, match) where match maps field -> {result, proven, registered, note}."""
    gate = facts.get("gate")
    if gate in ("rejected", "insufficient_evidence"):
        return gate, {}
    if gate != "facts_ok":
        return "rejected", {}
    schema = VERIFIER_CFG["banks"].get(fdr["bank_host"])
    if schema is None:
        return "rejected", {}
    fields = facts.get("fields", {})
    proven = {k: v.get("value") for k, v in fields.items() if v.get("state") == "proven"}
    required = set(schema["required"])
    match = {}
    insufficient = any(k not in proven for k in required)
    mismatch = False

    def put(k, result, registered=None, note="", bad=False):
        nonlocal mismatch
        match[k] = {"result": result, "proven": proven.get(k), "registered": registered, "note": note}
        if bad:
            mismatch = True

    if "status" in proven:
        k = _status_key(proven["status"])
        if k in {_status_key(a) for a in schema["active_values"]}:
            put("status", "match", "active")
        elif k in {_status_key(a) for a in schema.get("inactive_values", [])}:
            put("status", "mismatch", "active", "the bank reports the deposit is not active", True)
        else:
            put("status", "unrecognised", "active", "status wording not in this bank's schema: unknown, not an accusation")
            insufficient = insufficient or "status" in required
    if "balance" in proven:
        b, p = money(proven["balance"]), money(fdr["principal"])
        if b is None or p is None:
            put("balance", "unparseable", fdr["principal"], "balance could not be read as a number")
            insufficient = insufficient or "balance" in required
        else:
            ok = b >= p
            put("balance", "match" if ok else "mismatch", fdr["principal"],
                "at least the registered principal" if ok else "below the registered principal", not ok)
    if "principal" in proven:
        a, b = money(proven["principal"]), money(fdr["principal"])
        if a is None or b is None:
            put("principal", "unparseable", fdr["principal"])
        else:
            ok = a == b
            put("principal", "match" if ok else "mismatch", fdr["principal"], "", not ok)
    if "maturity" in proven:
        a, b = norm_date(proven["maturity"]), norm_date(fdr["maturity"])
        if a is None or b is None:
            put("maturity", "unparseable", fdr["maturity"], "date could not be read")
            insufficient = insufficient or "maturity" in required
        else:
            put("maturity", "match" if a == b else "mismatch", fdr["maturity"], "" if a == b else "maturity differs from the register", a != b)
    need = schema.get("holder_required")
    if "holders" in proven and need:
        ok = holder_ok(proven["holders"], need)
        put("holders", "match" if ok else "mismatch", f"includes {need}", "" if ok else f"{need} is not listed as a holder", not ok)
    if "lien" in proven:
        r, note = read_lien(proven["lien"], schema.get("lien_rules"))
        if r == "match":
            put("lien", "match", "lien in favour of AICTE", "")
        elif r in ("encumbrance", "no_lien"):
            put("lien", "mismatch", "lien in favour of AICTE", note, True)
        else:
            put("lien", "unrecognised", "lien in favour of AICTE", note)
            insufficient = insufficient or "lien" in required
    for k, v in fields.items():
        if k not in match:
            st = v.get("state")
            if st == "not_exposed":
                match[k] = {"result": "not_exposed", "proven": None, "registered": None, "note": "this response does not show it: unknown, not clear"}
            elif st == "ambiguous_duplicate_key":
                match[k] = {"result": "ambiguous", "proven": None, "registered": None, "note": "the field appears twice in the record"}
                insufficient = insufficient or k in required
            elif st == "proven":
                match[k] = {"result": "not_checked", "proven": v.get("value"), "registered": None, "note": "shown, not part of the policy"}
            else:
                match[k] = {"result": "unsupported", "proven": None, "registered": None, "note": st}
                insufficient = insufficient or k in required
    # A proven contradiction outranks an unknown elsewhere: a closed deposit whose page drops the lien is a discrepancy.
    if mismatch:
        return "discrepancy", match
    if insufficient:
        return "insufficient_evidence", match
    return "verified", match


LABEL = {
    "verified": "Verified",
    "discrepancy": "Discrepancy",
    "insufficient_evidence": "Insufficient evidence",
    "rejected": "Rejected",
}

REASONS = {
    "host_not_allow_listed": "The proof came from a server AICTE has not approved.",
    "host_differs_from_challenge_bank": "The proof came from a different bank than the one registered for this FDR.",
    "request_line_not_disclosed": "The request was hidden, so the endpoint could not be checked.",
    "request_not_an_approved_endpoint": "The request was not to an approved deposit-details page.",
    "redirect": "The bank answered with a redirect, not the deposit page. The session may have expired.",
    "unexpected_content_type": "The bank returned a web page (likely a login page), not deposit data.",
    "auth_failed": "The bank refused the session (not logged in).",
    "http_error": "The bank returned an error.",
    "status_not_disclosed": "The bank's status line was not disclosed.",
    "fd_record_not_disclosed": "No deposit record was disclosed: the response could not be read as deposit data, or the prover withheld it.",
    "partial_disclosure": "Part of the deposit record was withheld, so it cannot be read.",
    "bad_chunking": "The response framing was invalid.",
    "malformed_json": "The bank's response was not valid data.",
    "fd_number_differs_from_challenge": "The proof shows a different deposit than the one requested.",
    "no_fd_record": "The response did not contain any deposit record.",
    "fd_record_ambiguous": "The deposit appears more than once in the response.",
    "tls_proof_failed": "The TLS proof did not complete or did not verify.",
    "session_proof_outranks": "A bank-session proof in the last 30 days contradicts this; a branch confirmation cannot override it.",
}


FIELD_NAMES = {"status": "status", "balance": "balance", "maturity": "maturity date", "lien": "lien or loan details", "principal": "principal", "holders": "holders"}


def reason_text(code, facts=None):
    if code and code.startswith("required_field_unknown:"):
        names = [FIELD_NAMES.get(k, k) for k in code.split(":", 1)[1].split(",") if k]
        return ("The bank's response does not settle the " + ", ".join(names) +
                ". AICTE cannot treat an unknown as clear, so this is not a confirmation.")
    if code == "tls_proof_failed" and facts:
        t = str(facts.get("stages", {}).get("tls_proof", ""))
        if "certificate" in t:
            return "The server's certificate does not chain to a root the verifier trusts: not the genuine bank."
        if "unexpected end of file" in t or "closed" in t:
            return "The proof session ended before completion (cancelled, or the prover or bank disconnected)."
        return "The TLS proof did not complete or did not verify."
    return REASONS.get(code, code or "")


def fdr_status(fdr, evidence_rows, actions, open_challenge):
    """Derive the record status shown everywhere. Returns (key, label, detail)."""
    if fdr.get("released_at"):
        return "released", "Released", time.strftime("%d %b %Y", time.localtime(fdr["released_at"]))
    latest = evidence_rows[0] if evidence_rows else None
    if open_challenge and (not latest or latest["received_at"] < open_challenge["issued_at"]):
        return "requested", "Proof requested", "waiting for the institution"
    if not latest:
        return "submitted", "Submitted by institution", "not confirmed by any bank"
    acts = [a for a in actions if a["evidence_id"] == latest["id"]]
    if any(a["action"] == "escalate" for a in acts):
        return "escalated", "Escalated", LABEL[latest["decision"]]
    if latest["decision"] == "verified" or (latest["decision"] == "insufficient_evidence" and any(a["action"] == "accept" for a in acts)):
        # the second case is only reachable when the gap was closed by a verified bank-signed e-advice (see main.eadvice_state)
        if any(a["action"] == "accept" for a in acts):
            age_days = (time.time() - latest["received_at"]) / 86400
            if age_days > DEMO["confirmation_valid_days"]:
                return "stale", "Confirmation expired", f"older than {DEMO['confirmation_valid_days']} days"
            how = "Confirmed by bank branch" if '"branch_confirmation"' in (latest.get("facts_json") or "") else "Confirmed by bank session"
            return "confirmed", how, time.strftime("%d %b %Y, %H:%M", time.localtime(latest["received_at"]))
        return "verified", "Verified, awaiting officer", ""
    return latest["decision"], LABEL[latest["decision"]], ""
