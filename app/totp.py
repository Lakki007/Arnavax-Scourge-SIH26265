"""RFC 6238 time-based one-time passwords (authenticator apps), standard library only.
6 digits, 30-second steps, HMAC-SHA1 (the authenticator-app default); accepts ±1 step of clock skew;
a used step cannot be replayed (the last accepted step is stored per officer)."""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

STEP, DIGITS = 30, 6


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret: str) -> bytes:
    return base64.b32decode(secret + "=" * (-len(secret) % 8))


def code_at(secret: str, step: int) -> str:
    h = hmac.new(_key(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    n = (struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % (10 ** DIGITS)
    return f"{n:0{DIGITS}d}"


def now_step(t: float | None = None) -> int:
    return int((t if t is not None else time.time()) // STEP)


def verify(secret: str, code: str, last_step: int = 0, t: float | None = None):
    """Returns the matched step (int) or None. Rejects steps at or before last_step (replay)."""
    code = (code or "").strip().replace(" ", "")
    if not code.isdigit() or len(code) != DIGITS:
        return None
    cur = now_step(t)
    for st in (cur, cur - 1, cur + 1):
        if st > last_step and hmac.compare_digest(code_at(secret, st), code):
            return st
    return None


def uri(secret: str, account: str, issuer: str = "Scourge AICTE") -> str:
    return f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret}&issuer={quote(issuer)}&digits={DIGITS}&period={STEP}"
