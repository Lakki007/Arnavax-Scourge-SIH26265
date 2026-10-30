"""Named user accounts with passwords (scrypt), separate from the demo role picker.

AUTH_MODE (env SCOURGE_AUTH):
  demo      - role picker AND password sign-in both work (judges can click through)
  password  - production: role picker is refused; every person signs in with their own account,
              officers additionally with TOTP when MFA is required.
Passwords are stored as scrypt(n=2^14, r=8, p=1) with a per-account 16-byte salt; never logged."""
import hashlib
import hmac
import os
import re
import secrets
import time

from . import db

AUTH_MODE = os.environ.get("SCOURGE_AUTH", "demo")
N, R, P = 2 ** 14, 8, 1
MIN_LEN = 12
USER_RE = re.compile(r"^[a-z0-9._-]{3,40}$")


class Weak(ValueError):
    pass


def _hash(pw: str, salt: bytes) -> str:
    return hashlib.scrypt(pw.encode(), salt=salt, n=N, r=R, p=P, dklen=32).hex()


def check_strength(user: str, pw: str):
    if len(pw) < MIN_LEN:
        raise Weak(f"Use at least {MIN_LEN} characters.")
    if user.lower() in pw.lower():
        raise Weak("The password must not contain the user name.")
    if len(set(pw)) < 6:
        raise Weak("The password is too repetitive.")


def create(user: str, pw: str, role: str, inst: str | None = None, officer: str | None = None):
    user = user.strip().lower()
    if not USER_RE.match(user):
        raise Weak("User names are 3-40 characters: a-z, 0-9, dot, dash, underscore.")
    if role not in ("officer", "institution") or (role == "officer") != bool(officer) or (role == "institution") != bool(inst):
        raise Weak("An officer account needs an officer id; an institution account needs an institution id.")
    check_strength(user, pw)
    salt = secrets.token_bytes(16)
    db.x("INSERT INTO accounts(username,role,institution_id,officer,salt,pw_hash,created_at) VALUES(?,?,?,?,?,?,?)",
         (user, role, inst, officer, salt.hex(), _hash(pw, salt), time.time()))
    db.event("system", "account_created", username=user, role=role, institution=inst, officer=officer)


_DUMMY_SALT = secrets.token_bytes(16)


def verify(user: str, pw: str):
    """Returns the account row or None. Constant work whether or not the user exists."""
    a = db.q1("SELECT * FROM accounts WHERE username=? AND disabled=0", (user.strip().lower(),))
    h = _hash(pw, bytes.fromhex(a["salt"]) if a else _DUMMY_SALT)
    if a and hmac.compare_digest(h, a["pw_hash"]):
        return a
    return None


def role_picker_allowed() -> bool:
    return AUTH_MODE != "password"
