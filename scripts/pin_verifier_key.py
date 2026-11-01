#!/usr/bin/env python3
"""Deliberately re-pin the verifier's public key after a planned verifier key change (audited).

  scripts/pin_verifier_key.py <operator-name> "<reason>"     pins the key the verifier currently publishes"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import evidence_sig  # noqa: E402

if len(sys.argv) != 3 or len(sys.argv[2]) < 10:
    sys.exit(__doc__)
pub = evidence_sig.current_pub_hex()
if not pub:
    sys.exit("the verifier has not published a key")
old = evidence_sig.pinned()
evidence_sig.pin(pub, actor=f"operator:{sys.argv[1]}", note=sys.argv[2] + (f" (replaces {evidence_sig.fingerprint(old)[:16]})" if old else ""))
print(f"pinned verifier key {evidence_sig.fingerprint(pub)[:16]}")
