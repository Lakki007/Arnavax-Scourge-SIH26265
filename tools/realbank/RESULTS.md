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

## 2026-11-01 · State Bank of India (OnlineYONO web) · team member's own ₹1,000 FD · follow-up

A fresh ₹1,000 7-day FD was opened and the browser's own FD-details calls were probed from the laptop
(`.tlsn/.../real_bank_probe`, Mac arm64 build). Requests were isolated with DevTools "Request conditions"
(block `https://api.onlineyono.sbi.bank.in/app/deposits/*`), copied as cURL, and fed through `from_curl.py`.

Findings, in order:
1. A **replayed** request (already sent by the browser) → MPC-TLS session completes, SBI returns **HTTP 401**.
   `status`, `requestNo`, `txnId` present (encrypted, 16/32/40 bytes); `body` absent. 0.7 s, 188 MB at MAX_SENT=16384.
2. A **blocked, never-sent** request copied as cURL → still **401**. Chrome omits cookies and the `Origin`/`Sec-*`
   headers from a blocked request's "Copy as cURL" (provisional headers only).
3. The blocked request **with the browser's own headers merged in** (`from_curl.py --merge`, adding `Origin`,
   `User-Agent`, `Sec-Fetch-*`, `sec-ch-ua*`; `Authorization`/body unchanged) → **HTTP 200 OK**, 0.4 s, **61 MB**.
   But the response is only 388 bytes (vs the browser's real 2.3 kB), and the plain fields are absent: SBI replies
   with a short encrypted body, not the deposit record.
4. Plain `curl` (no browser TLS fingerprint) is blocked by SBI's WAF ("Request Rejected", support ID) — this is a
   firewall block of a non-browser client, separate from the API behaviour above.

**What this establishes.** The split-key (MPC-TLS) session works against SBI's **logged-in** deposits API
(`/app/deposits/allDeposit/customer/accounts`), certificate verified against public (Mozilla) roots, and SBI returns
HTTP 200 to a correctly-headed request. Per-proof traffic on a real bank is ~61 MB at a 4 KB request budget,
matching `BUDGETS.md`.

**What it does NOT establish.** The account/FD fields. SBI's web app encrypts both the request and response bodies
at the application layer (the page shows `{"header":{...},"body":"<ciphertext>"}`), with a key its JavaScript derives
per request in the browser. A request copied out of the browser and re-sent is answered with a short encrypted reply,
not the deposit record, so no FD field can be disclosed this way.

**Conclusion / next step.** For a bank that encrypts payloads inside HTTPS (SBI YONO), the only route to the data is a
**browser-extension prover**: notarise the browser's *own* original request as it is sent (no replay), and disclose the
page's per-session app-layer key to AICTE's verifier so it can decrypt the proven ciphertext. A bank that serves FD
pages without this extra layer could be proven today with the existing probe. Wording stays:
"live real-bank session (first bank tested: SBI)"; never claim the account or FD data was proven.
