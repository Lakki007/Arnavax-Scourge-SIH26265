"""Scourge portal: FastAPI + SQLite + server-rendered pages.
Officer journey: register FDR -> request bank proof -> evidence -> accept / escalate -> release gate.
Institution journey: open challenge -> prove from bank (real MPC-TLS prover, synthetic bank in demo) -> result."""
import base64
import csv
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import time
from datetime import date, datetime
from pathlib import Path

from fastapi.exceptions import RequestValidationError
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import PlainTextResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import accounts, auth, db, eadvice, i18n, jobs, lifecycle, mailer, policy, procs, reports, seed, totp, vault, views
from .settings import AICTE_SIGNING_KEY, ROOT, ACCESS_CODE, COOKIE_SECURE, DEMO, INTERNAL_KEY, UPLOAD_MAX_BYTES, UPLOAD_TYPES, UPLOADS, VERIFIER_CFG

HERE = Path(__file__).parent
app = FastAPI(title="Scourge", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
T = Jinja2Templates(directory=HERE / "templates")


# ----------------------------------------------------------------- helpers
inr = views.inr


def when(t):
    if not t:
        return ""
    return datetime.fromtimestamp(t).strftime("%d %b %Y, %H:%M:%S")


def ago(t):
    if not t:
        return ""
    d = time.time() - t
    if d < 60:
        return f"{int(d)} s ago"
    if d < 3600:
        return f"{int(d // 60)} min ago"
    if d < 86400:
        return f"{int(d // 3600)} h ago"
    return f"{int(d // 86400)} days ago"


T.env.globals.update(OFFICER_DEPT=auth.OFFICER_DEPT, OFFICERS=auth.OFFICERS, VAULT_KINDS=vault.KINDS, DEPTS=lifecycle.RULES['release']['departments'], inr=inr, when=when, ago=ago, LABEL=policy.LABEL, DEMO=DEMO, BEHAVIOURS=seed.BEHAVIOURS, DEMO_ALLOW=VERIFIER_CFG["allow_hosts"])
T.env.filters["fromjson"] = json.loads


def render(request, name, s=None, status=200, **ctx):
    s = s if s is not None else auth.read(request)
    lang = "hi" if request.cookies.get("lang") == "hi" and (not s or s.get("role") != "officer") else "en"
    return T.TemplateResponse(request, name, {"s": s, "path": request.url.path, "health": procs.health(), "lang": lang,
                                              "t": i18n.translator(lang), "HI_BODY": i18n.HI_ALERT_BODY, **ctx}, status_code=status)


@app.get("/lang/{code}")
def set_lang(request: Request, code: str):
    r = RedirectResponse(request.headers.get("referer") or "/portal", 303)
    r.set_cookie("lang", "hi" if code == "hi" else "en", max_age=365 * 86400, samesite="lax", secure=COOKIE_SECURE)
    return r


def fdr_bundle(fid, _depth=0):
    f = db.q1("SELECT f.*, i.name AS inst_name, i.city AS inst_city FROM fdrs f JOIN institutions i ON i.id=f.institution_id WHERE f.id=?", (fid,))
    if not f:
        return None
    ev = db.q("SELECT * FROM evidence WHERE fdr_id=? ORDER BY received_at DESC", (fid,))
    acts = db.q("SELECT * FROM actions WHERE fdr_id=? ORDER BY at DESC", (fid,))
    openc = db.q1("SELECT * FROM challenges WHERE fdr_id=? AND state='issued' ORDER BY issued_at DESC", (fid,))
    if not challenge_live(openc):
        openc = None
    base = policy.fdr_status(f, ev, acts, openc)
    st = base
    rel = db.q1("SELECT * FROM release_requests WHERE fdr_id=? AND state='pending' ORDER BY id DESC", (fid,))
    pred = db.q1("SELECT id, fd_number FROM fdrs WHERE id=?", (f["renews_fdr_id"],)) if f.get("renews_fdr_id") else None
    succ_row = db.q1("SELECT id FROM fdrs WHERE renews_fdr_id=?", (fid,))
    succ = None
    if succ_row and _depth < 20:
        sb = fdr_bundle(succ_row["id"], _depth + 1)
        succ = {"id": sb["f"]["id"], "fd_number": sb["f"]["fd_number"], "status": sb["status"]}
        if sb["status"][0] in ("confirmed", "released", "renewed", "release_pending") and base[0] != "released":
            st = ("renewed", "Renewed", f"into {succ['id']}, confirmed by the bank")
    if st == base and rel and base[0] == "confirmed":
        st = ("release_pending", "Release proposed", f"by {rel['proposed_by']}; needs a second officer")
    issued = db.q1("SELECT * FROM release_requests WHERE fdr_id=? AND state='approved' ORDER BY id DESC", (fid,)) if not f["released_at"] else None
    if issued and st[0] != "renewed":
        st = ("instruction_issued", "Release instruction issued", f"{issued['letter_no']}: awaiting the bank's acknowledgement")
    hold = db.q1("SELECT * FROM holds WHERE fdr_id=? AND lifted_at IS NULL ORDER BY id DESC", (fid,))
    if hold and base[0] != "released" and st[0] != "renewed":
        st = ("on_hold", "On hold", hold["reason"][:80])
    b = {"f": f, "evidence": ev, "actions": acts, "open": openc, "status": st, "base_status": base, "issued": issued,
         "release_request": rel, "predecessor": pred, "successor": succ, "hold": hold}
    if rel:
        b["clearances"] = {c["dept"]: c for c in db.q("SELECT * FROM release_clearances WHERE release_id=?", (rel["id"],))}
        b["release_fp"] = release_fingerprint(f, rel, b["clearances"], hold)
        allapp = db.q("SELECT * FROM release_approvals WHERE release_id=? ORDER BY at", (rel["id"],))
        b["approvals"] = [a for a in allapp if a.get("fp") in (None, b["release_fp"])]
        b["stale_approvals"] = [a for a in allapp if a.get("fp") not in (None, b["release_fp"])]
        b["release_version"] = f"{b['release_fp']}:{len(b['approvals'])}"
        b["signers_needed"] = 2 if float(f["principal"]) >= lifecycle.RULES["release"]["second_signer_from_rupees"] else 1
    b["eadvice"] = eadvice_state(b)
    b["branch"] = db.q1("SELECT * FROM branch_requests WHERE fdr_id=? ORDER BY created_at DESC", (fid,))
    b["documents"] = db.q("SELECT id, kind, filename, mime, size, sha256, uploaded_by, uploaded_at FROM documents WHERE fdr_id=? ORDER BY id DESC", (fid,))
    return b


def release_fingerprint(f, rel, clearances, hold):
    """What an approving officer signed off on. If any of it changes afterwards (bank evidence, clearances,
    documents, hold, principal), earlier signatures no longer count and must be given again."""
    docs = db.q("SELECT id, sha256 FROM documents WHERE fdr_id=? ORDER BY id", (f["id"],))
    material = {"evidence": rel["evidence_id"], "principal": f["principal"], "hold": hold["id"] if hold else None,
                "clearances": sorted((d, c["decision"]) for d, c in clearances.items()),
                "documents": [(d["id"], d["sha256"]) for d in docs]}
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()


def eadvice_state(b):
    """Field-level fallback. If the latest session proof was Insufficient evidence, a verified bank-signed
    e-advice uploaded after it may fill ONLY the fields the proof could not show. Returns None or
    {row, decision, match, filled} where decision is what the policy says with those fields filled in."""
    latest = b["evidence"][0] if b["evidence"] else None
    if not latest:
        return None
    row = db.q1("SELECT * FROM eadvices WHERE fdr_id=? AND evidence_id=? ORDER BY id DESC", (b["f"]["id"], latest["id"]))
    if not row:
        return None
    out = {"row": row, "decision": None, "match": {}, "filled": []}
    if not row["ok"] or latest["decision"] != "insufficient_evidence":
        return out
    facts = json.loads(latest["facts_json"])
    if facts.get("gate") not in ("facts_ok",):
        return out  # only a readable deposit record with gaps can be completed; a rejected proof cannot
    fields = {k: dict(v) for k, v in facts.get("fields", {}).items()}
    adv = json.loads(row["fields_json"])
    for k, v in adv.items():
        if fields.get(k, {}).get("state") != "proven":
            fields[k] = {"state": "proven", "value": v}
            out["filled"].append(k)
    if not out["filled"]:
        return out
    d, m = policy.apply({"gate": "facts_ok", "fields": fields}, b["f"])
    out["decision"], out["match"] = d, m
    return out


def challenge_live(c):
    return c and c["state"] == "issued" and c["expires_at"] > time.time()


def get_fdr_or_404(s, fid):
    b = fdr_bundle(fid)
    if not b or not auth.can_see_fdr(s, b["f"]):
        raise HTTPException(404, "No FDR with that ID is visible to you.")
    return b


def all_rows(s):
    if s["role"] == "officer":
        ids = [r["id"] for r in db.q("SELECT id FROM fdrs ORDER BY id")]
    else:
        ids = [r["id"] for r in db.q("SELECT id FROM fdrs WHERE institution_id=? ORDER BY id", (s["inst"],))]
    return [fdr_bundle(i) for i in ids]


# ----------------------------------------------------------------- request size cap
MAX_BODY = UPLOAD_MAX_BYTES + 256 * 1024  # largest legitimate request: one 2 MB upload plus form fields


@app.middleware("http")
async def cap_body(request: Request, call_next):
    """Refuse oversized bodies before they are read or spooled to disk."""
    if request.method in ("POST", "PUT", "PATCH"):
        n = request.headers.get("content-length")
        if n is None and request.headers.get("transfer-encoding", "").lower() == "chunked":
            return PlainTextResponse("Chunked request bodies are not accepted.", status_code=411)
        if n is not None and (not n.isdigit() or int(n) > MAX_BODY):
            return PlainTextResponse(f"Request too large (limit {MAX_BODY // 1024} KB).", status_code=413)
    return await call_next(request)


# ----------------------------------------------------------------- errors
@app.exception_handler(RequestValidationError)
async def bad_input(request: Request, exc: RequestValidationError):
    fields = ", ".join(str(e.get("loc", ["?"])[-1]) for e in exc.errors())
    return await http_err(request, HTTPException(422, f"Invalid or missing value: {fields}."))


@app.exception_handler(HTTPException)
async def http_err(request: Request, exc: HTTPException):
    if request.url.path.startswith(("/api/", "/internal/")):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
    if exc.status_code == 401:
        return RedirectResponse(f"/signin?next={request.url.path}", status_code=303)
    return render(request, "error.html", status=exc.status_code, code=exc.status_code, message=exc.detail)


# ----------------------------------------------------------------- lifecycle
@app.on_event("startup")
def startup():
    db.conn()
    if not db.q1("SELECT 1 AS x FROM institutions") or not seed.BANK_STATE.exists():
        seed.seed()
    jobs.reap_orphans()
    procs.start_all()


@app.on_event("shutdown")
def shutdown():
    procs.stop_all()


# ----------------------------------------------------------------- public pages
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    s = auth.read(request)
    if not s:
        return render(request, "home.html", recent=[], spec=None)
    recent = db.q("SELECT e.id, e.decision, e.received_at, e.fdr_id, f.fd_number, f.institution_id, i.name AS inst FROM evidence e JOIN fdrs f ON f.id=e.fdr_id JOIN institutions i ON i.id=f.institution_id ORDER BY e.received_at DESC LIMIT 8")
    recent = [r for r in recent if auth.can_see_fdr(s, r)]
    spec = None
    if recent:
        e = db.q1("SELECT * FROM evidence WHERE id=?", (recent[0]["id"],))
        spec = {"e": recent[0], "rc": views.receipt(json.loads(e["facts_json"]), "recv")}
    return render(request, "home.html", recent=recent, spec=spec)


@app.get("/how", response_class=HTMLResponse)
def how(request: Request):
    return render(request, "how.html")


@app.get("/find")
def find(request: Request, fd: str = ""):
    s = auth.require(request)
    fd = fd.strip().upper()
    if not fd:
        return RedirectResponse("/register" if s["role"] == "officer" else "/portal", 303)
    rows = db.q("SELECT id, institution_id FROM fdrs WHERE fd_number=? OR id=?", (fd, fd))
    rows = [r for r in rows if auth.can_see_fdr(s, r)]
    if rows:
        return RedirectResponse(f"/fdrs/{rows[0]['id']}", 303)
    return RedirectResponse(f"/register?q={fd}" if s["role"] == "officer" else "/portal?notfound=" + fd, 303)


@app.get("/signin", response_class=HTMLResponse)
def signin_page(request: Request, next: str = "/"):
    return render(request, "signin.html", institutions=db.q("SELECT * FROM institutions ORDER BY id"), next=next if next.startswith("/") and not next.startswith("//") else "/",
                  access_required=bool(ACCESS_CODE), picker=accounts.role_picker_allowed(), demo_accounts=seed.DEMO_ACCOUNTS if accounts.role_picker_allowed() else [])


def _safe_next(next, dest):
    return next if next and next.startswith("/") and not next.startswith(("//", "/\\")) and next not in ("/", "/signin") else dest


@app.post("/signin/password")
def signin_password(request: Request, username: str = Form(""), password: str = Form(""), otp: str = Form(""), next: str = Form("/")):
    """Named-account sign-in (scrypt passwords, lockout, TOTP for officers). The only sign-in in SCOURGE_AUTH=password mode."""
    user = username.strip().lower()[:40]
    if db.q1("SELECT COUNT(*) AS n FROM login_failures WHERE who=? AND at>?", ("acct:" + user, time.time() - LOCKOUT_SECONDS))["n"] >= LOCKOUT_FAILURES:
        db.event("anonymous", "signin_locked", username=user)
        raise HTTPException(429, "Too many failed attempts for this account. Sign-in is locked for 15 minutes.")
    a = accounts.verify(user, password[:200])

    def fail(msg, kind="signin_password_failed"):
        db.x("INSERT INTO login_failures(who,at) VALUES(?,?)", ("acct:" + user, time.time()))
        db.event("anonymous", kind, username=user)
        time.sleep(0.3)
        raise HTTPException(403, msg)
    if not a:
        fail("Wrong user name or password.")
    if a["role"] == "officer":
        mfa = db.q1("SELECT * FROM officer_mfa WHERE officer=? AND enabled=1", (a["officer"],))
        if MFA_REQUIRED and not mfa:
            raise HTTPException(403, "Two-factor sign-in is required for officers. Ask the administrator to enrol your authenticator.")
        if mfa:
            st = totp.verify(mfa["secret"], otp, mfa["last_step"])
            if st is None:
                fail(f"Enter the current 6-digit code from your authenticator app.", "signin_otp_failed")
            db.x("UPDATE officer_mfa SET last_step=? WHERE officer=?", (st, a["officer"]))
        cookie, dest = auth.make_cookie("officer", None, a["officer"]), "/register"
    else:
        cookie, dest = auth.make_cookie("institution", a["institution_id"]), "/portal"
    r = RedirectResponse(_safe_next(next, dest), 303)
    r.set_cookie(auth.COOKIE, cookie, httponly=True, samesite="lax", max_age=auth.MAX_AGE, secure=COOKIE_SECURE)
    db.event(f"officer:{a['officer']}" if a["role"] == "officer" else f"institution:{a['institution_id']}", "signed_in", username=user, method="password")
    return r


@app.post("/signin")
def signin(request: Request, role: str = Form(...), next: str = Form("/"), access_code: str = Form(""), otp: str = Form("")):
    if not accounts.role_picker_allowed():
        raise HTTPException(403, "Demo role selection is switched off. Sign in with your own account.")
    # The code gates entry. Someone already signed in may switch demo roles (the officer's
    # "Switch to INST-42 to answer it" step) without typing it again.
    if ACCESS_CODE and not auth.read(request) and not hmac.compare_digest(access_code.encode(), ACCESS_CODE.encode()):
        time.sleep(0.5)  # slows guessing; the code is shared with judges only
        db.event("anonymous", "signin_refused")
        raise HTTPException(403, "Wrong access code. Ask the team for the judges' code.")
    if role == "officer" or (role.startswith("officer:") and role.split(":", 1)[1] in auth.OFFICERS):
        who = role.split(":", 1)[1] if ":" in role else "RB-01"
        mfa = db.q1("SELECT * FROM officer_mfa WHERE officer=? AND enabled=1", (who,))
        if mfa:
            if db.q1("SELECT COUNT(*) AS n FROM login_failures WHERE who=? AND at>?", (who, time.time() - LOCKOUT_SECONDS))["n"] >= LOCKOUT_FAILURES:
                db.event("anonymous", "signin_locked", officer=who)
                raise HTTPException(429, f"Too many wrong codes for {who}. Sign-in is locked for 15 minutes.")
            st = totp.verify(mfa["secret"], otp, mfa["last_step"])
            if st is None:
                db.x("INSERT INTO login_failures(who,at) VALUES(?,?)", (who, time.time()))
                db.event("anonymous", "signin_otp_failed", officer=who)
                time.sleep(0.3)
                raise HTTPException(403, f"Officer {who} uses two-factor sign-in: enter the current 6-digit code from the authenticator app.")
            db.x("UPDATE officer_mfa SET last_step=? WHERE officer=?", (st, who))
        cookie, dest = auth.make_cookie("officer", None, who), "/register"
    elif role.startswith("institution:") and db.q1("SELECT 1 AS x FROM institutions WHERE id=?", (role.split(":", 1)[1],)):
        cookie, dest = auth.make_cookie("institution", role.split(":", 1)[1]), "/portal"
    else:
        raise HTTPException(400, "Unknown role.")
    if next and next.startswith("/") and not next.startswith(("//", "/\\")) and next not in ("/", "/signin"):
        dest = next
    r = RedirectResponse(dest, 303)
    r.set_cookie(auth.COOKIE, cookie, httponly=True, samesite="lax", max_age=auth.MAX_AGE, secure=COOKIE_SECURE)
    db.event("officer" if role.startswith("officer") else role, "signed_in", officer=role.split(":", 1)[1] if role.startswith("officer:") else None)
    return r


@app.post("/signout")
def signout(request: Request, csrf: str = Form("")):
    s = auth.read(request)
    if s:
        auth.check_csrf(s, csrf)
    r = RedirectResponse("/", 303)
    r.delete_cookie(auth.COOKIE)
    return r


# ----------------------------------------------------------------- officer: register
PAGE = 200


def monitor(allr, today=None):
    """Lifecycle monitoring: deposits that need action now. Derived from the register; nothing is sent anywhere."""
    today = today or date.today()
    warn = DEMO.get("maturity_warning_days", 90)
    out = {"matured": [], "maturing": [], "recheck": [], "flagged": []}
    for r in allr:
        st = r["status"][0]
        if st in ("released", "renewed"):
            continue
        try:
            days = (date.fromisoformat(r["f"]["maturity"]) - today).days
        except ValueError:
            days = None
        if days is not None and days < 0:
            out["matured"].append((r, days))
        elif days is not None and days <= warn:
            out["maturing"].append((r, days))
        if st in ("submitted", "stale", "insufficient_evidence", "rejected"):
            out["recheck"].append((r, days))
        if st in ("discrepancy", "escalated", "on_hold"):
            out["flagged"].append((r, days))
    out["warn_days"] = warn
    return out


@app.get("/register", response_class=HTMLResponse)
def register(request: Request, q: str = "", status: str = "", page: int = 1):
    s = auth.require(request, "officer")
    allr = all_rows(s)
    rows = allr
    counts = {}
    for r in rows:
        counts[r["status"][0]] = counts.get(r["status"][0], 0) + 1
    qq = q.strip().upper()
    if qq:
        rows = [r for r in rows if qq in r["f"]["fd_number"] or qq in r["f"]["id"] or qq in r["f"]["inst_name"].upper()]
    if status:
        rows = [r for r in rows if r["status"][0] == status]
    matched = len(rows)
    pages = max(1, -(-matched // PAGE))
    page = min(max(1, page), pages)
    rows = rows[(page - 1) * PAGE: page * PAGE]

    def latest_dec(r):
        return r["evidence"][0]["decision"] if r["evidence"] and r["status"][0] not in ("stale", "released") else None
    bank_ok = sum(1 for r in allr if latest_dec(r) == "verified")
    bank_bad = sum(1 for r in allr if latest_dec(r) == "discrepancy")
    paper = sum(1 for r in allr if latest_dec(r) not in ("verified", "discrepancy") and r["status"][0] != "released")
    bb = {}
    for r in allr:
        bb.setdefault(r["f"]["institution_id"], []).append(r)
    for i in db.q("SELECT id FROM institutions"):
        bb.setdefault(i["id"], [])
    lifecycle.run_daily(bb)
    mon = monitor(allr)
    mon["shortfall"] = [(iid, a) for iid, a in ((iid, lifecycle.adequacy(iid, v)) for iid, v in bb.items()) if a["shortfall_filed"] > 0]
    mon["termgap"] = [(r, lifecycle.term_gap(r["f"])) for r in allr if r["status"][0] not in ("released", "renewed") and lifecycle.term_gap(r["f"])]
    return render(request, "register.html", s, rows=rows, monitor=mon, q=q, fstatus=status, counts=counts, total=len(allr), paper=paper, bank_ok=bank_ok, bank_bad=bank_bad,
                  matched=matched, page=page, pages=pages)


@app.get("/fdrs/new", response_class=HTMLResponse)
def new_fdr_page(request: Request):
    s = auth.require(request)
    v = {"institution_id": s["inst"]} if s["role"] == "institution" else {}
    if request.query_params.get("renews"):
        v["renews"] = request.query_params["renews"]
    return render(request, "fdr_new.html", s, institutions=_insts_for(s), errors={}, v=v, renewable=_renewable(s))


def _renewable(s):
    """FDRs that can be renewed: not released and not already renewed."""
    rows = db.q("SELECT f.id, f.fd_number, f.institution_id, f.maturity FROM fdrs f WHERE f.released_at IS NULL "
                "AND NOT EXISTS (SELECT 1 FROM fdrs g WHERE g.renews_fdr_id=f.id) ORDER BY f.id")
    return [r for r in rows if s["role"] == "officer" or r["institution_id"] == s["inst"]]


def _insts_for(s):
    if s["role"] == "institution":
        return db.q("SELECT * FROM institutions WHERE id=?", (s["inst"],))
    return db.q("SELECT * FROM institutions")


FD_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{5,24}$")
AMT_RE = re.compile(r"^\d{1,10}(\.\d{1,2})?$")


@app.post("/fdrs/new", response_class=HTMLResponse)
def new_fdr(request: Request, csrf: str = Form(""), institution_id: str = Form(""), fd_number: str = Form(""),
            bank_host: str = Form(""), principal: str = Form(""), issue_date: str = Form(""), maturity: str = Form(""),
            renews: str = Form("")):
    # Step 1 of the workflow: the institution files its own claim. An officer may also enter one from a paper submission.
    s = auth.require(request)
    auth.check_csrf(s, csrf)
    if s["role"] == "institution":
        institution_id = s["inst"]  # an institution can only file for itself, whatever the form says
    by = "officer" if s["role"] == "officer" else "institution"
    v = {"institution_id": institution_id, "fd_number": fd_number.strip().upper(), "bank_host": bank_host,
         "principal": principal.replace(",", "").replace("₹", "").strip(), "issue_date": issue_date, "maturity": maturity}
    e = {}
    if not db.q1("SELECT 1 AS x FROM institutions WHERE id=?", (institution_id,)):
        e["institution_id"] = "Choose an institution."
    if not FD_RE.match(v["fd_number"]):
        e["fd_number"] = "Use 6–25 letters, digits or hyphens, e.g. FD-0000888888."
    elif db.q1("SELECT 1 AS x FROM fdrs WHERE fd_number=?", (v["fd_number"],)):
        e["fd_number"] = "This FD number is already registered."
    if bank_host not in VERIFIER_CFG["allow_hosts"]:
        e["bank_host"] = "Choose a bank AICTE has approved for bank-session proofs."
    if not AMT_RE.match(v["principal"]) or float(v["principal"] or 0) <= 0:
        e["principal"] = "Enter the principal in rupees, e.g. 750000 or 750000.00."
    try:
        di = date.fromisoformat(issue_date)
        if di > date.today():
            e["issue_date"] = "The issue date cannot be in the future."
    except ValueError:
        e["issue_date"] = "Enter a valid date."
        di = None
    try:
        dm = date.fromisoformat(maturity)
        if dm <= date.today():
            e["maturity"] = "A security deposit must mature in the future."
        elif di and dm <= di:
            e["maturity"] = "Maturity must be after the issue date."
    except ValueError:
        e["maturity"] = "Enter a valid date."
    v["renews"] = renews
    if renews:
        old = db.q1("SELECT * FROM fdrs WHERE id=?", (renews,))
        if not old or old["institution_id"] != institution_id:
            e["renews"] = "Choose one of this institution's own FDRs."
        elif old["released_at"]:
            e["renews"] = "That FDR has already been released."
        elif db.q1("SELECT 1 AS x FROM fdrs WHERE renews_fdr_id=?", (renews,)):
            e["renews"] = "That FDR already has a renewal filed."
    if e:
        return render(request, "fdr_new.html", s, status=422, institutions=_insts_for(s), errors=e, v=v, renewable=_renewable(s))
    n = db.q1("SELECT COUNT(*) AS n FROM fdrs")["n"] + 1
    fid = f"FDR-{n:03d}"
    while db.q1("SELECT 1 AS x FROM fdrs WHERE id=?", (fid,)):
        n += 1
        fid = f"FDR-{n:03d}"
    db.x("INSERT INTO fdrs(id,institution_id,fd_number,bank_host,principal,issue_date,maturity,created_at,created_by,renews_fdr_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
         (fid, institution_id, v["fd_number"], bank_host, f"{float(v['principal']):.2f}", issue_date, maturity, time.time(), by, renews or None))
    if renews:
        db.event(auth.actor(s), "renewal_filed", renews, renewed_into=fid, fd_number=v["fd_number"])
    db.event(auth.actor(s), "fdr_registered", fid, fd_number=v["fd_number"], principal=v["principal"], filed_by=by)
    return RedirectResponse(f"/fdrs/{fid}?registered=1", 303)


# ----------------------------------------------------------------- FDR detail + officer actions
@app.get("/fdrs/{fid}", response_class=HTMLResponse)
def fdr_detail(request: Request, fid: str):
    s = auth.require(request)
    b = get_fdr_or_404(s, fid)
    latest = b["evidence"][0] if b["evidence"] else None
    gates = action_gates(b)
    chall = db.q("SELECT * FROM challenges WHERE fdr_id=? ORDER BY issued_at DESC", (fid,))
    events = db.q("SELECT * FROM events WHERE fdr_id=? ORDER BY id DESC LIMIT 40", (fid,))
    jobs_ = db.q("SELECT * FROM jobs WHERE fdr_id=? ORDER BY started_at DESC LIMIT 5", (fid,))
    bank_dep = next((d for c in seed.read_bank_state()["customers"].values() for d in c["deposits"] if d["fd_number"] == b["f"]["fd_number"]), None)
    latest_match = json.loads(latest["match_json"]) if latest else {}
    latest_facts = json.loads(latest["facts_json"]) if latest else {}
    return render(request, "fdr_detail.html", s, b=b, latest=latest, latest_match=latest_match, latest_facts=latest_facts, gates=gates,
                  challenges=chall, events=events, jobs=jobs_, now=time.time(), bank_dep=bank_dep,
                  cvw=views.claim_vs_witness(b["f"], latest_match), nxt=views.next_action(b, gates, auth.officer_id(s) if s["role"] == "officer" else None),
                  officer_mfa=bool(s["role"] == "officer" and db.q1("SELECT 1 AS x FROM officer_mfa WHERE officer=? AND enabled=1", (auth.officer_id(s),))),
                  rc=receipt_for(s, latest, latest_facts) if latest else None,
                  reason=policy.reason_text(latest["reason"], latest_facts) if latest and latest["reason"] else "")


def receipt_for(s, e, facts):
    """AICTE's view for everyone; the hidden text only for the institution that owns the local copy."""
    own = s["role"] == "institution" and db.q1("SELECT institution_id FROM challenges WHERE id=?", (e["challenge_id"],))["institution_id"] == s["inst"]
    return views.receipt(facts, "recv", views.institution_copy(e["challenge_id"]) if own else None)


def action_gates(b):
    """For each officer action: (allowed, reason-if-not)."""
    f, latest, acts = b["f"], (b["evidence"][0] if b["evidence"] else None), b["actions"]
    g = {}
    if f["released_at"]:
        g["challenge"] = (False, "This deposit has been released.")
    elif b.get("successor") and b["status"][0] == "renewed":
        g["challenge"] = (False, f"Renewed into {b['successor']['id']}. Prove the renewed deposit instead.")
    elif challenge_live(b["open"]):
        g["challenge"] = (False, f"A challenge is already open until {when(b['open']['expires_at'])}.")
    else:
        g["challenge"] = (True, "")
    latest_acts = [a for a in acts if latest and a["evidence_id"] == latest["id"]]
    decided = any(a["action"] in ("accept", "escalate") for a in latest_acts)
    if not latest:
        g["accept"] = (False, "No bank-session evidence yet.")
        g["escalate"] = (False, "No bank-session evidence yet.")
    elif decided:
        g["accept"] = g["escalate"] = (False, "The latest evidence already has an officer decision.")
    else:
        ea = b.get("eadvice")
        if latest["decision"] == "verified" or (ea and ea["decision"] == "verified"):
            g["accept"] = (True, "")
        else:
            g["accept"] = (False, "Only verified evidence can be accepted.")
        g["escalate"] = (True, "") if latest["decision"] != "verified" else (False, "Verified evidence does not need escalation.")
    st = b["base_status"][0]
    rel = b.get("release_request")
    if f["released_at"]:
        g["release"] = (False, "Already released.")
    elif b.get("successor"):
        g["release"] = (False, f"Renewed into {b['successor']['id']}: the renewed deposit is the one held now.")
    elif st != "confirmed":
        g["release"] = (False, f"Release needs an accepted bank-session confirmation from the last {DEMO['confirmation_valid_days']} days. Current status: {b['base_status'][1]}.")
    elif b.get("hold"):
        g["release"] = (False, f"On hold: {b['hold']['reason']}. Lift the hold first.")
    elif rel:
        g["release"] = (False, f"A release is already proposed by {rel['proposed_by']} and awaits a second officer.")
    else:
        g["release"] = (True, "")
    # maker-checker: a pending release can be approved only by a different officer, and only while the confirmation is fresh
    if not rel:
        g["approve"] = g["reject"] = (False, "No release has been proposed.")
    else:
        g["reject"] = (True, "")
        if f["released_at"]:
            g["approve"] = (False, "Already released.")
        elif b.get("hold"):
            g["approve"] = (False, f"On hold: {b['hold']['reason']}. Lift the hold first.")
        elif st != "confirmed":
            g["approve"] = (False, f"The bank confirmation is no longer current ({b['base_status'][1]}). Reject, or request a fresh proof.")
        else:
            depts = lifecycle.RULES["release"]["departments"]
            cl = b.get("clearances", {})
            objected = [d for d, c in cl.items() if c["decision"] == "object"]
            missing = [d for d in depts if d not in cl]
            if objected:
                g["approve"] = (False, f"Objection from {', '.join(depts[d] for d in objected)}. Reject the proposal or resolve the objection.")
            elif missing:
                g["approve"] = (False, f"Waiting for clearance from {', '.join(depts[d] for d in missing)}.")
            else:
                g["approve"] = (True, "")
    g["withdraw"] = (True, "") if challenge_live(b["open"]) else (False, "No open challenge.")
    return g


def officer_post(request, csrf):
    s = auth.require(request, "officer")
    auth.check_csrf(s, csrf)
    return s


@app.post("/fdrs/{fid}/challenge")
def new_challenge(request: Request, fid: str, csrf: str = Form("")):
    s = officer_post(request, csrf)
    b = get_fdr_or_404(s, fid)
    ok, why = action_gates(b)["challenge"]
    if not ok:
        raise HTTPException(409, why)
    cid = "CH-" + secrets.token_hex(5).upper()
    token = secrets.token_hex(32)
    now = time.time()
    db.x("INSERT INTO challenges(id,fdr_id,institution_id,token,token_sha256,issued_at,expires_at,state,issued_by) VALUES(?,?,?,?,?,?,?,?,?)",
         (cid, fid, b["f"]["institution_id"], token, hashlib.sha256(token.encode()).hexdigest(), now, now + DEMO["challenge_ttl_seconds"], "issued", "officer"))
    db.event("officer", "challenge_issued", fid, challenge=cid, expires_in_s=DEMO["challenge_ttl_seconds"])
    return RedirectResponse(f"/fdrs/{fid}#proof", 303)


@app.post("/fdrs/{fid}/challenge/withdraw")
def withdraw(request: Request, fid: str, csrf: str = Form("")):
    s = officer_post(request, csrf)
    b = get_fdr_or_404(s, fid)
    if not challenge_live(b["open"]):
        raise HTTPException(409, "No open challenge to withdraw.")
    db.x("UPDATE challenges SET state='withdrawn' WHERE id=? AND state='issued'", (b["open"]["id"],))
    db.event("officer", "challenge_withdrawn", fid, challenge=b["open"]["id"])
    return RedirectResponse(f"/fdrs/{fid}", 303)


@app.post("/fdrs/{fid}/decide")
def decide(request: Request, fid: str, csrf: str = Form(""), action: str = Form(...), evidence_id: int = Form(...), note: str = Form("")):
    s = officer_post(request, csrf)
    b = get_fdr_or_404(s, fid)
    if action not in ("accept", "escalate", "release"):
        raise HTTPException(400, "Unknown action.")
    ok, why = action_gates(b)[action]
    if not ok:
        raise HTTPException(409, why)
    latest = b["evidence"][0] if b["evidence"] else None
    if action != "release" and (not latest or latest["id"] != evidence_id):
        raise HTTPException(409, "Newer evidence arrived. Review it before deciding.")
    note = note.strip()
    if action in ("escalate", "release") and len(note) < 10:
        raise HTTPException(422, "Add a note of at least 10 characters explaining the decision.")
    now = time.time()
    who = auth.officer_id(s)
    if action == "release":
        # Maker step only. Money moves after a second officer approves (see /release/approve).
        db.x("INSERT INTO release_requests(fdr_id,evidence_id,proposed_by,proposed_at,note,state) VALUES(?,?,?,?,?,?)",
             (fid, latest["id"], who, now, note[:500], "pending"))
        db.event(f"officer:{who}", "release_proposed", fid, evidence_id=latest["id"], note=note[:500])
        return RedirectResponse(f"/fdrs/{fid}", 303)
    ea = b.get("eadvice")
    if action == "accept" and latest["decision"] != "verified" and ea:
        note = (note + " " if note else "") + f"[completed by bank-signed e-advice #{ea['row']['id']}: {', '.join(ea['filled'])}]"
    db.x("INSERT INTO actions(fdr_id,evidence_id,action,note,actor,at) VALUES(?,?,?,?,?,?)", (fid, evidence_id, action, note[:500], "officer", now))
    db.event("officer", f"officer_{action}", fid, evidence_id=evidence_id, note=note[:500], officer=who)
    return RedirectResponse(f"/fdrs/{fid}", 303)


FAULTS = set()   # one-shot failure points armed by tests through /internal/fault (loopback + internal key only)


def _fault(point):
    if point in FAULTS:
        FAULTS.discard(point)
        raise RuntimeError(f"injected failure at {point}")


def release_version(b):
    """What the approving officer saw: the case fingerprint plus the signatures that count."""
    return f"{b['release_fp']}:{len(b['approvals'])}"


@app.post("/fdrs/{fid}/release/approve")
def release_approve(request: Request, fid: str, csrf: str = Form(""), note: str = Form(""), otp: str = Form(""), version: str = Form("")):
    """One all-or-nothing transaction: reload the case, re-check every gate, check the version the officer saw,
    record the signature, change state if enough valid signatures exist, sign the instruction, write the audit
    entries. Any failure rolls all of it back."""
    s = officer_post(request, csrf)
    who = auth.officer_id(s)
    if who not in auth.APPROVERS | auth.PROPOSERS:
        raise HTTPException(403, "Only an approving officer can sign a release.")
    step_up(s, otp)
    get_fdr_or_404(s, fid)  # visibility check before taking the write lock

    def work(c):
        b = fdr_bundle(fid)
        ok, why = action_gates(b)["approve"]
        if not ok:
            raise HTTPException(409, why)
        rel, now = b["release_request"], time.time()
        if version and version != release_version(b):
            raise HTTPException(409, "The case changed after you opened it (new evidence, clearance, document, hold or signature). Reload and review again.")
        if who == rel["proposed_by"]:
            raise HTTPException(409, "Maker-checker: the officer who proposed a release cannot approve it. Sign in as another officer.")
        signed_by = [a["officer"] for a in b["approvals"]]
        if who in signed_by:
            raise HTTPException(409, "You have already signed this release. A different officer must add the second signature.")
        c.execute("INSERT INTO release_approvals(release_id,officer,at,fp) VALUES(?,?,?,?)", (rel["id"], who, now, b["release_fp"]))
        _fault("after_signature")
        if len(signed_by) + 1 < b["signers_needed"]:
            db.event_in(c, f"officer:{who}", "release_signed", fid, release=rel["id"], signature=len(signed_by) + 1, of=b["signers_needed"], fingerprint=b["release_fp"])
            return False
        approvers = signed_by + [who]
        letter = f"AICTE/RB/REL/{datetime.utcfromtimestamp(now).year}/{rel['id']:04d}"
        f = b["f"]
        signed = {"type": "aicte-pledge-release/v1", "letter_no": letter, "fdr_id": fid, "fd_number": f["fd_number"],
                  "bank_host": f["bank_host"], "institution_id": f["institution_id"], "principal": f["principal"],
                  "proposed_by": rel["proposed_by"], "approved_by": who, "signed_by": approvers,
                  "clearances": sorted(b["clearances"]), "bank_proof_evidence_id": rel["evidence_id"], "case_fingerprint": b["release_fp"],
                  "approved_at": int(now)}
        signed_json = json.dumps(signed, sort_keys=True, separators=(",", ":"))
        signature = base64.b64encode(AICTE_SIGNING_KEY.sign(signed_json.encode())).decode()
        n = c.execute("UPDATE release_requests SET state='approved', decided_by=?, decided_at=?, decision_note=?, letter_no=?, signed_json=?, signature=? WHERE id=? AND state='pending'",
                      (who, now, note.strip()[:500], letter, signed_json, signature, rel["id"])).rowcount
        if n != 1:
            raise HTTPException(409, "This release was already decided.")
        _fault("after_state")
        c.execute("INSERT INTO actions(fdr_id,evidence_id,action,note,actor,at) VALUES(?,?,?,?,?,?)",
                  (fid, rel["evidence_id"], "release", f"{rel['note']} | instruction issued, signed by {', '.join(approvers)}", f"officer:{who}", now))
        # the deposit is NOT marked released here: that needs the bank's acknowledgement (see /release/complete)
        _fault("before_audit")
        db.event_in(c, f"officer:{who}", "release_instruction_issued", fid, release=rel["id"], proposed_by=rel["proposed_by"], letter=letter,
                    evidence_id=rel["evidence_id"], signed_by=approvers, fingerprint=b["release_fp"])
        _fault("after_audit")
        return True
    issued = db.tx(work)
    return RedirectResponse(f"/fdrs/{fid}?issued=1" if issued else f"/fdrs/{fid}", 303)


@app.post("/internal/fault")
def arm_fault(request: Request, point: str = Form("")):
    internal(request)
    if point not in ("after_signature", "after_state", "before_audit", "after_audit"):
        raise HTTPException(400, "unknown fault point")
    FAULTS.add(point)
    return {"armed": point}


@app.post("/fdrs/{fid}/release/reject")
def release_reject(request: Request, fid: str, csrf: str = Form(""), note: str = Form("")):
    s = officer_post(request, csrf)
    b = get_fdr_or_404(s, fid)
    ok, why = action_gates(b)["reject"]
    if not ok:
        raise HTTPException(409, why)
    note = note.strip()
    if len(note) < 10:
        raise HTTPException(422, "Add a reason of at least 10 characters.")
    rel, who = b["release_request"], auth.officer_id(s)
    state = "withdrawn" if who == rel["proposed_by"] else "rejected"
    db.x("UPDATE release_requests SET state=?, decided_by=?, decided_at=?, decision_note=? WHERE id=? AND state='pending'",
         (state, who, time.time(), note[:500], rel["id"]))
    db.event(f"officer:{who}", f"release_{state}", fid, release=rel["id"], note=note[:500])
    return RedirectResponse(f"/fdrs/{fid}", 303)


@app.get("/fdrs/{fid}/release-letter", response_class=HTMLResponse)
def release_letter(request: Request, fid: str):
    s = auth.require(request)
    b = get_fdr_or_404(s, fid)
    rel = db.q1("SELECT * FROM release_requests WHERE fdr_id=? AND state='approved' ORDER BY id DESC", (fid,))
    if not rel:
        raise HTTPException(404, "No approved release for this FDR.")
    e = db.q1("SELECT * FROM evidence WHERE id=?", (rel["evidence_id"],))
    ev = db.q1("SELECT hash FROM events WHERE kind='release_instruction_issued' AND fdr_id=? ORDER BY id DESC", (fid,))
    qr_url, qr_svg = letter_qr(request, rel) if rel["signed_json"] else (None, None)
    clear = db.q("SELECT * FROM release_clearances WHERE release_id=? ORDER BY at", (rel["id"],))
    signers = db.q("SELECT * FROM release_approvals WHERE release_id=? ORDER BY at", (rel["id"],))
    return render(request, "release_letter.html", s, b=b, f=b["f"], rel=rel, e=e, audit_hash=(ev or {}).get("hash"), qr_url=qr_url, qr_svg=qr_svg,
                  clear=clear, signers=signers, DEPTS=lifecycle.RULES["release"]["departments"])


def _aicte_public_pem():
    from cryptography.hazmat.primitives import serialization
    return AICTE_SIGNING_KEY.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()


@app.get("/api/aicte-release-key")
def aicte_release_key():
    """AICTE's public key for checking signed release letters offline (Ed25519). Public by design."""
    return PlainTextResponse(_aicte_public_pem())


@app.get("/fdrs/{fid}/release-letter.json")
def release_letter_json(request: Request, fid: str):
    """Signed, machine-checkable pledge-release instruction: a bank can verify it offline with AICTE's public key."""
    s = auth.require(request)
    get_fdr_or_404(s, fid)
    rel = db.q1("SELECT * FROM release_requests WHERE fdr_id=? AND state='approved' ORDER BY id DESC", (fid,))
    if not rel or not rel["signed_json"]:
        raise HTTPException(404, "No signed release for this FDR.")
    body = {"payload": json.loads(rel["signed_json"]), "signed_bytes": rel["signed_json"], "signature_b64": rel["signature"],
            "algorithm": "Ed25519", "public_key_pem": _aicte_public_pem()}
    return Response(json.dumps(body, indent=2), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{rel["letter_no"].replace("/", "_")}.json"'})


# ----------------------------------------------------------------- release completion (bank acknowledgement)
@app.post("/fdrs/{fid}/release/complete")
async def release_complete(request: Request, fid: str):
    """The pledge is released only when the bank acknowledges executing THIS signed instruction. A completion
    recorder (not the proposer or a signer) records the acknowledgement, which must name this letter, this FD and
    this amount, be a new document, and arrive while the approval basis is unchanged. All-or-nothing, once."""
    s = auth.require(request, "officer")
    form = await request.form()
    auth.check_csrf(s, form.get("csrf", ""))
    who = auth.officer_id(s)
    if who not in auth.COMPLETERS:
        raise HTTPException(403, "Only the release completion recorder can record the bank's acknowledgement.")
    ack = form.get("ack")
    if ack is None or not getattr(ack, "filename", ""):
        raise HTTPException(422, "Attach the bank's acknowledgement.")
    data = await ack.read(vault.MAX_BYTES + 1)
    try:
        mime = vault.check(data)
    except vault.Refused as e:
        raise HTTPException(422, f"Acknowledgement refused: {e}")
    ref_letter, ref_fd = (form.get("letter_no") or "").strip(), (form.get("fd_number") or "").strip().upper()
    ref_amount, bank_ref = policy.money(form.get("amount") or ""), (form.get("bank_ref") or "").strip()
    if len(bank_ref) < 4:
        raise HTTPException(422, "Enter the bank's own reference number from the acknowledgement.")
    sha = hashlib.sha256(data).hexdigest()

    def work(c):
        b = fdr_bundle(fid)
        if b["f"]["released_at"]:
            raise HTTPException(409, "Already completed: the bank's acknowledgement is on record.")
        rel = b["issued"]
        if not rel:
            raise HTTPException(409, "No signed release instruction has been issued for this FDR.")
        signed = json.loads(rel["signed_json"])
        if who in signed["signed_by"] or who == signed["proposed_by"]:
            raise HTTPException(403, "The officer who proposed or signed the release cannot record its completion.")
        if ref_letter != rel["letter_no"] or ref_fd != b["f"]["fd_number"] or ref_amount != policy.money(b["f"]["principal"]):
            raise HTTPException(422, "The acknowledgement must name this release letter, this FD number and this amount.")
        if c.execute("SELECT 1 FROM documents WHERE sha256=?", (sha,)).fetchone():
            raise HTTPException(422, "This file is already on record (an old receipt or an earlier document cannot be reused as the acknowledgement).")
        cur_fp = release_fingerprint(b["f"], rel, {x["dept"]: x for x in c.execute("SELECT * FROM release_clearances WHERE release_id=?", (rel["id"],)).fetchall()}, b["hold"])
        if b["hold"] or cur_fp != signed["case_fingerprint"]:
            raise HTTPException(409, "The case changed after the instruction was signed (or a hold was placed). It needs fresh review and signatures, not completion.")
        now = time.time()
        stored, wk, nonce, _ = vault.seal(fid, data)
        did = c.execute("INSERT INTO documents(fdr_id,kind,filename,mime,size,sha256,stored_as,wrapped_key,nonce,uploaded_by,uploaded_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (fid, "bank_acknowledgement", re.sub(r"[^\w.\- ]", "_", ack.filename)[:120], mime, len(data), sha, stored, wk, nonce, f"officer:{who}", now)).lastrowid
        if c.execute("UPDATE fdrs SET released_at=? WHERE id=? AND released_at IS NULL", (now, fid)).rowcount != 1:
            raise HTTPException(409, "Already completed.")
        db.event_in(c, f"officer:{who}", "release_completed", fid, release=rel["id"], letter=rel["letter_no"], bank_ref=bank_ref[:60], acknowledgement=did, sha256=sha)
    db.tx(work)
    return RedirectResponse(f"/fdrs/{fid}?released=1", 303)


# ----------------------------------------------------------------- regulatory hold
@app.post("/fdrs/{fid}/hold")
def place_hold(request: Request, fid: str, csrf: str = Form(""), reason: str = Form("")):
    s = officer_post(request, csrf)
    b = get_fdr_or_404(s, fid)
    if b["f"]["released_at"]:
        raise HTTPException(409, "Already released.")
    if b["hold"]:
        raise HTTPException(409, "A hold is already active.")
    reason = reason.strip()
    if len(reason) < 10:
        raise HTTPException(422, "Give a reason of at least 10 characters (e.g. the pending case or grievance).")
    who = auth.officer_id(s)
    db.x("INSERT INTO holds(fdr_id,placed_by,placed_at,reason) VALUES(?,?,?,?)", (fid, who, time.time(), reason[:300]))
    db.event(f"officer:{who}", "hold_placed", fid, reason=reason[:300])
    return RedirectResponse(f"/fdrs/{fid}", 303)


@app.post("/fdrs/{fid}/hold/lift")
def lift_hold(request: Request, fid: str, csrf: str = Form(""), note: str = Form("")):
    s = officer_post(request, csrf)
    b = get_fdr_or_404(s, fid)
    if not b["hold"]:
        raise HTTPException(409, "No active hold.")
    note = note.strip()
    if len(note) < 10:
        raise HTTPException(422, "Record why the hold is lifted (at least 10 characters).")
    who = auth.officer_id(s)
    db.x("UPDATE holds SET lifted_by=?, lifted_at=?, lift_note=? WHERE id=?", (who, time.time(), note[:300], b["hold"]["id"]))
    db.event(f"officer:{who}", "hold_lifted", fid, hold=b["hold"]["id"], note=note[:300])
    return RedirectResponse(f"/fdrs/{fid}", 303)


# ----------------------------------------------------------------- field-level fallback: bank-signed e-advice
def _bank_cert_files(host):
    """The synthetic bank's e-advice signing key (demo only): the same test-CA identity as its TLS server."""
    for b_ in DEMO["banks"].values():
        if b_["host"] == host and b_["cert"].endswith("bank_ok.der"):
            return ROOT / "certs" / "bank_ok.key.pem", ROOT / "certs" / "bank_ok.pem"
    return None


@app.get("/mockbank/eadvice/{fd_number}")
def mockbank_eadvice(request: Request, fd_number: str):
    """SYNTHETIC bank e-advice desk: what the institution would download from its real bank."""
    s = auth.require(request)
    st = seed.read_bank_state()
    for cid, c in st["customers"].items():
        for d in c["deposits"]:
            if d["fd_number"] != fd_number:
                continue
            if s["role"] == "institution" and cid != s["inst"]:
                raise HTTPException(403, "Not your deposit.")
            host = VERIFIER_CFG["allow_hosts"][0]
            files = _bank_cert_files(host)
            p = float(d["principal"])
            lien = f"LOAN {p * 0.85:.2f} AGAINST DEPOSIT" if d["behaviour"] == "lien_loan" else d["lien"]
            status = "CLOSED" if d["behaviour"] == "closed" else "ACTIVE"
            doc = eadvice.issue(host, fd_number, {"status": status, "lien": lien, "holders": d["holders"]},
                                files[0].read_bytes(), files[1].read_bytes())
            db.event(auth.actor(s), "eadvice_issued_by_mock_bank", None, fd_number=fd_number)
            return Response(json.dumps(doc, indent=2), media_type="application/json",
                            headers={"Content-Disposition": f'attachment; filename="e-advice_{fd_number}.json"'})
    raise HTTPException(404, "That deposit does not exist at the mock bank.")


@app.post("/fdrs/{fid}/eadvice")
async def upload_eadvice(request: Request, fid: str, csrf: str = Form(""), file: UploadFile = File(None)):
    s = auth.require(request)
    auth.check_csrf(s, csrf)
    b = get_fdr_or_404(s, fid)
    latest = b["evidence"][0] if b["evidence"] else None
    if not latest or latest["decision"] != "insufficient_evidence":
        raise HTTPException(409, "An e-advice is only used to complete a bank-session proof that ended as Insufficient evidence.")
    if file is None or not file.filename:
        raise HTTPException(422, "Choose the signed e-advice file from your bank.")
    data = await file.read(64 * 1024 + 1)
    if len(data) > 64 * 1024:
        raise HTTPException(422, "That file is too large for an e-advice (limit 64 KB).")
    try:
        doc = json.loads(data)
    except Exception:
        doc = {}
    ca = (ROOT / VERIFIER_CFG["trust"]["ca_der"]).read_bytes()
    r = eadvice.verify(doc, b["f"], ca, VERIFIER_CFG["allow_hosts"])
    db.x("INSERT INTO eadvices(fdr_id,evidence_id,uploaded_at,actor,ok,reason,fields_json,signer,issued_at,sha256) VALUES(?,?,?,?,?,?,?,?,?,?)",
         (fid, latest["id"], time.time(), auth.actor(s), 1 if r["ok"] else 0, r["reason"], json.dumps(r["fields"]), r["signer"], r["issued_at"],
          hashlib.sha256(data).hexdigest()))
    db.event(auth.actor(s), "eadvice_verified" if r["ok"] else "eadvice_rejected", fid, evidence_id=latest["id"], reason=r["reason"],
             fields=sorted(r["fields"]))
    return RedirectResponse(f"/fdrs/{fid}#eadvice", 303)


# ----------------------------------------------------------------- evidence
@app.get("/evidence/{eid}", response_class=HTMLResponse)
def evidence_page(request: Request, eid: int):
    s = auth.require(request)
    e = db.q1("SELECT * FROM evidence WHERE id=?", (eid,))
    if not e:
        raise HTTPException(404, "No such evidence.")
    b = get_fdr_or_404(s, e["fdr_id"])
    facts = json.loads(e["facts_json"])
    match = json.loads(e["match_json"])
    return render(request, "evidence.html", s, e=e, b=b, facts=facts, match=match, metrics=json.loads(e["metrics_json"]),
                  reason=policy.reason_text(e["reason"], facts), ch=db.q1("SELECT * FROM challenges WHERE id=?", (e["challenge_id"],)),
                  rc=receipt_for(s, e, facts), rq=views.receipt(facts, "sent"), cvw=views.claim_vs_witness(b["f"], match))


@app.get("/evidence/{eid}/receipt", response_class=HTMLResponse)
def evidence_receipt(request: Request, eid: int):
    """Fragment used by the live proof page once the verifier has recorded the evidence."""
    s = auth.require(request)
    e = db.q1("SELECT * FROM evidence WHERE id=?", (eid,))
    if not e:
        raise HTTPException(404, "No such evidence.")
    b = get_fdr_or_404(s, e["fdr_id"])
    facts = json.loads(e["facts_json"])
    return T.TemplateResponse(request, "_receipt_fragment.html", {"s": s, "e": e, "rc": receipt_for(s, e, facts), "b": b,
                              "reason": policy.reason_text(e["reason"], facts) if e["reason"] else ""})


@app.get("/evidence/{eid}/export")
def evidence_export(request: Request, eid: int):
    s = auth.require(request)
    e = db.q1("SELECT * FROM evidence WHERE id=?", (eid,))
    if not e:
        raise HTTPException(404, "No such evidence.")
    b = get_fdr_or_404(s, e["fdr_id"])
    c = db.q1("SELECT id, fdr_id, institution_id, issued_at, expires_at, consumed_at FROM challenges WHERE id=?", (e["challenge_id"],))
    out = {"scourge_evidence": 1, "label": "SYNTHETIC DEMO (mock bank, test CA)" if VERIFIER_CFG["trust"]["mode"] == "test_ca" else "public Web PKI",
           "fdr": {k: b["f"][k] for k in ("id", "institution_id", "fd_number", "bank_host", "principal", "maturity")},
           "challenge": c, "evidence_id": e["id"], "received_at": e["received_at"], "decision": e["decision"],
           "verifier_facts": json.loads(e["facts_json"]), "registry_match": json.loads(e["match_json"]), "metrics": json.loads(e["metrics_json"]),
           "not_shown_by_this_proof": ["changes after the session time", "liens or loans the bank page does not display", "correctness of the bank's own ledger"]}
    db.event(auth.actor(s), "evidence_exported", e["fdr_id"], evidence_id=eid)
    return Response(json.dumps(out, indent=2), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="scourge-evidence-{eid}-{b["f"]["id"]}.json"'})


# ----------------------------------------------------------------- institution portal + prover
@app.get("/portal", response_class=HTMLResponse)
def portal(request: Request, notfound: str = ""):
    s = auth.require(request, "institution")
    rows = all_rows(s)
    open_ch = [c for c in db.q("SELECT c.*, f.fd_number FROM challenges c JOIN fdrs f ON f.id=c.fdr_id WHERE c.institution_id=? AND c.state='issued' ORDER BY c.issued_at DESC", (s["inst"],)) if challenge_live(c)]
    recent_jobs = db.q("SELECT j.*, f.fd_number FROM jobs j JOIN fdrs f ON f.id=j.fdr_id WHERE j.institution_id=? ORDER BY j.started_at DESC LIMIT 6", (s["inst"],))
    inst = db.q1("SELECT * FROM institutions WHERE id=?", (s["inst"],))
    adq = lifecycle.adequacy(s["inst"], rows)
    lifecycle.run_daily({s["inst"]: rows})
    notes = db.q("SELECT * FROM notifications WHERE channel='in_app' AND audience='institution' AND institution_id=? ORDER BY id DESC LIMIT 5", (s["inst"],))
    return render(request, "portal.html", s, rows=rows, open_ch=open_ch, recent_jobs=recent_jobs, inst=inst, notfound=notfound, adq=adq, notes=notes)


def own_challenge(s, cid):
    c = db.q1("SELECT * FROM challenges WHERE id=?", (cid,))
    if not c or c["institution_id"] != s["inst"]:
        raise HTTPException(404, "No such challenge for your institution.")
    return c


@app.get("/challenges/{cid}", response_class=HTMLResponse)
def challenge_page(request: Request, cid: str):
    s = auth.require(request, "institution")
    c = own_challenge(s, cid)
    b = get_fdr_or_404(s, c["fdr_id"])
    jobs.reap_orphans()
    running = db.q1("SELECT id FROM jobs WHERE challenge_id=? AND state='running'", (cid,))
    last_job = db.q1("SELECT * FROM jobs WHERE challenge_id=? ORDER BY started_at DESC", (cid,))
    cli = (f"scourge_prove --config scourge.json --verifier <AICTE verifier address> --challenge-id {c['id']} --institution {c['institution_id']} "
           f"--token {c['token']} --fd-number {b['f']['fd_number']} --url 'https://<your bank host>/<deposit details path>' --headers-file headers.txt")
    return render(request, "challenge.html", s, c=c, b=b, live=challenge_live(c), running=running, last_job=last_job, cli=cli, now=time.time())


@app.post("/challenges/{cid}/prove")
def prove(request: Request, cid: str, csrf: str = Form(""), endpoint: str = Form("details"), target: str = Form("genuine"), tamper: str = Form("")):
    s = auth.require(request, "institution")
    auth.check_csrf(s, csrf)
    c = own_challenge(s, cid)
    if not challenge_live(c):
        raise HTTPException(409, "This challenge is no longer open. Ask AICTE for a new one.")
    if endpoint not in ("details", "summary") or target not in ("genuine", "fake", "unlisted") or tamper not in ("", "hide_lien"):
        raise HTTPException(400, "Unsupported proof option.")
    if tamper == "hide_lien" and endpoint != "summary":
        raise HTTPException(400, "Hiding a field only applies to the accounts-summary page.")
    fdr = db.q1("SELECT * FROM fdrs WHERE id=?", (c["fdr_id"],))
    with db._lock:  # check-and-start is atomic, so a double click or two tabs cannot start two provers
        jobs.reap_orphans()
        if db.q1("SELECT id FROM jobs WHERE challenge_id=? AND state='running'", (cid,)):
            raise HTTPException(409, "A proof for this challenge is already running.")
        try:
            jid = jobs.start(c, fdr, {"endpoint": endpoint, "target": target, "tamper": tamper or None}, auth.actor(s))
        except jobs.BusyError as e:
            raise HTTPException(429, str(e))
    return RedirectResponse(f"/jobs/{jid}", 303)


def own_job(s, jid):
    j = db.q1("SELECT * FROM jobs WHERE id=?", (jid,))
    if not j or not (s["role"] == "officer" or j["institution_id"] == s["inst"]):
        raise HTTPException(404, "No such proof run.")
    return j


@app.get("/jobs/{jid}", response_class=HTMLResponse)
def job_page(request: Request, jid: str):
    s = auth.require(request)
    j = own_job(s, jid)
    f = db.q1("SELECT * FROM fdrs WHERE id=?", (j["fdr_id"],))
    return render(request, "job.html", s, j=j, f=f, mode=json.loads(j["mode_json"]))


@app.get("/api/jobs/{jid}")
def job_api(request: Request, jid: str):
    s = auth.require(request)
    j = own_job(s, jid)
    out = {k: j[k] for k in ("id", "state", "started_at", "ended_at", "error", "evidence_id", "challenge_id")}
    out["stages"] = json.loads(j["stages_json"])
    if j["evidence_id"]:
        e = db.q1("SELECT decision, reason, facts_json FROM evidence WHERE id=?", (j["evidence_id"],))
        out["decision"] = e["decision"]
        out["decision_label"] = policy.LABEL[e["decision"]]
        out["reason"] = policy.reason_text(e["reason"], json.loads(e["facts_json"])) if e["reason"] else ""
    return out


@app.post("/jobs/{jid}/cancel")
def job_cancel(request: Request, jid: str, csrf: str = Form("")):
    s = auth.require(request)
    auth.check_csrf(s, csrf)
    j = own_job(s, jid)
    if j["state"] != "running":
        raise HTTPException(409, "This proof run has already finished.")
    jobs.cancel(jid)
    db.event(auth.actor(s), "proof_cancel_requested", j["fdr_id"], job=jid)
    return RedirectResponse(f"/jobs/{jid}", 303)


# ----------------------------------------------------------------- internal API (verifier -> app)
def internal(request: Request):
    key = request.headers.get("x-internal-key", "")
    if request.client.host not in ("127.0.0.1", "::1") or not hmac.compare_digest(key, INTERNAL_KEY):
        raise HTTPException(403, "forbidden")


@app.post("/internal/challenges/consume")
async def consume(request: Request):
    internal(request)
    body = await request.json()
    cid, inst, token = str(body.get("challenge_id", "")), str(body.get("institution_id", "")), str(body.get("token", ""))

    def work(c):
        row = c.execute("SELECT * FROM challenges WHERE id=?", (cid,)).fetchone()
        if not row or not hmac.compare_digest(row["token_sha256"], hashlib.sha256(token.encode()).hexdigest()):
            return 409, {"reason": "challenge_unknown"}
        if row["institution_id"] != inst:
            return 409, {"reason": "challenge_wrong_institution"}
        if row["state"] == "consumed":
            return 409, {"reason": "challenge_already_used"}
        if row["state"] == "withdrawn":
            return 409, {"reason": "challenge_withdrawn"}
        if row["expires_at"] < time.time():
            return 409, {"reason": "challenge_expired"}
        f = c.execute("SELECT * FROM fdrs WHERE id=?", (row["fdr_id"],)).fetchone()
        if f["released_at"]:
            return 409, {"reason": "fdr_released"}
        c.execute("UPDATE challenges SET state='consumed', consumed_at=? WHERE id=? AND state='issued'", (time.time(), cid))
        return 200, {"challenge_id": cid, "fd_number": f["fd_number"], "bank_host": f["bank_host"], "fdr_id": f["id"]}

    code, out = db.tx(work)
    row = db.q1("SELECT fdr_id FROM challenges WHERE id=?", (cid,))
    db.event("verifier", "challenge_consumed" if code == 200 else "challenge_refused", row["fdr_id"] if row else None, challenge=cid, reason=out.get("reason"))
    return JSONResponse(out, status_code=code)


@app.post("/internal/evidence")
async def post_evidence(request: Request):
    internal(request)
    facts = await request.json()
    cid = str(facts.get("challenge_id", ""))
    c = db.q1("SELECT * FROM challenges WHERE id=? AND state='consumed'", (cid,))
    if not c:
        raise HTTPException(409, "unknown or unconsumed challenge")
    if db.q1("SELECT id FROM evidence WHERE challenge_id=?", (cid,)):
        raise HTTPException(409, "evidence already recorded for this challenge")
    fdr = db.q1("SELECT * FROM fdrs WHERE id=?", (c["fdr_id"],))
    decision, match = policy.apply(facts, fdr)
    metrics = facts.pop("metrics", {})
    if decision == "insufficient_evidence" and not facts.get("reason"):
        missing = [k for k, v in match.items() if v["result"] in ("not_exposed", "ambiguous", "unrecognised", "unparseable", "unsupported")]
        facts["reason"] = "required_field_unknown:" + ",".join(missing) if missing else None
    eid = db.x("INSERT INTO evidence(challenge_id,fdr_id,received_at,gate,reason,decision,facts_json,match_json,metrics_json) VALUES(?,?,?,?,?,?,?,?,?)",
               (cid, fdr["id"], time.time(), facts.get("gate", "rejected"), facts.get("reason"), decision, json.dumps(facts), json.dumps(match), json.dumps(metrics)))
    db.event("verifier", "evidence_recorded", fdr["id"], evidence_id=eid, decision=decision, reason=facts.get("reason"))
    return {"evidence_id": eid, "decision": decision}


# ----------------------------------------------------------------- baseline comparison
@app.get("/baseline", response_class=HTMLResponse)
def baseline_page(request: Request, uid: int = 0):
    s = auth.require(request, "officer")
    up = db.q1("SELECT * FROM baseline_uploads WHERE id=?", (uid,)) if uid else None
    b = fdr_bundle(up["fdr_id"]) if up else None
    latest = b["evidence"][0] if b and b["evidence"] else None
    cvw = views.claim_vs_witness(b["f"], json.loads(latest["match_json"]) if latest else {}) if b else None
    return render(request, "baseline.html", s, fdrs=db.q("SELECT id, fd_number FROM fdrs ORDER BY id"), up=up, b=b, error=None,
                  latest=latest, cvw=cvw, history=db.q("SELECT * FROM baseline_uploads ORDER BY id DESC LIMIT 6"))


@app.post("/baseline", response_class=HTMLResponse)
async def baseline_upload(request: Request, csrf: str = Form(""), fdr_id: str = Form(""), file: UploadFile = File(None)):
    s = auth.require(request, "officer")
    auth.check_csrf(s, csrf)

    def fail(msg):
        return render(request, "baseline.html", s, status=422, fdrs=db.q("SELECT id, fd_number FROM fdrs ORDER BY id"), up=None, b=None, error=msg,
                      history=db.q("SELECT * FROM baseline_uploads ORDER BY id DESC LIMIT 6"))

    if not db.q1("SELECT 1 AS x FROM fdrs WHERE id=?", (fdr_id,)):
        return fail("Choose the FDR this document claims to show.")
    if file is None or not file.filename:
        return fail("Choose a file to upload (PNG, JPEG or PDF, up to 2 MB).")
    data = await file.read(UPLOAD_MAX_BYTES + 1)
    if len(data) > UPLOAD_MAX_BYTES:
        return fail("The file is larger than 2 MB.")
    if len(data) == 0:
        return fail("The file is empty.")
    mime = next((m for m, magic in UPLOAD_TYPES.items() if data.startswith(magic)), None)
    if not mime:
        return fail("Unsupported file. Upload a PNG, JPEG or PDF (checked by content, not by name).")
    h = hashlib.sha256(data).hexdigest()
    name = f"{h[:16]}{ {'image/png': '.png', 'image/jpeg': '.jpg', 'application/pdf': '.pdf'}[mime] }"
    (UPLOADS / name).write_bytes(data)
    safe = re.sub(r"[^A-Za-z0-9._ -]", "_", file.filename)[:80]
    uid = db.x("INSERT INTO baseline_uploads(fdr_id,filename,mime,size,sha256,stored_as,uploaded_at,actor) VALUES(?,?,?,?,?,?,?,?)",
               (fdr_id, safe, mime, len(data), h, name, time.time(), "officer"))
    db.event("officer", "baseline_document_uploaded", fdr_id, upload=uid, sha256=h, size=len(data))
    return RedirectResponse(f"/baseline?uid={uid}", 303)


@app.get("/baseline/file/{uid}")
def baseline_file(request: Request, uid: int):
    auth.require(request, "officer")
    up = db.q1("SELECT * FROM baseline_uploads WHERE id=?", (uid,))
    if not up:
        raise HTTPException(404, "No such upload.")
    return Response((UPLOADS / up["stored_as"]).read_bytes(), media_type=up["mime"],
                    headers={"Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'", "X-Content-Type-Options": "nosniff"})


# ----------------------------------------------------------------- mock bank console (SYNTHETIC)
@app.get("/mockbank", response_class=HTMLResponse)
def mockbank_page(request: Request):
    s = auth.require(request)
    st = seed.read_bank_state()
    regs = {r["fd_number"]: r for r in db.q("SELECT f.*, i.name AS inst FROM fdrs f JOIN institutions i ON i.id=f.institution_id")}
    rows = []
    for cid, c in st["customers"].items():
        if s["role"] == "institution" and cid != s["inst"]:
            continue
        for d in c["deposits"]:
            rows.append({"cust": cid, "cust_name": c["name"], "d": d, "reg": regs.get(d["fd_number"])})
    on_bank = {d["fd_number"] for c in st["customers"].values() for d in c["deposits"]}
    missing = [r for r in regs.values() if r["fd_number"] not in on_bank and (s["role"] == "officer" or r["institution_id"] == s["inst"])]
    return render(request, "mockbank.html", s, rows=rows, missing=missing)


@app.post("/mockbank/behaviour")
def mockbank_set(request: Request, csrf: str = Form(""), fd_number: str = Form(...), behaviour: str = Form(...), lien_text: str = Form("")):
    s = auth.require(request)
    auth.check_csrf(s, csrf)
    if behaviour not in seed.BEHAVIOURS:
        raise HTTPException(400, "Unknown bank behaviour.")
    lien_text = " ".join(lien_text.split())
    if behaviour == "custom_lien" and not re.fullmatch(r"[A-Za-z0-9 .,:;()/&@-]{1,80}", lien_text):
        raise HTTPException(422, "Lien wording: 1-80 letters, digits, spaces and . , : ; ( ) / & @ - only.")
    st = seed.read_bank_state()
    for cid, c in st["customers"].items():
        for d in c["deposits"]:
            if d["fd_number"] == fd_number:
                if s["role"] == "institution" and cid != s["inst"]:
                    raise HTTPException(403, "Not your deposit.")
                old = d["behaviour"]
                d["behaviour"] = "active" if behaviour == "custom_lien" else behaviour
                d["lien"] = lien_text if behaviour == "custom_lien" else seed.DEFAULT_LIEN
                d["lien_custom"] = behaviour == "custom_lien"
                seed.write_bank_state(st)
                fid = (db.q1("SELECT id FROM fdrs WHERE fd_number=?", (fd_number,)) or {}).get("id")
                db.event(auth.actor(s), "mock_bank_changed", fid, fd_number=fd_number, behaviour=behaviour, previous=old, lien_text=lien_text if behaviour == "custom_lien" else None)
                return RedirectResponse("/mockbank#" + fd_number, 303)
    raise HTTPException(404, "That deposit does not exist at the mock bank.")


@app.post("/mockbank/open")
def mockbank_open(request: Request, csrf: str = Form(""), fd_number: str = Form(...), bank_principal: str = Form("")):
    s = auth.require(request, "officer")
    auth.check_csrf(s, csrf)
    f = db.q1("SELECT * FROM fdrs WHERE fd_number=?", (fd_number,))
    if not f:
        raise HTTPException(404, "Not in the register.")
    st = seed.read_bank_state()
    cust = st["customers"].get(f["institution_id"])
    if cust is None:
        raise HTTPException(409, "This institution has no account at the mock bank.")
    bp = bank_principal.replace(",", "").replace("₹", "").strip() or f["principal"]
    if not AMT_RE.match(bp) or float(bp) <= 0:
        raise HTTPException(422, "Enter the bank's principal in rupees, e.g. 750000.")
    if any(d["fd_number"] == fd_number for d in cust["deposits"]):
        raise HTTPException(409, "Already exists at the mock bank.")
    cust["deposits"].append({"fd_number": fd_number, "principal": f"{float(bp):.2f}", "maturity": f["maturity"], "behaviour": "active",
                             "holders": f"{cust['name']} / MEMBER SECRETARY AICTE", "lien": "AICTE NEW DELHI (SECURITY DEPOSIT)"})
    seed.write_bank_state(st)
    db.event("officer", "mock_bank_deposit_opened", f["id"], fd_number=fd_number, bank_principal=f"{float(bp):.2f}", register_principal=f["principal"])
    return RedirectResponse("/mockbank#" + fd_number, 303)


# ----------------------------------------------------------------- two-factor (authenticator app) for officers
LOCKOUT_FAILURES, LOCKOUT_SECONDS = 5, 15 * 60
MFA_REQUIRED = os.environ.get("SCOURGE_MFA_REQUIRED") == "1"


def step_up(s, otp):
    """Release approval needs a fresh authenticator code when the officer has 2FA (or 2FA is mandatory)."""
    who = auth.officer_id(s)
    mfa = db.q1("SELECT * FROM officer_mfa WHERE officer=? AND enabled=1", (who,))
    if not mfa:
        if MFA_REQUIRED:
            raise HTTPException(409, "Two-factor sign-in is mandatory for release approval. Set it up under Account first.")
        return
    st = totp.verify(mfa["secret"], otp, mfa["last_step"])
    if st is None:
        db.event(f"officer:{who}", "step_up_failed")
        raise HTTPException(403, "Enter the current 6-digit authenticator code to approve a release (step-up check).")
    db.x("UPDATE officer_mfa SET last_step=? WHERE officer=?", (st, who))


@app.get("/account/mfa", response_class=HTMLResponse)
def mfa_page(request: Request):
    s = auth.require(request, "officer")
    who = auth.officer_id(s)
    row = db.q1("SELECT * FROM officer_mfa WHERE officer=?", (who,))
    if not row:
        db.x("INSERT INTO officer_mfa(officer,secret,enabled,created_at) VALUES(?,?,0,?)", (who, totp.new_secret(), time.time()))
        row = db.q1("SELECT * FROM officer_mfa WHERE officer=?", (who,))
    qr_svg = None
    if not row["enabled"]:
        import qrcode
        import qrcode.image.svg
        buf = io.BytesIO()
        qrcode.make(totp.uri(row["secret"], who), image_factory=qrcode.image.svg.SvgPathImage, box_size=6, border=2).save(buf)
        qr_svg = base64.b64encode(buf.getvalue()).decode()
    return render(request, "mfa.html", s, row=row, who=who, qr_svg=qr_svg, uri=totp.uri(row["secret"], who))


@app.post("/account/mfa/enable")
def mfa_enable(request: Request, csrf: str = Form(""), otp: str = Form("")):
    s = officer_post(request, csrf)
    who = auth.officer_id(s)
    row = db.q1("SELECT * FROM officer_mfa WHERE officer=?", (who,))
    if not row:
        raise HTTPException(409, "Open the set-up page first.")
    st = totp.verify(row["secret"], otp, row["last_step"])
    if st is None:
        raise HTTPException(422, "That code is not valid. Check the app's clock and try the current code.")
    db.x("UPDATE officer_mfa SET enabled=1, last_step=? WHERE officer=?", (st, who))
    db.event(f"officer:{who}", "mfa_enabled")
    return RedirectResponse("/account/mfa?enabled=1", 303)


# ----------------------------------------------------------------- encrypted document vault
@app.post("/fdrs/{fid}/documents")
async def upload_document(request: Request, fid: str, csrf: str = Form(""), kind: str = Form("fdr_receipt"), file: UploadFile = File(None)):
    s = auth.require(request)
    auth.check_csrf(s, csrf)
    b = get_fdr_or_404(s, fid)
    if kind not in vault.KINDS:
        raise HTTPException(400, "Unknown document type.")
    if file is None or not file.filename:
        raise HTTPException(422, "Choose a file.")
    data = await file.read(vault.MAX_BYTES + 1)
    try:
        mime = vault.check(data)
    except vault.Refused as e:
        db.event(auth.actor(s), "document_refused", fid, reason=str(e), filename=file.filename[:120])
        raise HTTPException(422, str(e))
    stored, wk, nonce, sha = vault.seal(fid, data)
    did = db.x("INSERT INTO documents(fdr_id,kind,filename,mime,size,sha256,stored_as,wrapped_key,nonce,uploaded_by,uploaded_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
               (fid, kind, re.sub(r"[^\w.\- ]", "_", file.filename)[:120], mime, len(data), sha, stored, wk, nonce,
                f"officer:{auth.officer_id(s)}" if s["role"] == "officer" else auth.actor(s), time.time()))
    db.event(auth.actor(s), "document_stored", fid, document=did, doc_type=kind, sha256=sha, size=len(data))
    return RedirectResponse(f"/fdrs/{fid}#documents", 303)


@app.get("/documents/{did}")
def download_document(request: Request, did: int, reason: str = ""):
    s = auth.require(request)
    d = db.q1("SELECT * FROM documents WHERE id=?", (did,))
    if not d:
        raise HTTPException(404, "No such document.")
    get_fdr_or_404(s, d["fdr_id"])
    reason = reason.strip()
    if s["role"] == "officer" and not (10 <= len(reason) <= 300):
        raise HTTPException(422, "Officers record a reason (10–300 characters) for every document download.")
    try:
        data = vault.open_doc(d)
    except vault.IntegrityFailure as e:
        db.event("system", "document_integrity_failed", d["fdr_id"], document=did, detail=str(e))
        db.x("INSERT OR IGNORE INTO notifications(fdr_id,institution_id,kind,step,audience,channel,title,body,created_at,as_of,status) "
             "SELECT ?, institution_id, 'document_integrity_failed', ?, 'officer', 'in_app', 'Document integrity failure', ?, ?, ?, 'shown' FROM fdrs WHERE id=?",
             (d["fdr_id"], str(did), f"Document #{did} ({d['filename']}) failed its integrity check and was not served.", time.time(), date.today().isoformat(), d["fdr_id"]))
        raise HTTPException(409, "This document failed its integrity check (it was changed after upload) and has not been served. Officers have been alerted.")
    db.event(auth.actor(s), "document_downloaded", d["fdr_id"], document=did, reason=reason[:300] or None)
    return Response(data, media_type=d["mime"], headers={"Content-Disposition": f'attachment; filename="{d["filename"]}"', "X-Content-Type-Options": "nosniff"})


# ----------------------------------------------------------------- evidence tier 3: bank branch confirmation
BANK_DIR = json.loads((ROOT / "config" / "bank_directory.json").read_text())
BRANCH_TTL = 7 * 86400


def _branch_for(f):
    d = BANK_DIR.get(f["bank_host"])
    if not d or not d["branches"]:
        return None
    # demo: route to the branch for the institution's city if listed, else the first reviewed branch
    city = (db.q1("SELECT city FROM institutions WHERE id=?", (f["institution_id"],)) or {}).get("city", "")
    for b_ in d["branches"]:
        if city and city.lower() in b_["name"].lower():
            return b_
    return d["branches"][0]


@app.post("/fdrs/{fid}/branch/request")
def branch_request(request: Request, fid: str, csrf: str = Form("")):
    """Tier 3: ask the bank branch (from AICTE's reviewed directory, never an institution-supplied address)
    to confirm the deposit through a signed, expiring, single-use link plus a separate one-time code."""
    s = officer_post(request, csrf)
    b = get_fdr_or_404(s, fid)
    f = b["f"]
    if f["released_at"]:
        raise HTTPException(409, "Already released.")
    if db.q1("SELECT 1 AS x FROM branch_requests WHERE fdr_id=? AND state='sent' AND expires_at>?", (fid, time.time())):
        raise HTTPException(409, "A branch confirmation request is already open for this FDR.")
    br = _branch_for(f)
    if not br:
        raise HTTPException(409, "No reviewed branch contact for this bank in AICTE's directory.")
    rid = "BR-" + secrets.token_hex(4).upper()
    token, code = secrets.token_urlsafe(24), f"{secrets.randbelow(10**6):06d}"
    now = time.time()
    db.x("INSERT INTO branch_requests(id,fdr_id,branch_code,contact,token_sha256,code_sha256,created_at,expires_at,state,requested_by) VALUES(?,?,?,?,?,?,?,?,?,?)",
         (rid, fid, br["code"], br["contact"], hashlib.sha256(token.encode()).hexdigest(), hashlib.sha256(code.encode()).hexdigest(),
          now, now + BRANCH_TTL, "sent", auth.officer_id(s)))
    link = f"{str(request.base_url).rstrip('/')}/branch/confirm/{rid}?t={token}"
    # two separate messages, as a real deployment would send by e-mail and SMS (demo: held in the synthetic bank's inbox)
    db.x("INSERT INTO branch_outbox(request_id,to_addr,subject,body,created_at) VALUES(?,?,?,?,?)",
         (rid, br["contact"], f"AICTE request {rid}: confirm fixed deposit {f['fd_number']}",
          f"AICTE asks {br['name']} to confirm deposit {f['fd_number']}. Open this single-use link (valid 7 days): {link}", now))
    db.x("INSERT INTO branch_outbox(request_id,to_addr,subject,body,created_at) VALUES(?,?,?,?,?)",
         (rid, br["contact"] + " (SMS)", f"AICTE one-time code for {rid}", f"One-time code: {code}", now))
    db.event(f"officer:{auth.officer_id(s)}", "branch_confirmation_requested", fid, request=rid, branch=br["code"])
    return RedirectResponse(f"/fdrs/{fid}#branch", 303)


def _open_branch_request(rid, t):
    r = db.q1("SELECT * FROM branch_requests WHERE id=?", (rid,))
    if not r or not t or not hmac.compare_digest(r["token_sha256"], hashlib.sha256(t.encode()).hexdigest()):
        raise HTTPException(404, "This confirmation link is not valid.")
    if r["state"] != "sent":
        raise HTTPException(409, "This confirmation request has already been answered or closed.")
    if r["expires_at"] < time.time():
        raise HTTPException(409, "This confirmation link has expired.")
    if r["attempts"] >= 5:
        raise HTTPException(429, "Too many wrong codes. AICTE must issue a new request.")
    return r


@app.get("/branch/confirm/{rid}", response_class=HTMLResponse)
def branch_confirm_page(request: Request, rid: str, t: str = ""):
    r = _open_branch_request(rid, t)
    f = db.q1("SELECT f.*, i.name AS inst_name FROM fdrs f JOIN institutions i ON i.id=f.institution_id WHERE f.id=?", (r["fdr_id"],))
    br = next((x for x in BANK_DIR.get(f["bank_host"], {}).get("branches", []) if x["code"] == r["branch_code"]), {})
    return render(request, "branch_confirm.html", auth.read(request), r=r, f=f, br=br, tok=t)


@app.post("/branch/confirm/{rid}", response_class=HTMLResponse)
def branch_confirm(request: Request, rid: str, t: str = Form(""), code: str = Form(""), status: str = Form(""),
                   balance: str = Form(""), maturity: str = Form(""), holders: str = Form(""), lien: str = Form(""),
                   officer_name: str = Form(""), lien_choice: str = Form(""), loan_amount: str = Form("")):
    r = _open_branch_request(rid, t)
    if not hmac.compare_digest(r["code_sha256"], hashlib.sha256(code.strip().encode()).hexdigest()):
        db.x("UPDATE branch_requests SET attempts=attempts+1 WHERE id=?", (rid,))
        db.event("bank_branch", "branch_code_rejected", r["fdr_id"], request=rid)
        raise HTTPException(403, "Wrong one-time code.")
    if len(officer_name.strip()) < 3:
        raise HTTPException(422, "Enter the name and designation of the confirming bank officer.")
    f = db.q1("SELECT * FROM fdrs WHERE id=?", (r["fdr_id"],))
    # Structured lien answer (explicit choices, mapped to this bank's canonical wording); free text is kept only for API callers.
    if lien_choice:
        rules = VERIFIER_CFG["banks"].get(f["bank_host"], {}).get("lien_rules") or {}
        if lien_choice == "aicte":
            lien = (rules.get("in_favour_of_aicte") or [""])[0]
        elif lien_choice == "none":
            lien = "NO LIEN"
        elif lien_choice == "loan":
            amt = policy.money(loan_amount)
            if amt is None or amt <= 0:
                raise HTTPException(422, "Enter the loan or overdraft amount in rupees.")
            lien = f"LOAN {amt // 100}.{amt % 100:02d} AGAINST DEPOSIT"
        else:
            raise HTTPException(422, "Unknown lien answer.")
    answers = {"status": status.strip(), "balance": balance.strip(), "maturity": maturity.strip(), "holders": holders.strip(), "lien": lien.strip()}
    fields = {k: ({"state": "proven", "value": v} if v else {"state": "not_exposed"}) for k, v in answers.items()}
    br = next((x for x in BANK_DIR.get(f["bank_host"], {}).get("branches", []) if x["code"] == r["branch_code"]), {})
    facts = {"gate": "facts_ok", "tier": "branch_confirmation", "fields": fields,
             "branch": {"code": br.get("code"), "name": br.get("name"), "contact": br.get("contact"), "officer": officer_name.strip()[:120]},
             "stages": {"tls_proof": "not a TLS proof: answered by the bank branch through AICTE's signed link"}}
    decision, match = policy.apply(facts, f)
    reason = None
    since = time.time() - DEMO["confirmation_valid_days"] * 86400
    for e in db.q("SELECT decision, facts_json FROM evidence WHERE fdr_id=? AND received_at>?", (f["id"], since)):
        if e["decision"] == "discrepancy" and json.loads(e["facts_json"]).get("tier") != "branch_confirmation":
            decision, reason = "discrepancy", "session_proof_outranks"
    now = time.time()
    cid = "CH-" + rid

    def work(c):
        c.execute("INSERT INTO challenges(id,fdr_id,institution_id,token,token_sha256,issued_at,expires_at,state,consumed_at,issued_by) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (cid, f["id"], f["institution_id"], "-", r["token_sha256"], r["created_at"], r["expires_at"], "consumed", now, "branch"))
        eid = c.execute("INSERT INTO evidence(challenge_id,fdr_id,received_at,gate,reason,decision,facts_json,match_json,metrics_json) VALUES(?,?,?,?,?,?,?,?,?)",
                        (cid, f["id"], now, "facts_ok", reason, decision, json.dumps(facts), json.dumps(match), json.dumps({"tier": "branch_confirmation"}))).lastrowid
        c.execute("UPDATE branch_requests SET state='answered', answered_at=?, answer_json=?, evidence_id=? WHERE id=? AND state='sent'",
                  (now, json.dumps(answers), eid, rid))
        return eid
    eid = db.tx(work)
    db.event("bank_branch", "branch_confirmation_answered", f["id"], request=rid, evidence_id=eid, decision=decision, branch=br.get("code"))
    return render(request, "branch_confirm.html", auth.read(request), r=None, done=decision, f=f, br=br, tok="")


@app.get("/mockbank/branch-inbox", response_class=HTMLResponse)
def branch_inbox(request: Request):
    """SYNTHETIC: the bank branch's mailbox. In a real deployment AICTE never sees these messages."""
    s = auth.require(request)
    rows = db.q("SELECT o.*, r.fdr_id, r.state FROM branch_outbox o JOIN branch_requests r ON r.id=o.request_id ORDER BY o.id DESC LIMIT 50")
    if s["role"] == "institution":
        raise HTTPException(403, "Only the (synthetic) bank branch reads this inbox.")
    return render(request, "branch_inbox.html", s, rows=rows)


# ----------------------------------------------------------------- release clearance (multi-department)
@app.post("/fdrs/{fid}/release/clear")
async def release_clear(request: Request, fid: str):
    """A department reviewer records its own department's decision, with a reason and a supporting memo
    (uploaded with the decision, or an earlier memo of THIS case uploaded by the same reviewer)."""
    s = auth.require(request, "officer")
    form = await request.form()
    auth.check_csrf(s, form.get("csrf", ""))
    dept, decision, note = form.get("dept", ""), form.get("decision", "clear"), (form.get("note") or "").strip()
    b = get_fdr_or_404(s, fid)
    rel = b["release_request"]
    depts = lifecycle.RULES["release"]["departments"]
    who = auth.officer_id(s)
    if dept not in depts or decision not in ("clear", "object"):
        raise HTTPException(400, "Unknown department or decision.")
    if auth.OFFICER_DEPT.get(who) != dept:
        db.event(f"officer:{who}", "release_clearance_refused", fid, dept=dept, why="not this department's reviewer")
        raise HTTPException(403, f"Only the {depts[dept]} reviewer can decide for {depts[dept]}.")
    if not rel:
        raise HTTPException(409, "No release has been proposed.")
    if who == rel["proposed_by"]:
        raise HTTPException(403, "The proposer cannot also clear the release.")
    if dept in b["clearances"]:
        raise HTTPException(409, f"{depts[dept]} has already recorded its decision.")
    if len(note) < 10:
        raise HTTPException(422, "Record the reason for the decision (at least 10 characters).")
    memo, doc_id = form.get("memo"), form.get("doc_id", "")
    if memo is not None and getattr(memo, "filename", ""):
        data = await memo.read(vault.MAX_BYTES + 1)
        try:
            mime = vault.check(data)
        except vault.Refused as e:
            raise HTTPException(422, f"Memo refused: {e}")
    elif doc_id:
        d = db.q1("SELECT * FROM documents WHERE id=?", (int(doc_id) if str(doc_id).isdigit() else -1,))
        if not d or d["fdr_id"] != fid or d["kind"] != "clearance_memo" or d["uploaded_by"] != f"officer:{who}":
            raise HTTPException(422, "That memo is not a clearance memo you uploaded for this case.")
        data = None
    else:
        raise HTTPException(422, "Attach the department's memo supporting this decision.")
    now = time.time()

    def work(c):
        nonlocal doc_id
        if data is not None:
            stored, wk, nonce, sha = vault.seal(fid, data)
            doc_id = c.execute("INSERT INTO documents(fdr_id,kind,filename,mime,size,sha256,stored_as,wrapped_key,nonce,uploaded_by,uploaded_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                               (fid, "clearance_memo", re.sub(r"[^\w.\- ]", "_", memo.filename)[:120], mime, len(data), sha, stored, wk, nonce, f"officer:{who}", now)).lastrowid
        if c.execute("SELECT 1 FROM release_clearances WHERE release_id=? AND dept=?", (rel["id"], dept)).fetchone():
            raise HTTPException(409, f"{depts[dept]} has already recorded its decision.")
        c.execute("INSERT INTO release_clearances(release_id,dept,decision,officer,at,note) VALUES(?,?,?,?,?,?)",
                  (rel["id"], dept, decision, who, now, f"{note[:300]} [memo #{doc_id}]"))
        db.event_in(c, f"officer:{who}", f"release_{'cleared' if decision == 'clear' else 'objection'}", fid, release=rel["id"], dept=dept, note=note[:300], memo=int(doc_id))
    db.tx(work)
    return RedirectResponse(f"/fdrs/{fid}#release", 303)


# ----------------------------------------------------------------- public verification of a signed release letter
@app.get("/verify", response_class=HTMLResponse)
def verify_release(request: Request, d: str = "", sig: str = ""):
    """Anyone (e.g. the bank) scans the QR on a release letter; the page checks AICTE's Ed25519 signature."""
    result, payload = None, None
    if d and sig:
        try:
            raw = base64.urlsafe_b64decode(d + "=" * (-len(d) % 4))
            AICTE_SIGNING_KEY.public_key().verify(base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4)), raw)
            payload, result = json.loads(raw), "valid"
            rel = db.q1("SELECT state FROM release_requests WHERE letter_no=?", (payload.get("letter_no"),))
            if not rel:
                result = "valid_unknown"   # signature valid, but this portal has no record (e.g. after a demo reset)
        except Exception:
            result = "invalid"
    return render(request, "verify.html", auth.read(request), result=result, payload=payload)


def letter_qr(request, rel):
    import qrcode
    import qrcode.image.svg
    d = base64.urlsafe_b64encode(rel["signed_json"].encode()).decode().rstrip("=")
    sg = base64.urlsafe_b64encode(base64.b64decode(rel["signature"])).decode().rstrip("=")
    url = f"{str(request.base_url).rstrip('/')}/verify?d={d}&sig={sg}"
    img = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, box_size=6, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return url, base64.b64encode(buf.getvalue()).decode()


# ----------------------------------------------------------------- institutions: deposit adequacy (APH Table 1.4)
def bundles_by_inst(only=None):
    out = {}
    for r in db.q("SELECT id, institution_id FROM fdrs ORDER BY id"):
        if only and r["institution_id"] != only:
            continue
        out.setdefault(r["institution_id"], []).append(fdr_bundle(r["id"]))
    for i in db.q("SELECT id FROM institutions"):
        if not only or i["id"] == only:
            out.setdefault(i["id"], [])
    return out


@app.get("/institutions", response_class=HTMLResponse)
def institutions_page(request: Request):
    s = auth.require(request, "officer")
    bb = bundles_by_inst()
    rows = []
    for inst in db.q("SELECT * FROM institutions ORDER BY id"):
        a = lifecycle.adequacy(inst["id"], bb.get(inst["id"], []))
        gaps = [(b["f"], lifecycle.term_gap(b["f"])) for b in bb.get(inst["id"], []) if b["status"][0] not in ("released", "renewed") and lifecycle.term_gap(b["f"])]
        rows.append({"i": inst, "a": a, "gaps": gaps})
    return render(request, "institutions.html", s, rows=rows, RULES=lifecycle.RULES)


@app.post("/institutions/{iid}/profile")
async def institution_profile(request: Request, iid: str):
    s = auth.require(request, "officer")
    form = await request.form()
    auth.check_csrf(s, form.get("csrf"))
    if not db.q1("SELECT 1 AS x FROM institutions WHERE id=?", (iid,)):
        raise HTTPException(404, "No such institution.")
    progs = []
    for v in form.getlist("prog"):
        prog, _, level = v.partition(":")
        if prog in lifecycle.RULES["programmes"] and lifecycle.RULES["programmes"][prog].get(level) is not None:
            progs.append([prog, level])
    exempt = form.get("exempt") or None
    if exempt and exempt not in lifecycle.RULES["exemptions"]:
        raise HTTPException(400, "Unknown exemption.")
    leased = 1 if form.get("leased") else 0
    db.x("INSERT INTO institution_profile(institution_id,programmes_json,leased,exempt) VALUES(?,?,?,?) "
         "ON CONFLICT(institution_id) DO UPDATE SET programmes_json=excluded.programmes_json, leased=excluded.leased, exempt=excluded.exempt",
         (iid, json.dumps(progs), leased, exempt))
    db.event(auth.actor(s), "institution_profile_updated", None, institution=iid, programmes=progs, leased=leased, exempt=exempt)
    return RedirectResponse(f"/institutions#{iid}", 303)


# ----------------------------------------------------------------- notifications: reminder and escalation ladder
@app.get("/notifications", response_class=HTMLResponse)
def notifications_page(request: Request, audience: str = ""):
    s = auth.require(request)
    if s["role"] == "officer":
        lifecycle.run_daily(bundles_by_inst())
        mailer.deliver_pending()
        rows = db.q("SELECT * FROM notifications WHERE channel='in_app' AND audience!='institution' AND (?='' OR audience=?) ORDER BY id DESC LIMIT 300", (audience, audience))
        outbox = {r["status"]: r["n"] for r in db.q("SELECT status, COUNT(*) AS n FROM notifications WHERE channel='email' GROUP BY status")}
    else:
        lifecycle.run_daily(bundles_by_inst(s["inst"]))
        rows = db.q("SELECT * FROM notifications WHERE channel='in_app' AND audience='institution' AND institution_id=? ORDER BY id DESC LIMIT 300", (s["inst"],))
        outbox = {r["status"]: r["n"] for r in db.q("SELECT status, COUNT(*) AS n FROM notifications WHERE channel='email' AND audience='institution' AND institution_id=? GROUP BY status", (s["inst"],))}
    return render(request, "notifications.html", s, rows=rows, outbox=outbox, audience=audience, LADDER=lifecycle.LADDER, smtp=mailer.configured())


@app.post("/notifications/run")
def notifications_run(request: Request, csrf: str = Form(""), as_of: str = Form("")):
    """Run the daily check now; an officer may run it 'as of' a future date to preview the ladder (demo time machine)."""
    s = officer_post(request, csrf)
    try:
        d = date.fromisoformat(as_of) if as_of else date.today()
    except ValueError:
        raise HTTPException(422, "Enter a valid date.")
    n = lifecycle.run_daily(bundles_by_inst(), d)
    sent = mailer.deliver_pending()
    db.event(auth.actor(s), "daily_check_run", None, as_of=d.isoformat(), added=n, email=sent)
    return RedirectResponse("/notifications?ran=" + str(n), 303)


# ----------------------------------------------------------------- audit + system
@app.get("/audit", response_class=HTMLResponse)
def audit(request: Request, kind: str = ""):
    s = auth.require(request, "officer")
    kinds = [r["kind"] for r in db.q("SELECT DISTINCT kind FROM events ORDER BY kind")]
    rows = db.q("SELECT * FROM events WHERE (?='' OR kind=?) ORDER BY id DESC LIMIT 300", (kind, kind))
    return render(request, "audit.html", s, rows=rows, kinds=kinds, kind=kind, chain=db.verify_chain())


@app.get("/api/audit/verify")
def audit_verify(request: Request):
    """Recompute the hash chain over the whole log. Anyone holding an exported head hash can re-check it later."""
    auth.require(request, "officer")
    return db.verify_chain()


@app.get("/audit.csv")
def audit_csv(request: Request):
    s = auth.require(request, "officer")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "time_utc", "actor", "kind", "fdr_id", "detail", "prev_hash", "hash"])
    for r in db.q("SELECT * FROM events ORDER BY id"):
        w.writerow([r["id"], datetime.utcfromtimestamp(r["at"]).isoformat() + "Z", r["actor"], r["kind"], r["fdr_id"] or "", r["detail_json"], r["prev_hash"] or "", r["hash"] or ""])
    db.event(auth.actor(s), "audit_exported")
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="scourge-audit.csv"'})


# ----------------------------------------------------------------- reports, calendar, reconciliation
@app.get("/reports", response_class=HTMLResponse)
def reports_page(request: Request):
    s = auth.require(request, "officer")
    rows = all_rows(s)
    return render(request, "reports.html", s, k=reports.kpis(rows), cal=reports.calendar(rows), rupees=lifecycle.rupees)


@app.get("/reports/register.{fmt}")
def register_export(request: Request, fmt: str):
    s = auth.require(request, "officer")
    rows = all_rows(s)
    if fmt == "csv":
        body, mt = reports.to_csv(rows), "text/csv"
    elif fmt == "xlsx":
        body, mt = reports.to_xlsx(rows, reports.kpis(rows)), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        raise HTTPException(404, "Unknown format.")
    db.event(auth.actor(s), "register_exported", None, format=fmt, rows=len(rows))
    return Response(body, media_type=mt, headers={"Content-Disposition": f'attachment; filename="scourge-register.{fmt}"'})


@app.get("/reconcile", response_class=HTMLResponse)
def reconcile_page(request: Request):
    s = auth.require(request, "officer")
    return render(request, "reconcile.html", s, findings=None)


@app.post("/reconcile", response_class=HTMLResponse)
async def reconcile_run(request: Request):
    s = auth.require(request, "officer")
    form = await request.form()
    auth.check_csrf(s, form.get("csrf", ""))
    up = form.get("file")
    if not up or not getattr(up, "filename", ""):
        return render(request, "reconcile.html", s, findings=None, error="Choose a bank file first.", status=400)
    data = await up.read(reports.MAX_BYTES + 1)
    try:
        kind, items = reports.parse(up.filename, data)
    except reports.BadFile as e:
        return render(request, "reconcile.html", s, findings=None, error=str(e), status=400)
    findings = reports.reconcile(kind, items, all_rows(s))
    db.event(auth.actor(s), "bank_file_reconciled", None, file=up.filename[:80], file_kind=kind, items=len(items),
             sha256=hashlib.sha256(data).hexdigest(), findings=len(findings))
    return render(request, "reconcile.html", s, findings=findings, kind=kind, n=len(items), fname=up.filename)


@app.get("/system", response_class=HTMLResponse)
def system(request: Request):
    s = auth.require(request)
    return render(request, "system.html", s, cfg=VERIFIER_CFG)


@app.post("/system/service")
def service(request: Request, csrf: str = Form(""), name: str = Form(...), op: str = Form(...)):
    s = auth.require(request, "officer")
    auth.check_csrf(s, csrf)
    if name not in procs.ALL or op not in ("start", "stop"):
        raise HTTPException(400, "Unknown service operation.")
    ok = procs.start(name) if op == "start" else procs.stop(name)
    db.event(auth.actor(s), f"service_{op}", None, service=name, ok=ok)
    if not ok:
        raise HTTPException(500, f"Could not {op} {name}. See var/logs/{name}.log.")
    return RedirectResponse("/system", 303)


@app.post("/system/reset")
def reset(request: Request, csrf: str = Form("")):
    s = auth.require(request, "officer")
    auth.check_csrf(s, csrf)
    jobs.reap_orphans()
    if db.q1("SELECT id FROM jobs WHERE state='running'"):
        raise HTTPException(409, "A proof is running. Wait for it to finish or cancel it, then reset.")
    seed.seed()
    for f in list(UPLOADS.glob("*")) + list(views.COPIES.glob("*")):
        f.unlink()
    vault.wipe()
    procs.start_all()
    return RedirectResponse("/register" if s["role"] == "officer" else "/portal", 303)


@app.get("/api/health")
def health():
    return {"services": procs.health(), "trust_mode": VERIFIER_CFG["trust"]["mode"], "allow_hosts": VERIFIER_CFG["allow_hosts"]}
