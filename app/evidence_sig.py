"""The verifier -> portal evidence boundary.

The verifier signs the exact bytes of every result (ECDSA secp256k1 over SHA-256) with a key that lives on the
verifier host. The portal accepts a result only if that signature verifies against the verifier key it has
PINNED. So neither the portal's internal key nor write access to the portal's database is enough to inject
a "verified" result, and every stored result can be re-verified later (scripts/audit_evidence.py), which
catches an administrator who rewrites evidence or decisions.

Pinning: the first key seen is pinned (var/verifier_key.pin) and the pin is written to the hash-chained audit
log. A different key is refused until an operator re-pins deliberately (scripts/pin_verifier_key.py), which
is audited too."""
import hashlib
import os

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils

from .settings import VAR, VERIFIER_PUB_FILE

PIN_FILE = VAR / "verifier_key.pin"


def current_pub_hex():
    env = os.environ.get("SCOURGE_VERIFIER_PUBKEY", "").strip()
    if env:
        return env
    try:
        return VERIFIER_PUB_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None


def fingerprint(pub_hex):
    return hashlib.sha256(bytes.fromhex(pub_hex)).hexdigest()


def pinned():
    try:
        return PIN_FILE.read_text(encoding="utf-8").split()[1]           # "<fingerprint> <pub_hex>"
    except (FileNotFoundError, IndexError):
        return None


def pin(pub_hex, actor="system", note="first verifier key seen"):
    from . import db
    fd = os.open(PIN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(f"{fingerprint(pub_hex)} {pub_hex}\n")
    db.event(actor, "verifier_key_pinned", None, fingerprint=fingerprint(pub_hex), note=note)


def check(raw: bytes, sig_hex: str):
    """Returns (ok, fingerprint_or_None, reason)."""
    pub_hex = current_pub_hex()
    if not pub_hex:
        return False, None, "verifier_key_unavailable"
    pinned_hex = pinned()
    if pinned_hex is None:
        pin(pub_hex)
        pinned_hex = pub_hex
    if pinned_hex != pub_hex:
        return False, None, "verifier_key_changed"                       # refuse until an operator re-pins
    if not sig_hex:
        return False, None, "unsigned"
    return verify_with(pinned_hex, raw, sig_hex)


def verify_with(pub_hex, raw: bytes, sig_hex: str):
    try:
        pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256K1(), bytes.fromhex(pub_hex))
        sig = bytes.fromhex(sig_hex)
        if len(sig) != 64:
            return False, None, "bad_signature"
        der = utils.encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big"))
        pub.verify(der, raw, ec.ECDSA(hashes.SHA256()))
        return True, fingerprint(pub_hex), None
    except (InvalidSignature, ValueError):
        return False, None, "bad_signature"


def sign(raw: bytes, key_hex: str) -> str:
    """Verifier-side signing in Python (used by the extension's verifier bridge). Same format as the Rust verifier."""
    key = ec.derive_private_key(int(key_hex, 16), ec.SECP256K1())
    r, s = utils.decode_dss_signature(key.sign(raw, ec.ECDSA(hashes.SHA256())))
    n = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
    if s > n // 2:                                                        # low-S, like k256
        s = n - s
    return r.to_bytes(32, "big").hex() + s.to_bytes(32, "big").hex()
