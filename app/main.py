"""Scourge portal: FastAPI + SQLite + server-rendered pages.
Officer journey: register FDR -> request bank proof -> evidence -> accept / escalate -> release gate.
Institution journey: open challenge -> prove from bank (real MPC-TLS prover, synthetic bank in demo) -> result."""
import csv
import hashlib
import hmac
import io
import json
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

from . import auth, db, jobs, policy, procs, seed, views
from .settings import ACCESS_CODE, COOKIE_SECURE, DEMO, INTERNAL_KEY, UPLOAD_MAX_BYTES, UPLOAD_TYPES, UPLOADS, VERIFIER_CFG

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


T.env.globals.update(inr=inr, when=when, ago=ago, LABEL=policy.LABEL, DEMO=DEMO, BEHAVIOURS=seed.BEHAVIOURS, DEMO_ALLOW=VERIFIER_CFG["allow_hosts"])
T.env.filters["fromjson"] = json.loads


def render(request, name, s=None, status=200, **ctx):
    s = s if s is not None else auth.read(request)
    return T.TemplateResponse(request, name, {"s": s, "path": request.url.path, "health": procs.health(), **ctx}, status_code=status)


def fdr_bundle(fid):
    f = db.q1("SELECT f.*, i.name AS inst_name, i.city AS inst_city FROM fdrs f JOIN institutions i ON i.id=f.institution_id WHERE f.id=?", (fid,))
    if not f:
        return None
    ev = db.q("SELECT * FROM evidence WHERE fdr_id=? ORDER BY received_at DESC", (fid,))
    acts = db.q("SELECT * FROM actions WHERE fdr_id=? ORDER BY at DESC", (fid,))
    openc = db.q1("SELECT * FROM challenges WHERE fdr_id=? AND state='issued' ORDER BY issued_at DESC", (fid,))
    if not challenge_live(openc):
        openc = None
    st = policy.fdr_status(f, ev, acts, openc)
    return {"f": f, "evidence": ev, "actions": acts, "open": openc, "status": st}


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
                  access_required=bool(ACCESS_CODE))


@app.post("/signin")
def signin(request: Request, role: str = Form(...), next: str = Form("/"), access_code: str = Form("")):
    # The code gates entry. Someone already signed in may switch demo roles (the officer's
    # "Switch to INST-42 to answer it" step) without typing it again.
    if ACCESS_CODE and not auth.read(request) and not hmac.compare_digest(access_code.encode(), ACCESS_CODE.encode()):
        time.sleep(0.5)  # slows guessing; the code is shared with judges only
        db.event("anonymous", "signin_refused")
        raise HTTPException(403, "Wrong access code. Ask the team for the judges' code.")
    if role == "officer":
        cookie, dest = auth.make_cookie("officer", None), "/register"
    elif role.startswith("institution:") and db.q1("SELECT 1 AS x FROM institutions WHERE id=?", (role.split(":", 1)[1],)):
        cookie, dest = auth.make_cookie("institution", role.split(":", 1)[1]), "/portal"
    else:
        raise HTTPException(400, "Unknown role.")
    if next and next.startswith("/") and not next.startswith(("//", "/\\")) and next not in ("/", "/signin"):
        dest = next
    r = RedirectResponse(dest, 303)
    r.set_cookie(auth.COOKIE, cookie, httponly=True, samesite="lax", max_age=auth.MAX_AGE, secure=COOKIE_SECURE)
    db.event("officer" if role == "officer" else role, "signed_in")
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
        if st == "released":
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
        if st in ("discrepancy", "escalated"):
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
    return render(request, "register.html", s, rows=rows, monitor=monitor(allr), q=q, fstatus=status, counts=counts, total=len(allr), paper=paper, bank_ok=bank_ok, bank_bad=bank_bad,
                  matched=matched, page=page, pages=pages)


@app.get("/fdrs/new", response_class=HTMLResponse)
def new_fdr_page(request: Request):
    s = auth.require(request)
    v = {"institution_id": s["inst"]} if s["role"] == "institution" else {}
    return render(request, "fdr_new.html", s, institutions=_insts_for(s), errors={}, v=v)


def _insts_for(s):
    if s["role"] == "institution":
        return db.q("SELECT * FROM institutions WHERE id=?", (s["inst"],))
    return db.q("SELECT * FROM institutions")


FD_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{5,24}$")
AMT_RE = re.compile(r"^\d{1,10}(\.\d{1,2})?$")


@app.post("/fdrs/new", response_class=HTMLResponse)
def new_fdr(request: Request, csrf: str = Form(""), institution_id: str = Form(""), fd_number: str = Form(""),
            bank_host: str = Form(""), principal: str = Form(""), issue_date: str = Form(""), maturity: str = Form("")):
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
    if e:
        return render(request, "fdr_new.html", s, status=422, institutions=_insts_for(s), errors=e, v=v)
    n = db.q1("SELECT COUNT(*) AS n FROM fdrs")["n"] + 1
    fid = f"FDR-{n:03d}"
    while db.q1("SELECT 1 AS x FROM fdrs WHERE id=?", (fid,)):
        n += 1
        fid = f"FDR-{n:03d}"
    db.x("INSERT INTO fdrs(id,institution_id,fd_number,bank_host,principal,issue_date,maturity,created_at,created_by) VALUES(?,?,?,?,?,?,?,?,?)",
         (fid, institution_id, v["fd_number"], bank_host, f"{float(v['principal']):.2f}", issue_date, maturity, time.time(), by))
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
                  cvw=views.claim_vs_witness(b["f"], latest_match), nxt=views.next_action(b, gates),
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
        g["accept"] = (True, "") if latest["decision"] == "verified" else (False, "Only verified evidence can be accepted.")
        g["escalate"] = (True, "") if latest["decision"] != "verified" else (False, "Verified evidence does not need escalation.")
    st = b["status"][0]
    if f["released_at"]:
        g["release"] = (False, "Already released.")
    elif st != "confirmed":
        g["release"] = (False, f"Release needs an accepted bank-session confirmation from the last {DEMO['confirmation_valid_days']} days. Current status: {b['status'][1]}.")
    else:
        g["release"] = (True, "")
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
    db.x("INSERT INTO actions(fdr_id,evidence_id,action,note,actor,at) VALUES(?,?,?,?,?,?)", (fid, evidence_id if action != "release" else latest["id"], action, note[:500], "officer", now))
    if action == "release":
        db.x("UPDATE fdrs SET released_at=? WHERE id=?", (now, fid))
    db.event("officer", f"officer_{action}", fid, evidence_id=evidence_id, note=note[:500])
    return RedirectResponse(f"/fdrs/{fid}", 303)


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
    return render(request, "portal.html", s, rows=rows, open_ch=open_ch, recent_jobs=recent_jobs, inst=inst, notfound=notfound)


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
def mockbank_set(request: Request, csrf: str = Form(""), fd_number: str = Form(...), behaviour: str = Form(...)):
    s = auth.require(request)
    auth.check_csrf(s, csrf)
    if behaviour not in seed.BEHAVIOURS:
        raise HTTPException(400, "Unknown bank behaviour.")
    st = seed.read_bank_state()
    for cid, c in st["customers"].items():
        for d in c["deposits"]:
            if d["fd_number"] == fd_number:
                if s["role"] == "institution" and cid != s["inst"]:
                    raise HTTPException(403, "Not your deposit.")
                old, d["behaviour"] = d["behaviour"], behaviour
                seed.write_bank_state(st)
                fid = (db.q1("SELECT id FROM fdrs WHERE fd_number=?", (fd_number,)) or {}).get("id")
                db.event(auth.actor(s), "mock_bank_changed", fid, fd_number=fd_number, behaviour=behaviour, previous=old)
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


# ----------------------------------------------------------------- audit + system
@app.get("/audit", response_class=HTMLResponse)
def audit(request: Request, kind: str = ""):
    s = auth.require(request, "officer")
    kinds = [r["kind"] for r in db.q("SELECT DISTINCT kind FROM events ORDER BY kind")]
    rows = db.q("SELECT * FROM events WHERE (?='' OR kind=?) ORDER BY id DESC LIMIT 300", (kind, kind))
    return render(request, "audit.html", s, rows=rows, kinds=kinds, kind=kind)


@app.get("/audit.csv")
def audit_csv(request: Request):
    s = auth.require(request, "officer")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "time_utc", "actor", "kind", "fdr_id", "detail"])
    for r in db.q("SELECT * FROM events ORDER BY id"):
        w.writerow([r["id"], datetime.utcfromtimestamp(r["at"]).isoformat() + "Z", r["actor"], r["kind"], r["fdr_id"] or "", r["detail_json"]])
    db.event(auth.actor(s), "audit_exported")
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="scourge-audit.csv"'})


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
    procs.start_all()
    return RedirectResponse("/register" if s["role"] == "officer" else "/portal", 303)


@app.get("/api/health")
def health():
    return {"services": procs.health(), "trust_mode": VERIFIER_CFG["trust"]["mode"], "allow_hosts": VERIFIER_CFG["allow_hosts"]}
