"""Failure recovery: online backup, verified restore, and refusal on tampering (no server needed)."""
import os
import subprocess
import sys
import tarfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEED = """
import time
from app import db, vault
db.x("PRAGMA foreign_keys=OFF")
db.event('officer:RB-01', 'test_event', 'FDR-001', n=1)
data = b'%PDF-1.4 test document'
name, wk, nonce, sha = vault.seal('FDR-001', data)
db.x("INSERT INTO documents(fdr_id,kind,filename,mime,size,sha256,stored_as,wrapped_key,nonce,uploaded_by,uploaded_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
     ('FDR-001', 'other', 'a.pdf', 'application/pdf', len(data), sha, name, wk, nonce, 'test', time.time()))
"""


def run(args, var, **kw):
    env = {**os.environ, "SCOURGE_VAR": str(var)}
    return subprocess.run([sys.executable, *args], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120, **kw)


def test_backup_restore_roundtrip_and_tamper(tmp_path):
    live, restored, bak = tmp_path / "live", tmp_path / "restored", tmp_path / "b.tar.gz"
    live.mkdir()
    r = run(["-c", SEED], live)
    assert r.returncode == 0, r.stderr[-1500:]
    r = run(["scripts/backup.py", "create", str(bak)], live)
    assert r.returncode == 0, r.stderr[-1500:]
    r = run(["scripts/backup.py", "verify", str(bak)], live)
    assert r.returncode == 0 and "backup OK" in r.stdout, r.stdout + r.stderr[-1500:]
    r = run(["scripts/backup.py", "restore", str(bak)], restored)
    assert r.returncode == 0 and "audit chain ok" in r.stdout and "1 vault documents decrypt" in r.stdout, r.stdout + r.stderr[-1500:]
    # never overwrites a live instance
    assert run(["scripts/backup.py", "restore", str(bak)], restored).returncode != 0
    # a modified archive is rejected
    bad = tmp_path / "bad.tar.gz"
    with tarfile.open(bak) as src, tarfile.open(bad, "w:gz") as dst:
        for m in src.getmembers():
            f = src.extractfile(m)
            data = f.read() if f else b""
            if m.name == "bank_state.json" or (m.name.startswith("vault/") and m.isfile()):
                data = data[:-1] + bytes([data[-1] ^ 1])
            import io
            m.size = len(data)
            dst.addfile(m, io.BytesIO(data))
    r = run(["scripts/backup.py", "verify", str(bad)], live)
    assert r.returncode != 0 and "mismatch" in (r.stdout + r.stderr)
