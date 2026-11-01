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


ROTATE_CHECK = """
import base64, sqlite3, sys
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from app import vault
from app.settings import DB_PATH, VAR
c = sqlite3.connect(DB_PATH); c.row_factory = sqlite3.Row
docs = lambda: [dict(r) for r in c.execute('SELECT * FROM documents')]
old = (VAR / 'vault_master.key').read_bytes()
before = [vault.open_doc(d) for d in docs()]
mode = sys.argv[1]
if mode == 'crash':
    # simulate a crash after the database commit but before the key swap: rows are under .next, current is still old
    import os
    new = AESGCM.generate_key(bit_length=256)
    (VAR / 'vault_master.key.next').write_bytes(new)
    with c:
        for d in docs():
            c.execute('UPDATE documents SET wrapped_key=? WHERE id=?', (vault._wrap(vault._unwrap(base64.b64decode(d['wrapped_key'])), new), d['id']))
    assert [vault.open_doc(d) for d in docs()] == before, 'unreadable mid-rotation'
    print('crash-safe')
else:
    n = vault.rotate_master(c)
    assert n == len(before)
    assert [vault.open_doc(d) for d in docs()] == before, 'changed content'
    assert (VAR / 'vault_master.key').read_bytes() != old
    vault.finalize_rotation()
    assert not (VAR / 'vault_master.key.prev').exists()
    # the old key alone can no longer open any wrapped key
    for d in docs():
        w = base64.b64decode(d['wrapped_key'])
        try:
            AESGCM(old).decrypt(w[:12], w[12:], b'scourge-vault-key'); raise SystemExit('old key still works')
        except Exception as e:
            if isinstance(e, SystemExit): raise
    assert [vault.open_doc(d) for d in docs()] == before
    print('rotated', n)
"""


def _seed_docs(var, n=3):
    lines = ["import time", "from app import db, vault", 'db.x("PRAGMA foreign_keys=OFF")']
    for i in range(n):
        lines += [f"data = b'%PDF-1.4 document {i}'", "name, wk, nonce, sha = vault.seal('FDR-001', data)",
                  "db.x(\"INSERT INTO documents(fdr_id,kind,filename,mime,size,sha256,stored_as,wrapped_key,nonce,uploaded_by,uploaded_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)\", ('FDR-001','other','a.pdf','application/pdf',len(data),sha,name,wk,nonce,'test',time.time()))"]
    r = run(["-c", "\n".join(lines)], var)
    assert r.returncode == 0, r.stderr[-1500:]


def test_vault_master_key_rotation(tmp_path):
    _seed_docs(tmp_path)
    r = run(["-c", ROTATE_CHECK, "rotate"], tmp_path)
    assert r.returncode == 0 and "rotated 3" in r.stdout, r.stdout + r.stderr[-1500:]


def test_vault_rotation_is_crash_safe(tmp_path):
    _seed_docs(tmp_path)
    r = run(["-c", ROTATE_CHECK, "crash"], tmp_path)
    assert r.returncode == 0 and "crash-safe" in r.stdout, r.stdout + r.stderr[-1500:]


def test_rotate_script_end_to_end(tmp_path):
    _seed_docs(tmp_path)
    r = run(["scripts/rotate_vault_key.py", "--finalize"], tmp_path)
    assert r.returncode == 0 and "3 documents re-wrapped, all verify; old key deleted" in r.stdout, r.stdout + r.stderr[-1500:]
