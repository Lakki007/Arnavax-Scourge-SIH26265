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
KINDS = {"fdr_receipt": "FDR receipt", "bank_letter": "Bank letter / e-advice", "renewal": "Renewal advice", "other": "Other"}
PDF_BAD = (b"/JavaScript", b"/JS", b"/Launch", b"/EmbeddedFile", b"/RichMedia", b"/Encrypt", b"/XFA")


def _master():
    p = VAR / "vault_master.key"
    if not p.exists():
        p.write_bytes(AESGCM.generate_key(bit_length=256))
        os.chmod(p, 0o600)
    return p.read_bytes()


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
        key = AESGCM(_master()).decrypt(wrapped[:12], wrapped[12:], b"scourge-vault-key")
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
