#!/usr/bin/env python3
"""Create a named account (deployment). Usage:
  scripts/create_account.py officer <username> <RB-xx>
  scripts/create_account.py institution <username> <INST-id>
The password is read from the terminal (never from the command line)."""
import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import accounts  # noqa: E402

if len(sys.argv) != 4 or sys.argv[1] not in ("officer", "institution"):
    sys.exit(__doc__)
role, user, ident = sys.argv[1:]
pw = getpass.getpass("Password (12+ characters): ")
if pw != getpass.getpass("Repeat: "):
    sys.exit("Passwords differ.")
try:
    accounts.create(user, pw, role, ident if role == "institution" else None, ident if role == "officer" else None)
except accounts.Weak as e:
    sys.exit(str(e))
print(f"Created {role} account {user}.")
if role == "officer":
    # Production (SCOURGE_AUTH=password) requires two-factor sign-in for every officer, so enrol it now.
    import time
    from app import db, totp
    secret = totp.new_secret()
    print("Add this to the officer's authenticator app:\n  " + totp.uri(secret, ident))
    for _ in range(3):
        st = totp.verify(secret, input("Current 6-digit code: ").strip(), 0)
        if st is not None:
            db.x("INSERT OR REPLACE INTO officer_mfa(officer,secret,enabled,created_at,last_step) VALUES(?,?,1,?,?)", (ident, secret, time.time(), st))
            print(f"Two-factor sign-in enrolled for {ident}.")
            break
    else:
        sys.exit("Code not accepted. The account exists but cannot sign in until two-factor is enrolled; run again.")
