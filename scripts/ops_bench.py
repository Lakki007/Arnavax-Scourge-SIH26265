#!/usr/bin/env python3
"""Operations benchmark: latency, traffic, memory, backpressure, outage recovery, crash during a proof,
backup/restore and key rotation, all through the real prover, signing verifier and synthetic bank.

  ./scripts/run.sh &   then   python3 scripts/ops_bench.py [N]      (N sequential proofs, default 20)

Prints one JSON report; docs/OPERATIONS.md records a run with its hardware."""
import json
import os
import platform
import re
import signal
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tests"))
os.environ.setdefault("SCOURGE_NO_AUTOSTART", "1")
import test_acceptance as T  # noqa: E402

B = T.B
N = int(sys.argv[1]) if len(sys.argv) > 1 else 20
report = {"hardware": {"cpu": next((l.split(":", 1)[1].strip() for l in open("/proc/cpuinfo") if l.startswith("model name")), platform.processor()),
                       "cores": os.cpu_count(), "ram_gb": round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30, 1),
                       "os": f"{platform.system()} {platform.release()}", "python": platform.python_version()}}


def db():
    return sqlite3.connect(T.DB)


def health():
    try:
        return T.httpx.get(B + "/api/health", timeout=3).json()
    except Exception:
        return None


# ---- peak memory of each process kind, sampled while the benchmark runs
peaks, sampling = {}, [True]


def sampler():
    while sampling[0]:
        out = subprocess.run(["ps", "-eo", "rss,comm,args"], capture_output=True, text=True).stdout.splitlines()[1:]
        for line in out:
            rss, comm, args = line.split(None, 2) if len(line.split(None, 2)) == 3 else (line.split() + ["", ""])[:3]
            kind = comm if comm.startswith("scourge_") else ("portal" if "uvicorn app.main" in args else None)
            if kind:
                peaks[kind] = max(peaks.get(kind, 0), int(rss) // 1024)
        time.sleep(0.1)


threading.Thread(target=sampler, daemon=True).start()
off = T.S("officer")
off.post("/system/reset")
for n in ("verifier", "genuine", "fake", "unlisted"):
    off.post("/system/service", name=n, op="start")

# ---- 1. sequential proofs: latency and traffic
lat, mb = [], []
for i in range(N):
    t0 = time.time()
    j, cid = T.prove(off, "FDR-001")
    lat.append(time.time() - t0)
    m = json.loads(db().execute("SELECT metrics_json FROM evidence WHERE challenge_id=?", (cid,)).fetchone()[0])
    mb.append(m.get("mpc_bytes_prover_to_verifier", 0) / 1e6)
    assert j["decision"] == "verified", j
lat.sort()
report["sequential"] = {"proofs": N, "end_to_end_s_p50": round(statistics.median(lat), 2), "end_to_end_s_p95": round(lat[int(0.95 * (N - 1))], 2),
                        "prover_to_verifier_MB_median": round(statistics.median(mb), 1),
                        "note": "end to end = officer challenge -> institution proof -> verifier -> signed facts recorded"}

# ---- 2. burst: 6 proofs requested at once (verifier allows 2 concurrent)
fds = []
for k in range(6):
    fd = f"FD-00009977{k:02d}"
    off.post("/fdrs/new", institution_id="INST-42", fd_number=fd, bank_host="netbanking.mockbank.test", principal="500000",
             issue_date="2024-01-01", maturity="2034-01-01")
    fid = db().execute("SELECT id FROM fdrs WHERE fd_number=?", (fd,)).fetchone()[0]
    T.FD[fid] = fd
    off.post("/mockbank/open", fd_number=fd)
    off.post(f"/fdrs/{fid}/challenge")
    fds.append((fid, db().execute("SELECT id FROM challenges WHERE fdr_id=? ORDER BY issued_at DESC", (fid,)).fetchone()[0]))
inst = T.S("institution:INST-42")
codes = {}


def fire(fid, cid):
    codes[cid] = inst.post(f"/challenges/{cid}/prove").status_code


t0 = time.time()
ths = [threading.Thread(target=fire, args=x) for x in fds]
[t.start() for t in ths]
[t.join() for t in ths]
accepted = [c for c, s in codes.items() if s == 303]
refused = [c for c, s in codes.items() if s == 429]
untouched = sum(1 for c in refused if db().execute("SELECT state FROM challenges WHERE id=?", (c,)).fetchone()[0] == "issued")
# the client retries refused ones until everything is verified
pending = set(refused)
retries = 0
while pending and time.time() - t0 < 300:
    time.sleep(1.0)
    for c in list(pending):
        if inst.post(f"/challenges/{c}/prove").status_code == 303:
            pending.discard(c)
        else:
            retries += 1
deadline = time.time() + 300
while time.time() < deadline:
    done = db().execute(f"SELECT count(*) FROM evidence WHERE challenge_id IN ({','.join('?' * len(fds))})", [c for _, c in fds]).fetchone()[0]
    if done == len(fds):
        break
    time.sleep(0.5)
burst_s = time.time() - t0
verified = db().execute(f"SELECT count(*) FROM evidence WHERE decision='verified' AND challenge_id IN ({','.join('?' * len(fds))})", [c for _, c in fds]).fetchone()[0]
report["burst"] = {"requested_at_once": len(fds), "accepted_immediately": len(accepted), "refused_429_busy": len(refused),
                   "refused_challenges_left_unused": untouched, "client_retries": retries, "all_verified": verified == len(fds),
                   "wall_time_s": round(burst_s, 1), "throughput_proofs_per_min": round(len(fds) / burst_s * 60, 1)}

# ---- 3. verifier outage and recovery
off.post("/system/service", name="verifier", op="stop")
off.post("/fdrs/FDR-002/challenge")
cid = db().execute("SELECT id FROM challenges WHERE fdr_id='FDR-002' ORDER BY issued_at DESC").fetchone()[0]
r = inst.post(f"/challenges/{cid}/prove")
jid = r.headers["location"].rsplit("/", 1)[1]
while inst.get(f"/api/jobs/{jid}").json()["state"] == "running":
    time.sleep(0.2)
failed_state = inst.get(f"/api/jobs/{jid}").json()["state"]
still_unused = db().execute("SELECT state FROM challenges WHERE id=?", (cid,)).fetchone()[0] == "issued"
t0 = time.time()
off.post("/system/service", name="verifier", op="start")
restart_s = time.time() - t0
r = inst.post(f"/challenges/{cid}/prove")
jid = r.headers["location"].rsplit("/", 1)[1]
while inst.get(f"/api/jobs/{jid}").json()["state"] == "running":
    time.sleep(0.2)
report["verifier_outage"] = {"proof_while_down": failed_state, "challenge_left_unused": still_unused,
                             "verifier_restart_s": round(restart_s, 2), "retry_same_challenge": inst.get(f"/api/jobs/{jid}").json().get("decision")}

# ---- 4. portal crash in the middle of a proof (slow bank, kill -9 the portal, restart)
T.bank(off, "FDR-004", "slow")
off.post("/fdrs/FDR-004/challenge")
cid = db().execute("SELECT id FROM challenges WHERE fdr_id='FDR-004' ORDER BY issued_at DESC").fetchone()[0]
r = inst.post(f"/challenges/{cid}/prove")
jid = r.headers["location"].rsplit("/", 1)[1]
time.sleep(2)
portal = [int(l.split()[0]) for l in subprocess.run(["ps", "-eo", "pid,args"], capture_output=True, text=True).stdout.splitlines() if "uvicorn app.main" in l]
crash = {"killed_portal": bool(portal)}
if portal:
    os.kill(portal[0], signal.SIGKILL)
    t0 = time.time()
    subprocess.Popen(["./scripts/run.sh"], cwd=ROOT, stdout=open(os.path.join(ROOT, "var_bench_restart.log"), "w"), stderr=subprocess.STDOUT, start_new_session=True)
    while not (health() and all(health()["services"].values())) and time.time() - t0 < 60:
        time.sleep(0.3)
    crash["portal_back_s"] = round(time.time() - t0, 1)
    off = T.S("officer")
    row = db().execute("SELECT state, error FROM jobs WHERE id=?", (jid,)).fetchone()
    crash["interrupted_job_state"] = row[0]
    crash["reset_not_blocked"] = off.post("/system/reset").status_code == 303
    crash["records_survive"] = db().execute("SELECT count(*) FROM fdrs").fetchone()[0] >= 4
report["portal_crash_mid_proof"] = crash

# ---- 5. backup, verify, restore drill and key rotation
T.prove(off, "FDR-001")
off.c.post("/fdrs/FDR-001/documents", data={"csrf": off.csrf, "kind": "fdr_receipt"}, files={"file": ("r.pdf", b"%PDF-1.4\n% receipt\n%%EOF\n", "application/pdf")})
tmp = tempfile.mkdtemp()
env = {**os.environ}
t0 = time.time(); subprocess.run([sys.executable, "scripts/backup.py", "create", f"{tmp}/b.tar.gz"], cwd=ROOT, env=env, check=True, capture_output=True); b_s = time.time() - t0
t0 = time.time(); v = subprocess.run([sys.executable, "scripts/backup.py", "verify", f"{tmp}/b.tar.gz"], cwd=ROOT, env=env, capture_output=True, text=True); v_s = time.time() - t0
t0 = time.time(); r = subprocess.run([sys.executable, "scripts/backup.py", "restore", f"{tmp}/b.tar.gz"], cwd=ROOT, env={**env, "SCOURGE_VAR": f"{tmp}/restored"}, capture_output=True, text=True); r_s = time.time() - t0
t0 = time.time(); k = subprocess.run([sys.executable, "scripts/rotate_vault_key.py"], cwd=ROOT, env=env, capture_output=True, text=True); k_s = time.time() - t0
a = subprocess.run([sys.executable, "scripts/audit_evidence.py"], cwd=ROOT, env=env, capture_output=True, text=True)
report["recovery"] = {"backup_s": round(b_s, 2), "backup_MB": round(os.path.getsize(f"{tmp}/b.tar.gz") / 1e6, 2), "verify_ok": v.returncode == 0, "verify_s": round(v_s, 2),
                      "restore_ok": r.returncode == 0, "restore_s": round(r_s, 2), "key_rotation_ok": k.returncode == 0, "key_rotation_s": round(k_s, 2),
                      "evidence_signatures_verify": a.returncode == 0, "audit_line": a.stdout.strip().splitlines()[-1] if a.stdout else a.stderr[-200:]}
sampling[0] = False
report["peak_memory_MB"] = peaks
print(json.dumps(report, indent=1))
