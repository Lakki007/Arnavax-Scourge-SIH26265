# Real-bank field results

Scourge's split-key (MPC-TLS) proof, run against a **real bank's production servers** from a team member's own laptop and own account. Credentials, session headers and account values never left the laptop; only the probe's summary line is recorded here.

## Milestones

| Date | Milestone | Result |
|---|---|---|
| 30 Oct 2026 | Split-key session with a real bank's production net-banking API (first bank tested: SBI) | ✓ Certificate verified against public (Mozilla) roots, AICTE's verifier co-held the session keys, **HTTP 200**, proof verified in ~1 s |
| 1 Nov 2026 | Split-key session with the bank's **logged-in deposits API**, on a deposit opened for the test | ✓ **HTTP 200 OK in 0.4 s, 61 MB** of prover-verifier traffic |
| 1 Nov 2026 | Mapped exactly what the bank's API requires from a client | ✓ The browser's own request headers (`Origin`, `Sec-Fetch-*`, `User-Agent`) — with them the bank accepts the split-key session |
| 1 Nov 2026 | Field intelligence: how a major bank protects deposit data | ✓ SBI adds a **second encryption layer inside HTTPS** for deposit data, keyed per request in the browser — this shaped the in-browser prover design (`docs/EXTENSION_PROVER.md`) |
| 1 Nov 2026 | Proof cost on a real bank | ✓ **61 MB per proof** at a 4 KB request budget — matches the public-server measurement in `BUDGETS.md` |

Next milestone: the deposit fields themselves through the **in-browser prover**, which notarises the page's own request and discloses only the page's session key to AICTE's verifier. The verifier→portal bridge for it is already built and tested (`tools/extension/bridge.py`, `tests/test_extension_bridge.py`).

## 2026-11-01 · Logged-in deposits API · team member's own ₹1,000 FD

A ₹1,000 7-day FD was opened for the test. The browser's own deposits call was isolated in DevTools, its request handed to the probe with `tools/realbank/from_curl.py` (which prints no secrets), and proved through the split-key session:

```
{"ok":true,"server":"api.onlineyono.sbi.bank.in","status_line":"HTTP/1.1 200 OK","secs":0.4,
 "request_bytes":2040,"prover_to_verifier_MB":61.0}
```

**What it shows.**
- The split-key session works against the bank's **logged-in deposits API**, not just a public page: certificate checked against public roots, AICTE's verifier co-holding the keys, the bank answering 200 OK.
- **Speed and cost on a real bank:** 0.4 s and 61 MB per proof — the numbers used in the feasibility sizing.
- **The bank's client requirements are mapped:** the API accepts the session when the request carries the browser's own headers (`Origin`, `Sec-Fetch-*`, `User-Agent`). `from_curl.py --merge` builds exactly that.
- **How SBI protects deposit data:** its web app wraps the deposit record in an app-layer cipher inside HTTPS (`{"header":{…},"body":"<ciphertext>"}`), keyed per request by the page's own JavaScript. A record encrypted this way is unlocked by the in-browser prover, which proves the page's own request and discloses only that session key to AICTE's verifier. That is the design in `docs/EXTENSION_PROVER.md`, and its verifier→portal bridge is built and tested.

## 2026-10-30 · Production net-banking API

Run on a MacBook Pro (M4 Pro), `real_bank_probe` built from this repo (`scripts/build_rust.sh`), pinned TLSNotary commit, against the bank's own account-summary call:

```
{"ok":true,"server":"api.onlineyono.sbi.bank.in","status_line":"HTTP/1.1 200 OK","secs":1.1,"request_bytes":2037}
```

**What it shows.** MPC-TLS with a real bank's production API server works end to end: the certificate verifies against public (Mozilla) roots, the verifier co-holds the session keys, the bank answers HTTP 200, and the proof verifies in about a second. (Traffic on that run was 357 MB because the request budget was set to 16 KB; at a right-sized 4 KB budget it is ~60 MB — see `BUDGETS.md` and the 1 Nov run.)

## How to run it

See `FIELD_TEST.md`. Short version: log in to your own net banking, find the deposit request in DevTools → Network, copy it as cURL, run `pbpaste | python3 tools/realbank/from_curl.py`, then the probe. Only the probe's one summary line is ever shared.
