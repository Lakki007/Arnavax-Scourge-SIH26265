#!/usr/bin/env python3
"""Re-verify every stored bank-session result against the VERIFIER's signature, and re-derive its decision.

An administrator with write access to the portal's database cannot pass this check after changing a stored
result, its facts or its decision: the signature covers the verifier's exact bytes and lives with a key the
portal never holds; the decision is recomputed from those bytes with the current policy.

  scripts/audit_evidence.py            exit 0 if every result verifies; prints each failure otherwise"""
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import evidence_sig, policy  # noqa: E402
from app.settings import DB_PATH  # noqa: E402

pub = evidence_sig.pinned()
if not pub:
    sys.exit("no verifier key pinned yet (no signed evidence received)")
c = sqlite3.connect(DB_PATH)
c.row_factory = sqlite3.Row
bad, n, legacy = [], 0, 0
for r in c.execute("SELECT * FROM evidence ORDER BY id"):
    n += 1
    if not r["facts_raw"]:
        legacy += 1                     # recorded before signed evidence existed
        continue
    ch = r["channel"] or evidence_sig.MPC
    key = evidence_sig.pinned_for(ch)
    ok, fp, why = evidence_sig.verify_with(key, r["facts_raw"].encode(), r["verifier_sig"] or "") if key else (False, None, "no pinned key for channel")
    if not ok:
        bad.append((r["id"], f"signature: {why}"))
        continue
    facts = json.loads(r["facts_raw"])
    if facts.get("challenge_id") != r["challenge_id"]:
        bad.append((r["id"], "bound to a different challenge than the signed result"))
        continue
    fdr = dict(c.execute("SELECT * FROM fdrs WHERE id=?", (r["fdr_id"],)).fetchone())
    facts.pop("metrics", None)
    decision, _ = evidence_sig.cap(policy.apply(facts, fdr)[0], ch)
    if decision != r["decision"]:
        bad.append((r["id"], f"stored decision '{r['decision']}' but the signed facts give '{decision}'"))
for eid, why in bad:
    print(f"evidence #{eid}: {why}")
print(f"{n - len(bad) - legacy} of {n} results verify against verifier key {evidence_sig.fingerprint(pub)[:16]}"
      + (f"; {legacy} recorded before signing" if legacy else ""))
sys.exit(1 if bad else 0)
