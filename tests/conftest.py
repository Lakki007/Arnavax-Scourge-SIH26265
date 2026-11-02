"""One command runs everything: `python -m pytest tests`.

If the portal is not already running, start it (with its verifier and synthetic banks) before the
test modules are collected, and stop it afterwards. This only happens when this platform's prebuilt
binaries can execute (bin/ for Linux x86-64, bin/<os>-<arch>/ for others such as darwin-arm64);
otherwise the server-driven tests skip with a reason, as before.
Opt out with SCOURGE_NO_AUTOSTART=1 (CI starts the server itself)."""
import os
import signal
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL = os.environ.get("SCOURGE_URL", "http://127.0.0.1:8080")
_proc = None


def _up():
    try:
        return urllib.request.urlopen(URL + "/api/health", timeout=2).status == 200
    except OSError:
        return False


def _binaries_runnable():
    sys.path.insert(0, ROOT)
    from app.settings import BIN  # resolves bin/<os>-<arch> when present
    exe = BIN / "scourge_mockbank"
    if not exe.exists():
        return False, f"no {exe}"
    try:
        subprocess.run([str(exe)], capture_output=True, timeout=5)
    except OSError as e:          # e.g. "Exec format error": Linux binary on macOS
        return False, f"{exe.name} cannot run on this platform ({e.strerror}); build it with scripts/build_rust.sh"
    except subprocess.TimeoutExpired:
        pass
    return True, ""


def pytest_configure(config):
    global _proc
    if os.environ.get("SCOURGE_NO_AUTOSTART") == "1" or _up():
        return
    if URL.split("//", 1)[1].split(":")[0] not in ("127.0.0.1", "localhost"):
        return
    ok, why = _binaries_runnable()
    if not ok:
        sys.stderr.write(f"\n[scourge] not starting the portal: {why}. Server-driven tests will skip.\n")
        return
    log = open(os.path.join(ROOT, "var_test_server.log"), "w")
    _proc = subprocess.Popen(["./scripts/run.sh"], cwd=ROOT, stdout=log, stderr=log, start_new_session=True)
    for _ in range(90):
        if _up():
            # the verifier and banks start with the app; give them a moment to listen
            try:
                import json
                h = json.load(urllib.request.urlopen(URL + "/api/health", timeout=3))
                if all(h.get("services", {}).values()):
                    sys.stderr.write("\n[scourge] portal started for the test run (stopped afterwards)\n")
                    return
            except (OSError, ValueError):
                pass
        if _proc.poll() is not None:
            break
        time.sleep(1)
    sys.stderr.write("\n[scourge] portal did not become healthy; see var_test_server.log. Server-driven tests will skip.\n")


def pytest_unconfigure(config):
    if _proc and _proc.poll() is None:
        try:
            os.killpg(_proc.pid, signal.SIGTERM)
            _proc.wait(10)
        except (OSError, subprocess.TimeoutExpired):
            os.killpg(_proc.pid, signal.SIGKILL)


CORE = ("test_acceptance.py", "test_evidence_boundary.py", "test_lifecycle_edges.py", "test_extension_bridge.py", "test_ui_journey.py")


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Say plainly whether the central proof path ran. A run where it was skipped is not evidence for it."""
    def core(reps):
        return [r for r in reps if any(c in r.nodeid for c in CORE)]
    ran = core(terminalreporter.stats.get("passed", []))
    skipped = core(terminalreporter.stats.get("skipped", []))
    w = terminalreporter.write_line
    if skipped and not ran:
        w("")
        w("=" * 78, red=True, bold=True)
        w(f"  CORE PROOF TESTS DID NOT RUN ({len(skipped)} skipped): the portal, verifier or proof binaries", red=True, bold=True)
        w("  are not available on this machine, so this run says nothing about the bank-proof path.", red=True, bold=True)
        w("  Run them with:  scourge test (Windows)  |  docker compose --profile ops run --rm tests", red=True)
        w("                  or build binaries: ./scripts/build_rust.sh, then python3 -m pytest tests", red=True)
        w("=" * 78, red=True, bold=True)
    elif ran:
        w(f"[scourge] core proof path: {len(ran)} tests passed through the real prover, signing verifier and synthetic bank"
          + (f"; {len(skipped)} skipped" if skipped else ""), green=not skipped, yellow=bool(skipped))
