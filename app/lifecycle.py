"""Portal lifecycle rules beyond the bank proof:
  * deposit adequacy against AICTE APH 2024-27 Table 1.4 (config/deposit_rules.json),
  * 10-year term gaps,
  * the reminder / escalation ladder (idempotent daily job writing a notification outbox).
Everything here is deterministic and recomputed from the database; nothing is sent anywhere
(e-mail is delivered by app/mailer.py when SMTP is configured, with retry)."""
import json
import time
from datetime import date, timedelta

from . import db
from .settings import ROOT

RULES = json.loads((ROOT / "config" / "deposit_rules.json").read_text(encoding="utf-8"))
ESC = RULES["reminders"]["escalation_after_days"]
LADDER = [("officer", "Scrutiny officer"), ("regional_head", "Regional head"), ("authority", "Competent authority")]


def rupees(x: float) -> str:
    """Indian digit grouping: 5000000 -> ₹50,00,000."""
    n = str(int(round(x)))
    head, tail = n[:-3], n[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return "₹" + ",".join(parts + [tail]) if parts else "₹" + tail


def _add_years(d: date, n: int) -> date:
    try:
        return d.replace(year=d.year + n)
    except ValueError:            # 29 Feb
        return d.replace(year=d.year + n, day=28)


# ------------------------------------------------------------------ adequacy
def profile(inst_id):
    p = db.q1("SELECT * FROM institution_profile WHERE institution_id=?", (inst_id,))
    if not p:
        return {"institution_id": inst_id, "programmes": [], "leased": 0, "exempt": None}
    return {**p, "programmes": json.loads(p["programmes_json"])}


def required_rupees(prof) -> tuple[float, list]:
    """Returns (required amount in rupees, line items). Exempt institutions need 0."""
    if prof.get("exempt"):
        return 0.0, [{"label": RULES["exemptions"].get(prof["exempt"], prof["exempt"]), "lakh": 0}]
    items = []
    for prog, level in prof["programmes"]:
        row = RULES["programmes"].get(prog)
        lakh = row.get(level) if row else None
        if lakh is None:
            continue
        items.append({"label": f"{row['label']} · {RULES['levels'][level]}", "lakh": lakh})
    total = sum(i["lakh"] for i in items) * RULES["unit_rupees"]
    if prof.get("leased"):
        total *= RULES["leased_multiplier"]
    return float(total), items


def adequacy(inst_id, bundles):
    """bundles: fdr bundles of this institution. 'Filed' counts deposits held (not released / renewed-away);
    'confirmed' counts those whose latest bank-session confirmation is accepted and current."""
    prof = profile(inst_id)
    req, items = required_rupees(prof)
    held = [b for b in bundles if b["status"][0] not in ("released", "renewed")]
    filed = sum(float(b["f"]["principal"]) for b in held)
    confirmed = sum(float(b["f"]["principal"]) for b in held if b["base_status"][0] == "confirmed")
    return {"profile": prof, "required": req, "lines": items, "filed": filed, "confirmed": confirmed,
            "shortfall_filed": max(0.0, req - filed), "shortfall_confirmed": max(0.0, req - confirmed),
            "leased": bool(prof.get("leased")), "exempt": prof.get("exempt")}


def term_gap(f) -> int | None:
    """Days by which the FDR matures before its 10-year term ends (None if no gap)."""
    try:
        issue, mat = date.fromisoformat(f["issue_date"]), date.fromisoformat(f["maturity"])
    except ValueError:
        return None
    end = _add_years(issue, RULES["term_years"])
    return (end - mat).days if mat < end else None


# ------------------------------------------------------------------ reminders and escalation
def _notify(fdr_id, inst_id, kind, step, audience, title, body, as_of):
    for channel in ("in_app", "email"):
        db.x("INSERT OR IGNORE INTO notifications(fdr_id,institution_id,kind,step,audience,channel,title,body,created_at,as_of,status) "
             "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
             (fdr_id or "", inst_id, kind, step, audience, channel, title, body, time.time(), as_of.isoformat(),
              "shown" if channel == "in_app" else "queued"))


def run_daily(bundles_by_inst, as_of: date | None = None):
    """Idempotent: re-running the same day adds nothing (UNIQUE on fdr/kind/step/audience/channel)."""
    as_of = as_of or date.today()
    before = db.q1("SELECT COUNT(*) AS n FROM notifications")["n"]
    period = str(as_of.toordinal() // RULES["reminders"]["recheck_every_days"])
    for inst_id, bundles in bundles_by_inst.items():
        for b in bundles:
            f, st = b["f"], b["status"][0]
            if st in ("released", "renewed"):
                continue
            try:
                days = (date.fromisoformat(f["maturity"]) - as_of).days
            except ValueError:
                continue
            if days >= 0:
                due = [s for s in RULES["reminders"]["maturity_days"] if days <= s]
                if due:
                    s = min(due)
                    msg = f"{f['fd_number']} matures on {f['maturity']} ({days} days). File the renewed FDR and answer a fresh bank proof."
                    _notify(f["id"], inst_id, "maturity_reminder", f"T-{s}", "institution", f"Maturity reminder T-{s}", msg, as_of)
                    _notify(f["id"], inst_id, "maturity_reminder", f"T-{s}", "officer", f"Maturity reminder T-{s}", msg, as_of)
            elif not b.get("successor"):
                overdue = -days
                for aud, label in LADDER:
                    if overdue >= ESC[aud]:
                        _notify(f["id"], inst_id, "overdue_escalation", aud, aud, f"Escalated to {label}",
                                f"{f['fd_number']} matured {overdue} days ago and is neither renewed nor released.", as_of)
            if st in ("submitted", "stale", "insufficient_evidence", "rejected"):
                _notify(f["id"], inst_id, "recheck_due", period, "institution", "Bank re-check due",
                        f"{f['fd_number']} has no current bank confirmation. Answer AICTE's proof request when it arrives.", as_of)
                _notify(f["id"], inst_id, "recheck_due", period, "officer", "Bank re-check due",
                        f"{f['fd_number']}: request a bank-session proof (no current confirmation).", as_of)
            g = term_gap(f)
            if g:
                _notify(f["id"], inst_id, "term_gap", "once", "institution", "Deposit term shorter than 10 years",
                        f"{f['fd_number']} matures {g} days before its 10-year term ends. Renew it before maturity.", as_of)
                _notify(f["id"], inst_id, "term_gap", "once", "officer", "Deposit term shorter than 10 years",
                        f"{f['fd_number']} matures {g} days before its 10-year term ends.", as_of)
        a = adequacy(inst_id, bundles)
        if a["shortfall_filed"] > 0:
            _notify(None, inst_id, "deposit_shortfall", period, "institution", "Security deposit shortfall",
                    f"Required {rupees(a['required'])} (APH Table 1.4); filed {rupees(a['filed'])}. Deposit the balance of {rupees(a['shortfall_filed'])}.", as_of)
            _notify(None, inst_id, "deposit_shortfall", period, "officer", "Security deposit shortfall",
                    f"{inst_id}: required {rupees(a['required'])}, filed {rupees(a['filed'])}, short {rupees(a['shortfall_filed'])}.", as_of)
    added = db.q1("SELECT COUNT(*) AS n FROM notifications")["n"] - before
    if added:
        db.event("system", "daily_check", None, as_of=as_of.isoformat(), notifications_added=added)
    return added
