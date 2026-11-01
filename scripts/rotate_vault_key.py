#!/usr/bin/env python3
"""Rotate the document vault's master key (crash-safe), then verify every document.

  scripts/rotate_vault_key.py            rotate, verify every document decrypts, keep the old key as .prev
  scripts/rotate_vault_key.py --finalize also delete the old key after a successful verify

Safe while the portal runs: a document is readable under the current, in-progress (.next) or previous
(.prev) key at every moment. Production would hold the master key in a KMS/HSM; the procedure is the same."""
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import vault  # noqa: E402
from app.settings import DB_PATH  # noqa: E402

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
n = vault.rotate_master(conn)
bad = 0
for row in conn.execute("SELECT * FROM documents"):
    try:
        vault.open_doc(dict(row))
    except Exception:
        bad += 1
if bad:
    sys.exit(f"rotated {n} documents but {bad} failed to decrypt; old key kept as vault_master.key.prev")
if "--finalize" in sys.argv:
    vault.finalize_rotation()
print(f"rotated the vault master key: {n} documents re-wrapped, all verify" + ("; old key deleted" if "--finalize" in sys.argv else "; old key kept as .prev until --finalize"))
