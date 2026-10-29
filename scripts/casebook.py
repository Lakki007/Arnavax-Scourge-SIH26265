"""Fresh, randomised case book: Scourge vs the upload-and-approve baseline on deposits neither was built on.
Ground truth is fixed by construction (what the SYNTHETIC bank holds). Real prover + verifier, real baseline upload.
Run with the app up:  python3 scripts/casebook.py [seed] [n]"""
import json, random, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from test_acceptance import S, db, FD  # noqa: E402

import os  # noqa: E402
VARDIR = Path(os.environ.get("SCOURGE_VAR", ROOT / "var"))
STATE = VARDIR / "bank_state.json"
SAMPLE = ROOT / "samples" / "edited_receipt_FD-0000777777.png"
GOOD_FMTS = [lambda p: f"{p:.2f}", lambda p: "Rs. " + ind(p), lambda p: "₹" + ind(p), lambda p: "INR " + ind(p), lambda p: (ind(p, False) + "/-") if p == int(p) else ind(p)]
DATE_FMTS = [lambda d: d, lambda d: f"{d[8:]}-{d[5:7]}-{d[:4]}", lambda d: f"{d[8:]}/{d[5:7]}/{d[:4]}",
             lambda d: f"{d[8:]}-{['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'][int(d[5:7])-1]}-{d[:4]}"]
# truth, share, how the bank/prover is set up
KINDS = [("honest", 30), ("honest_unreadable", 5), ("forged", 10), ("closed", 10), ("reduced", 10), ("lien_loan", 10),
         ("maturity_changed", 5), ("fake_bank", 10), ("hide_lien", 10)]


def ind(p, paise=True):
    w = f"{int(p)}"
    w = (w[:-3] and __import__("re").sub(r"(\d)(?=(\d\d)+$)", r"\1,", w[:-3]) + ",") + w[-3:]
    return w + (f".{round(p * 100) % 100:02d}" if paise else "")


def patch(fd, **over):
    st = json.loads(STATE.read_text())
    for c in st["customers"].values():
        for d in c["deposits"]:
            if d["fd_number"] == fd:
                d.update(over)
    STATE.write_text(json.dumps(st))


def run_proof(o, fid, inst, **mode):
    assert o.post(f"/fdrs/{fid}/challenge").status_code == 303
    cid = db().execute("SELECT id FROM challenges WHERE fdr_id=? ORDER BY issued_at DESC", (fid,)).fetchone()[0]
    i = S(f"institution:{inst}")
    jid = i.post(f"/challenges/{cid}/prove", **mode).headers["location"].rsplit("/", 1)[1]
    for _ in range(400):
        j = i.get(f"/api/jobs/{jid}").json()
        if j["state"] != "running":
            return j.get("decision") or f"job_{j['state']}", j
        time.sleep(0.2)
    return "timeout", {}


def main(seed=2026, n=40):
    rnd = random.Random(seed)
    o = S("officer")
    assert o.post("/system/reset").status_code == 303
    pool = [k for k, w in KINDS for _ in range(w)]
    out = []
    for k in range(n):
        kind = rnd.choice(pool)
        inst = rnd.choice(["INST-42", "INST-77"])
        p = rnd.randrange(2, 400) * 25000 + rnd.choice([0, 0, 0.5, 0.25])
        y = rnd.randrange(2027, 2036)
        mat = f"{y}-{rnd.randrange(1, 13):02d}-{rnd.randrange(1, 29):02d}"
        fdn = f"FD-{rnd.randrange(10**9, 10**10)}"
        r = o.post("/fdrs/new", institution_id=inst, fd_number=fdn, bank_host="netbanking.mockbank.test",
                   principal=f"{p:.2f}", issue_date="2025-01-01", maturity=mat)
        fid = r.headers["location"].split("/")[2].split("?")[0]
        # baseline: an edited receipt for this deposit is uploaded and "approved" (it can only look at the image)
        rb = o.c.post("/baseline", data={"csrf": o.csrf, "fdr_id": fid}, files={"file": ("receipt.png", SAMPLE.read_bytes(), "image/png")})
        baseline = "approved" if rb.status_code == 303 else f"error {rb.status_code}"
        mode = {}
        if kind != "forged":
            assert o.post("/mockbank/open", fd_number=fdn).status_code == 303
            plain = kind in ("reduced", "lien_loan")  # the mock computes these from a plain number
            patch(fdn, principal=f"{p:.2f}" if plain else rnd.choice(GOOD_FMTS)(p), maturity=rnd.choice(DATE_FMTS)(mat))
            if kind == "honest_unreadable":
                patch(fdn, principal=f"{p / 100000:g} Lakh")
            beh = {"closed": "closed", "reduced": "reduced", "lien_loan": "lien_loan", "maturity_changed": "maturity_changed",
                   "hide_lien": "lien_loan"}.get(kind, "active")
            patch(fdn, behaviour=beh)
            if kind == "fake_bank":
                mode = {"target": "fake"}
            if kind == "hide_lien":
                mode = {"endpoint": "summary", "tamper": "hide_lien"}
        dec, j = run_proof(o, fid, inst, **mode)
        bad = kind not in ("honest", "honest_unreadable")
        out.append({"i": k, "kind": kind, "bad": bad, "baseline": baseline, "scourge": dec, "fd": fdn})
        print(f"{k:3d} {kind:18s} baseline={baseline:9s} scourge={dec}", flush=True)
    (VARDIR / f"casebook_{seed}.json").write_text(json.dumps(out, indent=1))
    return out


if __name__ == "__main__":
    a = [int(x) for x in sys.argv[1:]]
    main(*a)
