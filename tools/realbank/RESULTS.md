# Real-bank field results

## 2026-10-30 · State Bank of India (YONO net banking) · team member's own account

Run on a MacBook Pro (M4 Pro), `bin/real_bank_probe` built from this repo (`scripts/build_rust.sh`), pinned TLSNotary commit.
Request: the browser's own logged-in `POST /app/statement/customer/casaod/accounts` call (account summary), replayed with
the browser's session headers and payload through the split-key session. Headers, payload and values were never shared.

```
{"ok":true,"server":"api.onlineyono.sbi.bank.in","status_line":"HTTP/1.1 200 OK","secs":1.1,"request_bytes":2037,
 "response_bytes":389,"disclosed_response_bytes":15,"prover_to_verifier_MB":357.4,
 "fields":[{"key":"status","found":false},{"key":"requestNo","found":false}]}
```
Second run (more field names): `ok:true`, HTTP 200, 1.0 s, 388 response bytes; only `body` matched (4 bytes).

**What this proves.** MPC-TLS with SBI's production API server works: the server certificate verifies against public
(Mozilla) roots, the verifier co-holds the session keys, SBI answers HTTP 200, and the proof verifies in about 1 second
with ~357 MB of prover-verifier traffic.

**What it does not prove yet.** The account data. SBI's browser view of this call is `{"body":"<app-layer ciphertext>",
"header":{"status":"success",...}}` (about 1 KB+). The replayed request got a ~0.4 KB reply without `status`, so SBI most
likely refuses replayed requests (one-time request numbers / encrypted payloads tied to the browser session).

**Next.** (1) Browser-extension prover so the proof covers the browser's own original request (no replay).
(2) For banks that encrypt payloads inside HTTPS, disclose the per-session app key to the verifier so it can decrypt the
proven ciphertext. (3) Repeat on an FD details page.
