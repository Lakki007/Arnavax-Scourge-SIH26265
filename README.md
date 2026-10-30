# Scourge: bank-session proof for AICTE security deposits (SIH26265 prototype)

**Ask the bank, not the paper.** Smart India Hackathon 2026 · PS SIH26265 · Team **Arnavax#** (ID 199322)

- Demo video (90 s): https://youtu.be/8HoplhFOGCg
- **Full idea, solution and workflow (start here):** [`docs/IDEA.md`](docs/IDEA.md)
- Code: https://github.com/Lakki007/Arnavax-Scourge-SIH26265
- Idea deck: `docs/ppt/Scourge_SIH26265_Idea.pdf`
- Lifecycle in the portal: file FDR (renewals linked) → bank-session proof → monitor (maturity, re-checks) → officer decision (hash-chained audit) → two-officer release with release letter

An institution proves, from its own net-banking session, what the bank's server says about an FDR. AICTE's separate verifier checks the MPC-TLS proof (TLSNotary) and sees only that deposit's record. The app compares the proven fields with the register and lets an officer accept, escalate or release.

**Status:** release candidate `v1.0.0-rc1`, **demo mode** (synthetic bank, real proofs). Judge kit: `docs/JUDGE_DEMO.md`. Operations: `docs/RUNBOOK.md`. Verification: `docs/PHASE4_VERIFICATION.md`.

- **Real:** the cryptographic proof, challenge binding, policy and decisions.
- **Synthetic:** the bank, which is a local mock with a test certificate authority.
- **Not shown yet:** a proof of a logged-in FD page at a real bank. That is the open go/no-go test (`EXPERIMENT_V2.md` from the V2 package).

This is not production-ready. See "Limitations".

## Architecture (approved MVP, four components)

```
 browser ──► app (FastAPI + SQLite, :8080)  ── registry, challenges, policy, evidence, UI
                │  supervises ▼
                ├─ scourge_verifierd (:7047)   separate process; MPC-TLS verifier; posts FACTS to the app
                ├─ scourge_mockbank  (:4400)   SYNTHETIC bank, netbanking.mockbank.test (test CA)
                ├─ scourge_mockbank  (:4401)   attack demo: fake bank, prover's own CA
                └─ scourge_mockbank  (:4402)   attack demo: valid certificate, host not approved
 scourge_prove ── runs per proof (demo: on this server; real: on the institution's computer)
```

**One adjustment from the approved architecture:** the app also supervises the verifier and banks, and runs the demo prover, so a judge can operate everything from one browser. They stay separate OS processes. The verifier never shares memory with the app, and talks to it only through two internal endpoints (localhost plus a secret key).

## Setup and run (Linux x86_64; tested on Python 3.13, Chromium via Playwright)

```bash
pip install -r requirements.txt          # tests: pip install -r requirements-dev.txt
./scripts/setup_certs.sh       # SYNTHETIC test certificates (already present in the package)
./scripts/build_rust.sh        # optional: rebuild bin/ from source (clones TLSNotary at a pinned commit)
./scripts/run.sh               # http://127.0.0.1:8080 (always loopback; publish with deploy/nginx.conf)
```

On first run the app:

- creates `var/` (database, mock bank state, logs);
- generates secrets in `var/*.key` (mode 0600);
- loads the sample register;
- starts the verifier and the three synthetic banks.

**Reset:** use "Reset demo" in the bottom bar, or delete `var/` and restart.

## Five-minute demo (also on the "How it works" page)

1. Choose **Start the demo**, sign in as **AICTE officer**, open **FDR-001** and choose **Request bank proof**.
2. Choose **Switch to INST-42 to answer it**, then **Prove from my bank**. Ten real prover stages arrive, followed by a **Verified** stamp.
3. Open the evidence: the register's claim beside the bank's answer (point at a row to light up the exact bytes it came from), the request and response exactly as AICTE received them with everything else blacked out, measured cost, and limitations. Sign back in as the officer, **Accept as confirmed**, then **Release deposit**.
4. In **Mock bank**, set FD-0000777777 to "Loan raised against the deposit". Request and prove it with the *Accounts summary* page: **Discrepancy**. PAN and the other accounts stay hidden.
5. Under **Try to cheat**, choose *Your own fake bank*: **Rejected** (certificate).
6. In **Baseline**, upload `samples/edited_receipt_FD-0000777777.png`: the baseline approves it; Scourge shows the bank's answer.
7. In **System**, stop the verifier and try a proof: it fails and the challenge stays unused. Start the verifier and retry.

## Sample inputs

- **Register form, valid:** INST-42, `FD-0000888888`, mock bank, `750000`, issue `2024-01-01`, maturity `2034-01-01`. Then use **Mock bank → Open at mock bank** (keep `750000` for *Verified*, or type `700000` for *Discrepancy* on principal and balance). Without it, the proof ends as insufficient evidence.
- **Register form, invalid:** FD number `bad no!`, principal `-5`, issue date in the future, maturity in the past, duplicate `FD-0000123456`.
- **Bank behaviours** (Mock bank console): 16 cases, from active, closed, partly withdrawn, loan lien and maturity changed, to missing or duplicate fields, malformed JSON, redirect, HTML login page, chunked, multi-deposit response, unrelated deposit, and a slow bank (to try cancelling).
- **Uploads:** `samples/edited_receipt_FD-0000777777.png` (accepted by the baseline); `samples/not_an_image.png` (rejected on content).

## Tests

```bash
./scripts/run.sh &                                  # tests drive the running app
python3 -m pytest -q tests/                         # 100 tests (+2 that run only with the deployment access code): policy, acceptance over HTTP + real binaries, browser journeys
python3 scripts/shots.py var/shots                  # screenshots of every page, desktop and mobile; reports console errors and 5xx
```

## What is real, synthetic or incomplete

| Capability | Status |
|---|---|
| MPC-TLS proof, selective disclosure, certificate check, allow-list, endpoint check | Real (TLSNotary, MIT/Apache-2.0) |
| Single-use, expiring, institution-bound challenges, consumed before the session | Real |
| Register match and decisions | Real, deterministic; no AI in any decision |
| Officer accept / escalate / release gate; append-only audit; exports | Real |
| Bank | **Synthetic** mock server; test CA |
| Prover location | **Demo**: on the server with a mock session (real use: the CLI command shown on the challenge page) |
| Sign-in | **Demo** role chooser, no passwords |
| Logged-in real-bank FD page | **Not shown** |
| Timing with a remote verifier | **Not measured** |

## Audit changes (29 Oct)

See `docs/AUDIT.md`. Behaviour that changed on purpose:

- **Lien is now required.** A bank page that does not show lien or loan details gives *Insufficient evidence*, not *Verified*. Any proven contradiction still gives *Discrepancy*, even if another field is unknown.
- **Only the AICTE officer** can start or stop services or reset the demo.
- **The home page shows a proof only to someone allowed to see that deposit.**
- **Mock bank → Open at mock bank** takes the principal the bank holds, so a judge can make the bank disagree with the register.

## Limitations

- **Demo sign-in.** No SSO or MFA. Signing in has no CSRF protection.
- **Storage and serving.** SQLite with a single writer. No TLS on the app itself; put it behind HTTPS in any deployment.
- **Challenge token storage.** The token is kept in the database so the institution portal can show it; production should deliver it once.
- **Freshness.** Proofs show the bank's response at the session time only. A field the bank page does not show is reported as unknown.
- **Record-only disclosure.** It cannot prove where the record sat in the response; this is shown on the evidence.
- **TLSNotary build.** It needs TLS 1.2 (P-256, AES-128-GCM) on the bank server. Most Indian net-banking hosts probed accepted this; TLS 1.3-only servers would fail.
- **Limits.** At most 2 concurrent proofs; each proof uploads about 39 MB from prover to verifier (measured on localhost).

## Licences

| Component | Licence |
|---|---|
| TLSNotary | MIT OR Apache-2.0 |
| FastAPI | MIT |
| Uvicorn | BSD-3 |
| Jinja2 | BSD-3 |
| Geist and Instrument Serif fonts | SIL OFL 1.1 (in `app/static/fonts`) |
| Test certificates and data | Synthetic |
