"""Encrypted document vault, one record per uploaded file.

* Each file is encrypted with its own random 256-bit key (AES-256-GCM); that key is wrapped with the
  vault master key (AES-256-GCM). Associated data binds the ciphertext to its FDR and plaintext hash.
* The SHA-256 of the plaintext is stored at upload and re-checked on every download; any change to
  the stored file (or its key) is refused and reported as an integrity failure.
* Uploads are checked by content: PNG, JPEG or PDF only; PDFs with JavaScript, launch actions,
  embedded files, rich media or encryption are refused.
DEMO: the master key lives in var/ (mode 0600); production would keep it in a key-management service."""
import base64
import hashlib
import os
import secrets
import time
import uuid

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .settings import VAR

VAULT = VAR / "vault"
VAULT.mkdir(parents=True, exist_ok=True)
MAX_BYTES = 2 * 1024 * 1024
KINDS = {"fdr_receipt": "FDR receipt", "bank_letter": "Bank letter / e-advice", "renewal": "Renewal advice",
         "clearance_memo": "Department clearance memo", "bank_acknowledgement": "Bank acknowledgement of release",
         "closure_order": "Closure order / approval withdrawal", "dues_statement": "Statement of student and staff dues",
         "merger_order": "Merger approval order", "conversion_order": "Conversion approval order", "other": "Other"}
PDF_BAD = (b"/JavaScript", b"/JS", b"/Launch", b"/EmbeddedFile", b"/RichMedia", b"/Encrypt", b"/XFA")


def _master():
    p = VAR / "vault_master.key"
    if not p.exists():
        p.write_bytes(AESGCM.generate_key(bit_length=256))
        os.chmod(p, 0o600)
    return p.read_bytes()


def _key_files():
    """Keys that may hold a document's wrapped key: current, then a rotation in progress (.next), then the previous one (.prev)."""
    return [VAR / n for n in ("vault_master.key", "vault_master.key.next", "vault_master.key.prev")]


def _unwrap(wrapped: bytes) -> bytes:
    for p in _key_files():
        if p.exists():
            try:
                return AESGCM(p.read_bytes()).decrypt(wrapped[:12], wrapped[12:], b"scourge-vault-key")
            except InvalidTag:
                continue
    raise InvalidTag()


def _wrap(key: bytes, master: bytes) -> str:
    wn = secrets.token_bytes(12)
    return base64.b64encode(wn + AESGCM(master).encrypt(wn, key, b"scourge-vault-key")).decode()


def rotate_master(conn):
    """Crash-safe master-key rotation. Every document's per-file key is re-wrapped under a new master key.
    1. the new key is written to vault_master.key.next (0600) and fsynced;
    2. every wrapped key is re-wrapped in ONE database transaction;
    3. the old key is copied to .prev, then .next replaces the current key in one atomic rename.
    A crash at any point leaves every document readable: _unwrap tries current, .next and .prev.
    Returns the number of documents re-wrapped. Call finalize_rotation() once all documents verify."""
    cur, nxt, prev = _key_files()
    _master()                                         # make sure a current key exists
    new = AESGCM.generate_key(bit_length=256)
    fd = os.open(nxt, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(new); f.flush(); os.fsync(f.fileno())
    rows = [(r[0], r[1]) for r in conn.execute("SELECT id, wrapped_key FROM documents").fetchall()]
    with conn:
        for rid, wk in rows:
            conn.execute("UPDATE documents SET wrapped_key=? WHERE id=?", (_wrap(_unwrap(base64.b64decode(wk)), new), rid))
    # keep the old key as .prev (copy, fsynced), then swap .next in with one atomic rename: a current key always exists
    fd = os.open(prev, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(cur.read_bytes()); f.flush(); os.fsync(f.fileno())
    os.replace(nxt, cur)
    os.chmod(cur, 0o600)
    return len(rows)


def finalize_rotation():
    """Delete the previous master key once every document has been verified under the new one."""
    prev = _key_files()[2]
    if prev.exists():
        prev.unlink()


class Refused(Exception):
    pass


class IntegrityFailure(Exception):
    pass


def sniff(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    raise Refused("Only PNG, JPEG or PDF files are accepted (checked by content, not by name).")


def check(data: bytes) -> str:
    if not data:
        raise Refused("The file is empty.")
    if len(data) > MAX_BYTES:
        raise Refused("The file is larger than 2 MB.")
    mime = sniff(data)
    if mime == "application/pdf":
        if b"%%EOF" not in data[-2048:]:
            raise Refused("The PDF is incomplete or damaged.")
        for bad in PDF_BAD:
            if bad in data:
                raise Refused(f"The PDF contains {bad.decode()} (scripts, launch actions, attachments or encryption are not accepted).")
    return mime


def seal(fdr_id: str, data: bytes):
    """Returns (stored_as, wrapped_key_b64, nonce_b64, sha256). Writes only ciphertext to disk."""
    sha = hashlib.sha256(data).hexdigest()
    key, nonce = AESGCM.generate_key(bit_length=256), secrets.token_bytes(12)
    ct = AESGCM(key).encrypt(nonce, data, f"{fdr_id}|{sha}".encode())
    wn = secrets.token_bytes(12)
    wrapped = wn + AESGCM(_master()).encrypt(wn, key, b"scourge-vault-key")
    name = uuid.uuid4().hex + ".bin"
    (VAULT / name).write_bytes(ct)
    os.chmod(VAULT / name, 0o600)
    return name, base64.b64encode(wrapped).decode(), base64.b64encode(nonce).decode(), sha


def open_doc(row) -> bytes:
    try:
        wrapped = base64.b64decode(row["wrapped_key"])
        key = _unwrap(wrapped)
        data = AESGCM(key).decrypt(base64.b64decode(row["nonce"]), (VAULT / row["stored_as"]).read_bytes(),
                                   f"{row['fdr_id']}|{row['sha256']}".encode())
    except (InvalidTag, FileNotFoundError, ValueError) as e:
        raise IntegrityFailure(f"stored file failed authenticated decryption ({type(e).__name__})")
    if hashlib.sha256(data).hexdigest() != row["sha256"]:
        raise IntegrityFailure("hash re-check failed")
    return data


def wipe():
    for p in VAULT.glob("*.bin"):
        p.unlink()
