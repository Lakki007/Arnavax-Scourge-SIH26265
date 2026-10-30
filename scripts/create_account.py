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
