#!/usr/bin/env python3
"""Consistent backup and verified restore of a Scourge instance (failure recovery).

  scripts/backup.py create  <dest.tar.gz>   online: SQLite backup API (safe while the app runs) + vault + keys
  scripts/backup.py verify  <backup.tar.gz> integrity_check, audit hash chain, every vault blob decrypts
  scripts/backup.py restore <backup.tar.gz> into an EMPTY SCOURGE_VAR (refuses to overwrite)

Keys (vault master key, session and signing keys) travel inside the archive, so store backups encrypted
and off-host (for example an AICTE-controlled bucket). Nothing here talks to a bank."""
import hashlib
import io
import json
import os
import sqlite3
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
VAR = Path(os.environ.get("SCOURGE_VAR", ROOT / "var"))
KEEP_FILES = ["vault_master.key", "session.key", "internal.key", "aicte_release_signing.key", "bank_state.json"]
KEEP_DIRS = ["vault", "uploads", "institution_copies"]


def create(dest: Path):
    with tempfile.TemporaryDirectory() as td:
        snap = Path(td) / "scourge.db"
        src = sqlite3.connect(VAR / "scourge.db")
        out = sqlite3.connect(snap)
        src.backup(out)                       # consistent snapshot even with writers active (WAL)
        out.close(); src.close()
        manifest = {}
        with tarfile.open(dest, "w:gz") as t:
            def add(p: Path, arc: str):
                manifest[arc] = hashlib.sha256(p.read_bytes()).hexdigest()
                t.add(p, arc)
            add(snap, "scourge.db")
            for f in KEEP_FILES:
                if (VAR / f).exists():
                    add(VAR / f, f)
            for d in KEEP_DIRS:
                for p in sorted((VAR / d).rglob("*")) if (VAR / d).exists() else []:
                    if p.is_file():
                        add(p, str(p.relative_to(VAR)))
            data = json.dumps(manifest, indent=1, sort_keys=True).encode()
            ti = tarfile.TarInfo("MANIFEST.json"); ti.size = len(data)
            t.addfile(ti, io.BytesIO(data))
    print(f"backup written: {dest} ({len(manifest)} files)")


def _extract(src: Path, into: Path):
    with tarfile.open(src, "r:gz") as t:
        t.extractall(into, filter="data")    # refuses absolute paths / traversal
    manifest = json.loads((into / "MANIFEST.json").read_text(encoding="utf-8"))
    for arc, h in manifest.items():
        if hashlib.sha256((into / arc).read_bytes()).hexdigest() != h:
            raise SystemExit(f"checksum mismatch: {arc}")
    return manifest


def _check(var: Path):
    os.environ["SCOURGE_VAR"] = str(var)
    c = sqlite3.connect(var / "scourge.db")
    if c.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise SystemExit("database integrity_check failed")
    c.close()
    from app import db, vault             # imported only now, against the restored directory
    chain = db.verify_chain()
    if not chain["ok"]:
        raise SystemExit(f"audit hash chain broken at entry {chain['broken_at']}: {chain['problem']}")
    bad = 0
    for row in db.q("SELECT * FROM documents"):
        try:
            vault.open_doc(row)
        except Exception:
            bad += 1
    if bad:
        raise SystemExit(f"{bad} vault documents failed to decrypt")
    return db.q1("SELECT COUNT(*) AS n FROM documents")["n"]


def verify(src: Path):
    with tempfile.TemporaryDirectory() as td:
        m = _extract(src, Path(td))
        docs = _check(Path(td))
    print(f"backup OK: {len(m)} files, checksums match, integrity ok, audit chain ok, {docs} vault documents decrypt")


def restore(src: Path):
    if (VAR / "scourge.db").exists():
        raise SystemExit(f"{VAR} already holds a database; restore only into an empty directory")
    VAR.mkdir(parents=True, exist_ok=True)
    _extract(src, VAR)
    (VAR / "MANIFEST.json").unlink()
    for f in KEEP_FILES:
        if (VAR / f).exists() and f.endswith(".key"):
            os.chmod(VAR / f, 0o600)
    docs = _check(VAR)
    print(f"restored into {VAR}: integrity ok, audit chain ok, {docs} vault documents decrypt")


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] not in ("create", "verify", "restore"):
        sys.exit(__doc__)
    {"create": create, "verify": verify, "restore": restore}[sys.argv[1]](Path(sys.argv[2]))
