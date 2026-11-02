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

## Institution → AICTE over a constrained link

`SCOURGE_VERIFIER_ADDR=127.0.0.1:7147 ./scripts/run.sh &` then `python3 scripts/wan_bench.py`. The prover reaches the verifier through a link emulator (bandwidth cap and one-way delay each direction); 3 proofs per profile, synthetic bank. Raw: `measurements/wan_bench_2026-11-02.json`.

| Link (each way) | Round trip | Verified | Proof time (median) |
|---|---|---|---|
| 1 Gbit/s | 2 ms | 3/3 | 2.1 s |
| 100 Mbit/s | 30 ms | 3/3 | 6.4 s |
| 50 Mbit/s | 40 ms | 3/3 | 10.0 s |
| 20 Mbit/s | 60 ms | 3/3 | 20.8 s |
| 10 Mbit/s | 80 ms | 3/3 | 37.9 s |

Traffic per proof: 38.7 MB institution → verifier, 2.3 MB back. Time is dominated by the institution's **upload** speed: plan on ~10 s at 50 Mbit/s and ~40 s at 10 Mbit/s, inside the 90 s timeout. This is an emulated link on one machine, not two physical machines; the earlier 0.4 s SBI figure ran prover and verifier in one process and is a compatibility result, not deployment latency. A real bank page larger than the synthetic one (SBI's session moved 61 MB) scales proportionally.

## Limits and operator procedures
- Concurrency is capped (`max_concurrent_proofs`, default 2) and each proof times out at 90 s; excess requests get 429 and retry.
- Real banks add network latency and larger pages; SBI deposit data is app-encrypted, so cryptographic verification there needs the in-browser prover (planned).
- **When cryptographic verification is unavailable** (bank unsupported, verifier down): the case stays open, the officer uses the governed fallback channel (bank-signed document / branch confirmation), recorded and labelled as weaker evidence; nothing is marked "cryptographically verified".
- Recovery runbook: `scripts/backup.py create|verify|restore`, `scripts/rotate_vault_key.py`, `scripts/audit_evidence.py`, `scripts/pin_verifier_key.py`.
