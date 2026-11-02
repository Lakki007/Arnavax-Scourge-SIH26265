# Implementation status: claim → evidence

Labels: **Tested here** = an automated test in this repo exercises it (`scripts/test_all.sh`: 288 demo-mode + 10 production-mode pass). **Team-recorded** = run by the team, logged, not reproducible by a reviewer without a bank account. **Implemented** = code exists, no dedicated test. **Planned** = designed, not built. Handbook rules are *our reading of the AICTE handbook, to be validated by AICTE*.

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
| Proof time over constrained links (10–1000 Mbit/s, 2–80 ms RTT) | Measured on an emulated link (not two machines) | `scripts/wan_bench.py`, `docs/OPERATIONS.md` |
| Per-field evidence status (value, source, freshness, release gate) on the deposit page and `/api/fdrs/{id}/evidence-matrix` | Tested here | `test_lifecycle_edges.py::test_e6` |
| Retention: closed records kept N years, legal hold blocks archiving, archived records read-only | Tested here | `test_lifecycle_edges.py::test_e4`, `test_e5` |
| Extension bridge cannot mint Verified (own key, results labelled unproven and capped) | Tested here | `test_extension_bridge.py`, `test_evidence_boundary.py::test_b5` |
| Split-key session with a real bank's logged-in deposits API (SBI, 1 Nov: 200 OK, 61 MB; prover and verifier in one process, so 0.4 s is not deployment latency) | Team-recorded | `tools/realbank/RESULTS.md` |
| Reading FD field values at a real bank | **Not achieved** — SBI encrypts deposit data inside HTTPS | `tools/realbank/RESULTS.md` |
| In-browser prover for app-encrypted banks | **Planned**; bridge built, its results capped at *Insufficient evidence* until the proof exists | `docs/EXTENSION_PROVER.md`, `tests/test_extension_bridge.py` |
| Signed e-advice / branch confirmation fallback | Implemented, labelled weaker evidence | `docs/FEASIBILITY.md` |
| Hosted demo | Live: https://scourge-sih26265.onrender.com (synthetic bank; proof verified from outside, 2 Nov: 12 s on the free plan) | `render.yaml` |
| External penetration test, user trials, AICTE rule validation | Planned | — |
