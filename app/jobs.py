"""Demo prover runner. Runs the REAL scourge_prove binary (MPC-TLS with the separate verifier)
against a SYNTHETIC bank, streaming its actual stage lines. In production the prover runs on
the institution's own machine against its real net-banking session."""
import json
import os
import secrets
import subprocess
import threading
import time

from . import db
from .settings import BANK_STATE, BIN, DEMO, JOBTMP, ROOT, VERIFIER_ADDR, VERIFIER_CFG
from .views import copy_path

_slots = threading.BoundedSemaphore(DEMO["max_concurrent_proofs"])
_running: dict[str, subprocess.Popen] = {}
_cancel: set[str] = set()

STAGE_TEXT = {
    "started": "Prover started",
    "verifier_connect": "Connecting to AICTE's verifier",
    "challenge_accepted": "Verifier accepted and consumed the challenge",
    "bank_connect": "Connecting to the bank",
    "mpc_tls": "Joint TLS session with the bank (keys shared with the verifier)",
    "response": "Bank response received",
    "select": "Selecting the deposit record to disclose",
    "prove": "Sending the selective-disclosure proof",
    "proved": "Proof accepted by the verifier's TLS check",
    "submitted": "Verifier is evaluating the facts",
    "aborted": "Prover stopped",
}

UNLISTED_HOST = DEMO["banks"]["unlisted"]["host"]


def _prover_config(target):
    if target != "unlisted":
        return ROOT / "config" / "scourge.json"
    p = JOBTMP / "prover_config_unlisted.json"
    if not p.exists():
        c = json.loads((ROOT / "config" / "scourge.json").read_text(encoding="utf-8"))
        c["banks"][UNLISTED_HOST] = dict(c["banks"]["netbanking.mockbank.test"], name="Not approved by AICTE")
        p.write_text(json.dumps(c), encoding="utf-8")
    return p


def _session_for(inst):
    st = json.loads(BANK_STATE.read_text(encoding="utf-8"))
    for k, v in st["sessions"].items():
        if v == inst:
            return k
    raise RuntimeError("no bank session for institution")


def _append_stage(job_id, line):
    with db._lock:
        row = db.q1("SELECT stages_json FROM jobs WHERE id=?", (job_id,))
        stages = json.loads(row["stages_json"])
        stages.append(line)
        db.x("UPDATE jobs SET stages_json=? WHERE id=?", (json.dumps(stages), job_id))


def start(challenge, fdr, mode, actor):
    if not _slots.acquire(blocking=False):
        raise BusyError(f"{DEMO['max_concurrent_proofs']} proofs are already running; try again in a few seconds")
    job_id = "JOB-" + secrets.token_hex(6)
    _starting.add(job_id)
    db.x("INSERT INTO jobs(id,challenge_id,fdr_id,institution_id,mode_json,state,started_at) VALUES(?,?,?,?,?,?,?)",
         (job_id, challenge["id"], fdr["id"], fdr["institution_id"], json.dumps(mode), "running", time.time()))
    db.event(actor, "proof_started", fdr["id"], job=job_id, challenge=challenge["id"], mode=mode)
    threading.Thread(target=_run, args=(job_id, challenge, fdr, mode, actor), daemon=True).start()
    return job_id


def reap_orphans():
    """A job marked running that no prover in THIS process owns was interrupted (app restart or crash).
    Mark it failed instead of leaving it running forever, which would block reset and retries."""
    with db._lock:
        for j in db.q("SELECT id, fdr_id FROM jobs WHERE state='running'"):
            if j["id"] not in _running and j["id"] not in _starting:
                db.x("UPDATE jobs SET state='failed', ended_at=?, error=? WHERE id=? AND state='running'",
                     (time.time(), "Interrupted: the app restarted during this proof. If the challenge was already used, request a new one.", j["id"]))
                db.event("system", "proof_interrupted", j["fdr_id"], job=j["id"])


_starting: set[str] = set()


class BusyError(Exception):
    pass


def cancel(job_id):
    _cancel.add(job_id)
    p = _running.get(job_id)
    if p and p.poll() is None:
        p.terminate()
        return True
    return False


def _run(job_id, challenge, fdr, mode, actor):
    hdr = JOBTMP / f"{job_id}.headers"
    try:
        target = mode["target"]
        bank = DEMO["banks"][target]
        host = bank["host"]
        path = "/api/accounts/summary" if mode["endpoint"] == "summary" else f"/api/fd/details?fd={fdr['fd_number']}"
        fd = os.open(hdr, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(f"Cookie: NB_SESSION={_session_for(fdr['institution_id'])}\nUser-Agent: scourge-demo-prover\n")
        args = [str(BIN / "scourge_prove"), "--config", str(_prover_config(target)), "--verifier", VERIFIER_ADDR,
                "--challenge-id", challenge["id"], "--institution", challenge["institution_id"], "--token", challenge["token"],
                "--fd-number", fdr["fd_number"], "--url", f"https://{host}{path}", "--headers-file", str(hdr),
                "--connect-addr", f"127.0.0.1:{bank['port']}", "--max-sent", "2048", "--max-recv", "16384",
                # the honest demo prover trusts the synthetic CA, as a real one trusts public roots
                "--extra-ca-der", str(ROOT / "certs" / "test_ca.der"),
                # institution-side local copy of the response (what its browser saw); never sent to the verifier
                "--save-response", str(copy_path(challenge["id"]))]
        if target == "fake":
            args += ["--extra-ca-der", str(ROOT / "certs" / "attacker_ca.der")]
        if mode.get("tamper") == "hide_lien":
            args += ["--malicious-hide", "lien"]
        p = subprocess.Popen(args, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        _running[job_id] = p
        _starting.discard(job_id)
        accepted = False
        deadline = time.time() + DEMO["prover_timeout_seconds"]
        timer = threading.Timer(DEMO["prover_timeout_seconds"], lambda: p.poll() is None and p.kill())
        timer.start()
        last_abort = None
        for raw in p.stdout:
            raw = raw.strip()
            try:
                line = json.loads(raw)
            except json.JSONDecodeError:
                continue  # prover only prints JSON stage lines; anything else is ignored
            line["text"] = STAGE_TEXT.get(line.get("stage"), line.get("stage"))
            if line.get("stage") == "challenge_accepted":
                accepted = True
            if line.get("stage") == "aborted":
                last_abort = str(line.get("detail"))
            _append_stage(job_id, line)
        p.wait()
        timer.cancel()
        _running.pop(job_id, None)
        cancelled = job_id in _cancel
        timed_out = time.time() >= deadline
        if not accepted:
            # The verifier never consumed the challenge, so it can be retried.
            msg = "Cancelled before the verifier accepted the challenge." if cancelled else (last_abort or "prover failed")
            if "verifier unreachable" in msg:
                msg = "AICTE's verifier is unreachable. The challenge was not used; start the verifier and retry."
            _finish(job_id, "cancelled" if cancelled else "failed", msg, None, actor, fdr)
            return
        ev = None
        for _ in range(60):  # the verifier posts evidence after the session ends
            ev = db.q1("SELECT id, decision FROM evidence WHERE challenge_id=?", (challenge["id"],))
            if ev:
                break
            time.sleep(0.25)
        if cancelled:
            _finish(job_id, "cancelled", "Cancelled during the proof. The challenge was already consumed; request a new one.", ev and ev["id"], actor, fdr)
        elif timed_out:
            _finish(job_id, "failed", f"Timed out after {DEMO['prover_timeout_seconds']} s.", ev and ev["id"], actor, fdr)
        elif ev:
            _finish(job_id, "done", ("Your prover stopped: " + last_abort) if last_abort else None, ev["id"], actor, fdr)
        else:
            _finish(job_id, "failed", "The verifier did not report a result.", None, actor, fdr)
    except Exception as e:  # never invent a result
        _finish(job_id, "failed", f"{type(e).__name__}: {e}", None, actor, fdr)
    finally:
        try:
            hdr.unlink()
        except FileNotFoundError:
            pass
        _cancel.discard(job_id)
        _starting.discard(job_id)
        _slots.release()


def _finish(job_id, state, error, evidence_id, actor, fdr):
    db.x("UPDATE jobs SET state=?, ended_at=?, error=?, evidence_id=? WHERE id=?", (state, time.time(), error, evidence_id, job_id))
    db.event(actor, f"proof_{state}", fdr["id"], job=job_id, evidence_id=evidence_id, error=error)
