"""Supervises the verifier and the synthetic banks as separate OS processes
(the verifier is never in-process with the app). Start/stop is exposed so judges can
exercise a failed dependency deliberately."""
import os
import socket
import subprocess
import threading
import time

from .settings import BANK_STATE, BIN, DEMO, INTERNAL_KEY, LOGS, ROOT, VERIFIER_ADDR, VERIFIER_KEY_FILE, VERIFIER_MODE

_procs: dict[str, subprocess.Popen] = {}
_lock = threading.Lock()


def _cmd(name):
    if name == "verifier":
        return [str(BIN / "scourge_verifierd"), str(ROOT / "config" / "scourge.json")]
    b = DEMO["banks"][name]
    return [str(BIN / "scourge_mockbank"), str(b["port"]), str(ROOT / b["cert"]), str(ROOT / b["key"]), str(BANK_STATE)]


def addr(name):
    if name == "verifier":
        h, p = VERIFIER_ADDR.rsplit(":", 1)
        return h, int(p)
    return "127.0.0.1", DEMO["banks"][name]["port"]


def port_open(name, timeout=0.3):
    try:
        with socket.create_connection(addr(name), timeout=timeout):
            return True
    except OSError:
        return False


def external(name):
    return name == "verifier" and VERIFIER_MODE == "external"


def start(name):
    if external(name):
        return port_open(name, timeout=2)   # run by AICTE on its own host; never started from here
    with _lock:
        p = _procs.get(name)
        if p and p.poll() is None:
            return True
        env = dict(os.environ, SCOURGE_INTERNAL_KEY=INTERNAL_KEY, SCOURGE_VERIFIER_KEY=str(VERIFIER_KEY_FILE))
        log = open(LOGS / f"{name}.log", "a", encoding="utf-8")
        _procs[name] = subprocess.Popen(_cmd(name), cwd=ROOT, env=env, stdout=log, stderr=log, start_new_session=True)
    for _ in range(40):
        if port_open(name):
            return True
        if _procs[name].poll() is not None:
            return False
        time.sleep(0.1)
    return port_open(name)


def stop(name):
    if external(name):
        return False                         # an independent verifier cannot be stopped by the portal
    with _lock:
        p = _procs.pop(name, None)
    if p and p.poll() is None:
        p.terminate()
        try:
            p.wait(5)
        except subprocess.TimeoutExpired:
            p.kill()
    for _ in range(30):
        if not port_open(name):
            return True
        time.sleep(0.1)
    return not port_open(name)


def running(name):
    if external(name):
        return port_open(name, timeout=1)
    p = _procs.get(name)
    return bool(p and p.poll() is None and port_open(name))


ALL = ["verifier", "genuine", "fake", "unlisted"]


def start_all():
    return {n: start(n) for n in ALL}


def stop_all():
    for n in ALL:
        stop(n)


def health():
    return {n: running(n) for n in ALL}
