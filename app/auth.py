"""Demo sign-in (no passwords; DEMO MODE ONLY) with signed session cookies and CSRF tokens.
Roles: officer (AICTE Regulation Bureau) and institution:<id>. Every route checks the role server-side."""
import base64
import hashlib
import hmac
import json
import secrets
import time

from fastapi import HTTPException, Request

from .settings import SESSION_KEY

COOKIE = "scourge_session"
MAX_AGE = 8 * 3600


def _sign(payload: bytes) -> str:
    return hmac.new(SESSION_KEY.encode(), payload, hashlib.sha256).hexdigest()


OFFICERS = {"RB-01": "Scrutiny officer", "RB-02": "Approving officer"}  # demo identities; real use: AICTE single sign-on


def make_cookie(role: str, institution_id: str | None, who: str | None = None) -> str:
    data = json.dumps({"role": role, "inst": institution_id, "who": who, "csrf": secrets.token_hex(16), "exp": time.time() + MAX_AGE}).encode()
    b = base64.urlsafe_b64encode(data).decode()
    return f"{b}.{_sign(data)}"


def read(request: Request):
    raw = request.cookies.get(COOKIE)
    if not raw or "." not in raw:
        return None
    b, sig = raw.rsplit(".", 1)
    try:
        data = base64.urlsafe_b64decode(b.encode())
    except Exception:
        return None
    if not hmac.compare_digest(_sign(data), sig):
        return None
    s = json.loads(data)
    if s["exp"] < time.time():
        return None
    return s


def actor(s) -> str:
    return "officer" if s["role"] == "officer" else f"institution:{s['inst']}"


def officer_id(s) -> str:
    """Which officer this is (for maker-checker). Older sessions without an identity are RB-01."""
    return s.get("who") or "RB-01"


def require(request: Request, role: str | None = None):
    s = read(request)
    if not s:
        raise HTTPException(401, "Sign in to continue.")
    if role and s["role"] != role:
        raise HTTPException(403, "Your role cannot do this." if role == "officer" else "This page is for institutions.")
    return s


def check_csrf(s, token: str | None):
    if not token or not hmac.compare_digest(s["csrf"], token):
        raise HTTPException(403, "The form expired. Reload the page and try again.")


def can_see_fdr(s, fdr) -> bool:
    return s["role"] == "officer" or (s["role"] == "institution" and s["inst"] == fdr["institution_id"])
