"""Core smoke test: drives the real journey through HTTP (no UI templates needed)."""
import base64, json, sys, time, httpx
B = "http://127.0.0.1:8080"
def session(role):
    c = httpx.Client(base_url=B, follow_redirects=False, timeout=30)
    r = c.post("/signin", data={"role": role, "next": "/"})
    raw = c.cookies["scourge_session"]; data = json.loads(base64.urlsafe_b64decode(raw.rsplit(".",1)[0]))
    return c, data["csrf"]
off, oc = session("officer"); inst, ic = session("institution:INST-42")
def run(fdr, behaviour="active", **mode):
    r = off.post("/mockbank/behaviour", data={"csrf": oc, "fd_number": {"FDR-001":"FD-0000123456","FDR-002":"FD-0000777777"}[fdr], "behaviour": behaviour}); assert r.status_code==303, r.text
    r = off.post(f"/fdrs/{fdr}/challenge", data={"csrf": oc}); assert r.status_code==303, (r.status_code, r.text[:300])
    import sqlite3; db=sqlite3.connect("var/scourge.db"); cid=db.execute("select id from challenges where fdr_id=? order by issued_at desc",(fdr,)).fetchone()[0]
    r = inst.post(f"/challenges/{cid}/prove", data={"csrf": ic, **mode}); assert r.status_code==303, (r.status_code, r.text[:300])
    jid = r.headers["location"].split("/")[-1]
    while True:
        j = inst.get(f"/api/jobs/{jid}").json()
        if j["state"] != "running": break
        time.sleep(0.3)
    print(f"{fdr} {behaviour:16s} {json.dumps(mode):55s} -> {j['state']:9s} {j.get('decision','-'):22s} {j.get('reason') or j.get('error') or ''}")
    print("   stages:", [s['stage'] for s in j['stages']])
    return j
run("FDR-001")
run("FDR-002", "closed")
run("FDR-001", "lien_loan", endpoint="summary")
run("FDR-002", "active", target="fake")
