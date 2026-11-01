# Implementation status: claim → evidence

Labels: **Tested here** = an automated test in this repo exercises it (`scripts/test_all.sh`: 283 demo-mode + 10 production-mode pass). **Team-recorded** = run by the team, logged, not reproducible by a reviewer without a bank account. **Implemented** = code exists, no dedicated test. **Planned** = designed, not built. Handbook rules are *our reading of the AICTE handbook, to be validated by AICTE*.

| Claim | Status | Evidence |
|---|---|---|
| MPC-TLS proof against a bank page, challenge-bound, selective disclosure | Tested here (synthetic bank) | `tests/test_acceptance.py` |
| Forged / tampered / replayed / stale / wrong-host proofs rejected | Tested here | `test_acceptance.py`, `test_policy.py` |
| Internal key alone cannot inject "verified"; admin rewrite of evidence detected; result cannot be moved to another challenge | Tested here | `tests/test_evidence_boundary.py`, `scripts/audit_evidence.py` |
| Self-approval impossible; cross-institution access refused; OTP replay refused; rollback on failure | Tested here (production mode) | `tests/test_production_mode.py` |
| Lifecycle: filing → scrutiny → renewal → release → deductions → bank acknowledgement | Tested here | `test_lifecycle*.py` |
| Renewal during pending release; ack of revoked letter; scheduler offline a week, no duplicates | Tested here | `tests/test_lifecycle_edges.py` |
| Backup / restore / vault-key rotation (crash-safe) | Tested here | `tests/test_backup.py` |
| Backpressure (429), verifier outage, portal crash mid-proof, latency/memory/traffic | Tested here, measured | `scripts/ops_bench.py`, `docs/OPERATIONS.md` |
| Split-key session with a real bank's logged-in deposits API (SBI, 1 Nov: 200 OK, 0.4 s, 61 MB) | Team-recorded | `tools/realbank/RESULTS.md` |
| Reading FD field values at a real bank | **Not achieved** — SBI encrypts deposit data inside HTTPS | `tools/realbank/RESULTS.md` |
| In-browser prover for app-encrypted banks | **Planned**; verifier→portal bridge tested | `docs/EXTENSION_PROVER.md`, `tests/test_extension_bridge.py` |
| Signed e-advice / branch confirmation fallback | Implemented, labelled weaker evidence | `docs/FEASIBILITY.md` |
| Hosted demo | Planned | — |
| External penetration test, user trials, AICTE rule validation | Planned | — |
