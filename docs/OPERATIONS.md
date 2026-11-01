# Operations: measured on declared hardware

Run: `./scripts/run.sh &` then `python3 scripts/ops_bench.py 20`. Raw output: `measurements/ops_bench_2026-11-01.json`.
Workload: real prover + signing verifier + **synthetic bank** (not a real bank). Hardware: 2 vCPU Intel Xeon @ 2.10 GHz, 7.8 GB RAM, Linux.

| Measure | Result |
|---|---|
| End-to-end proof (challenge → proof → signed facts recorded), 20 runs | p50 1.12 s, p95 1.35 s |
| Prover→verifier MPC traffic per proof | 38.7 MB (median) |
| Burst of 6 at once (limit 2 concurrent) | 2 accepted, 4 refused with HTTP 429; refused challenges stay unused; client retried; all 6 verified in 5.7 s |
| Verifier down during a proof | job fails cleanly, challenge stays unused, same challenge verifies after restart (0.11 s) |
| Portal killed (-9) mid-proof | restarted, interrupted job marked failed, records intact, reset not blocked |
| Backup / verify / restore / vault-key rotation | all OK, each < 0.1 s on this dataset |
| Evidence signature audit after all of the above | every stored result verifies against the pinned verifier key |
| Peak memory | verifier 405 MB, prover 181 MB, portal 129 MB, synthetic bank 30 MB |

## Limits and operator procedures
- Concurrency is capped (`max_concurrent_proofs`, default 2) and each proof times out at 90 s; excess requests get 429 and retry.
- Real banks add network latency and larger pages; SBI deposit data is app-encrypted, so cryptographic verification there needs the in-browser prover (planned).
- **When cryptographic verification is unavailable** (bank unsupported, verifier down): the case stays open, the officer uses the governed fallback channel (bank-signed document / branch confirmation), recorded and labelled as weaker evidence; nothing is marked "cryptographically verified".
- Recovery runbook: `scripts/backup.py create|verify|restore`, `scripts/rotate_vault_key.py`, `scripts/audit_evidence.py`, `scripts/pin_verifier_key.py`.
