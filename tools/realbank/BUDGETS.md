# Proof traffic depends on the request budget, not the page size

Measured 1 Nov 2026 with `bin/real_bank_probe` (same binary as the live real-bank run) against a public HTTPS server
(`raw.githubusercontent.com`, certificate checked against public Mozilla roots), prover and verifier in one process.
Request 143 bytes, response ~930 bytes. One run per row.

| MAX_SENT (request budget) | MAX_RECV (response budget) | Prover↔verifier traffic | Time |
|---|---|---|---|
| 2 KB | 4 KB | 38.7 MB | 0.9 s |
| 4 KB | 8 KB | 60.0 MB | 2.2 s |
| 4 KB | 32 KB | 60.0 MB | 1.5 s |
| 8 KB | 16 KB | 102.5 MB | 2.7 s |
| 16 KB | 16 KB | 187.5 MB | 3.6 s |
| 16 KB | 64 KB | 187.5 MB | 2.7 s |

**Reading.** Traffic grows with the request budget (about 10 MB per extra KB of request) and does not change with the
response budget, so a large FD page costs no more than a small one. The 357 MB in the 30 Oct real-bank run came from an
oversized request budget; that bank request was 2,037 bytes and fits a 4 KB budget, which here measured 60 MB.
The probe source now defaults to MAX_SENT=4096, MAX_RECV=65536 (the prebuilt binary in `bin/` keeps the old default until rebuilt with `scripts/build_rust.sh`, so pass the variables explicitly).

Reproduce: `URL=https://raw.githubusercontent.com/Lakki007/Arnavax-Scourge-SIH26265/main/VERSION ALLOW=githubusercontent.com MAX_SENT=4096 MAX_RECV=8192 bin/real_bank_probe`
