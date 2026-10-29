# Phase 4: judge-readiness verification (29 Oct 2026)

**Release:** `v1.0.0-rc1`, `scourge_prototype.zip` (sha256 in `docs/RELEASE.txt`).
**Environment:** Linux x86_64, 2 vCPU, 8 GB RAM, Python 3.13, everything on one machine.

**Labels.** [Measured] we ran it here. [Estimate] our reasoning, not measured. [Not run] not done.

## 1. Every claim, by dimension

Passing one dimension does not establish another.

| Claim (brief v9 / pitch) | Dimension | Implementation | Evidence | Status |
|---|---|---|---|---|
| The institution proves what the bank's server says; AICTE's separate verifier sees only the FD record | Correctness | `scourge_prove`, `scourge_verifierd` (TLSNotary MPC-TLS), `/internal/*` | 100 tests; `test_record_disclosure_hides_pan_and_other_accounts`; officer page HTML lacks the PAN (UI test) | [Measured] on the synthetic bank |
| Fake bank, unapproved host, withheld fields and replayed or expired challenges are refused | Correctness | Verifier trust roots, allow-list, challenge consume | Attack tests; case book: 14 of 14 fake-bank cases rejected, 17 of 17 hidden-loan cases insufficient | [Measured] |
| A field the bank page does not show is unknown, never clear | Correctness | `app/policy.py` (lien required; three-way status; strict readers) | 19 of 19 fresh format cases, 13 of 13 held-out cases, reader unit tests | [Measured]. Changed in this phase; see section 5 |
| Release only with an accepted bank confirmation under 30 days old | Correctness | `action_gates`, `fdr_status` | Release-gate, stale and later-contradiction tests | [Measured] |
| Turnaround from weeks to minutes | Real-world impact | — | Proof takes 0.6 s on loopback. Real-bank timing, a remote verifier and the institution's own effort are not measured | **Target only** |
| Works without a bank API or agreement | Real-world impact | — | Public bank pages: 13 of 16 sessions completed across 6 Indian bank hosts (`p3/real_probe_results.jsonl`). Those are login and redirect pages, **not FD pages** | **Unshown** for FD data (gate K2) |
| No rival SIH26265 build uses a bank-session proof | Differentiation | — | 8 rival READMEs saved in `comp/` (the brief said "roughly 12"; only 8 are retained) | [Measured] on 8 only. **Re-check before submission** |
| Interface drawn from the references; AA contrast; keyboard; 390 px | Visual polish | Templates, CSS | Screenshot sweep with no console errors; keyboard and mobile tests | [Measured]. Two cosmetic issues remain (section 7) |
| About 1 s per proof, 28–60 MB per proof | Performance | — | 0.58 s p50 and 38.7 MB per proof, with 1–2 KB request budgets on loopback | [Measured], same machine only |

## 2. Core against the baseline, on fresh cases

**Method.** `scripts/casebook.py` generated 180 new deposits with seeds 2026, 7 and 11. Each had:

- random amounts, maturities, institutions and FD numbers;
- random bank display formats (Rs., ₹, INR, `/-`, month-name dates);
- a known true state.

Each case went through the **real** baseline upload and the **real** prover and verifier.

| True state | n | Scourge | Baseline |
|---|---|---|---|
| Honest, readable | 44 | verified 44 | approved |
| Honest, bank shows "25 Lakh" | 17 | insufficient evidence 17 | approved |
| Forged (no such deposit at the bank) | 15 | insufficient evidence 15 | approved |
| Closed | 22 | discrepancy 22 | approved |
| Partly withdrawn | 24 | discrepancy 24 | approved |
| Loan raised | 22 | discrepancy 22 | approved |
| Maturity changed | 5 | discrepancy 5 | approved |
| Attacker's own-CA bank | 14 | rejected 14 | approved |
| Institution hides the loan bytes | 17 | insufficient evidence 17 | approved |

**Results.**

- **Scourge:** verified 0 of 119 bad deposits and accused 0 of 61 honest ones.
- **Baseline:** approved 180 of 180.
- The baseline approves everything by design. It is a deliberately weak comparison, not a fair competitor.

### What this establishes

- On a bank whose behaviour we wrote, the mechanism and the decision rules are consistent with ground truth across random values and the listed formats.
- The cryptographic refusals (fake CA, withheld bytes) work on every case tried.

### What it does not establish

- **Evaluation leakage.** The bad states are exactly the mock bank's built-in behaviours. The run tests consistency with our own model of fraud, not the discovery of fraud we did not anticipate.
- **Invalid assumptions.** Real banks' field names, formats, status words and whether they show liens are unknown. The readers were fitted to formats we chose, partly after seeing failures (`docs/central_result/`).
- **Misleading denominators.** The mix of states is ours: 119 of 180 bad cases does not reflect any real prevalence. Read the results per row, not as an overall accuracy figure.
- **Unsupported confidence.** "0 false accusations in 61" is synthetic. It says nothing about a real bank's page.
- **Generator defect found and fixed.** The first run of seed 7 showed one "honest" case marked Discrepancy. The generator had written the bank's amount without its 25 paise, so the bank really did show less. After fixing the generator, the same seed gave 0 accusations.
- **Not tested against a real bank.** A logged-in real-bank FD page has never been proven. That is the go/no-go gate (EXPERIMENT_V2.md). **Not run**, because it needs a person's own authorised net-banking login.

**Does the result depend on the scripted demonstration?** No. None of these 180 deposits is a demo deposit, and the formats were drawn at random. It still depends on the synthetic bank.

## 3. Product exercise

**[Measured]: suite and journeys**

- Local, default mode: 100 passed, 2 skipped. The skipped tests cover the deployment gate.
- Staged HTTPS deployment: 102 passed, 0 skipped.
- Central journey: the browser test passes, and the 2 min 14 s recorded replay was made from the live app.
- Persistence: survives `kill -9` and restart (confirmed record, evidence, audit, then release).
- Restart during a proof: the run is marked interrupted. Before this fix it stayed "running" forever and blocked reset.
- Upgrade and rollback rehearsed both ways on shared data.

**[Measured]: inputs**

- Uploads: 60 MB is refused before it is read. Non-images are refused by content. Empty, 2 MB-boundary and PDF uploads were covered earlier.
- Large register: 5,000 FDRs.
  - Before: 342 ms and a 2.2 MB page.
  - After pagination: 136 ms and 93 KB.
  - Headline counts still cover the whole book.
- Malformed IDs and parameters return the error page (422).

**[Measured]: layout and access**

- Mobile at 390 px: no horizontal page scroll. Wide tables scroll inside their own box.
- Keyboard-only home search works.
- The claim-to-bytes trace works with keyboard focus.
- Roles: cross-institution access returns 404. Officer-only actions return 403 for institutions.

## 4. Security and operations

| Area | Finding | Action |
|---|---|---|
| Secrets | Session and internal keys are generated per install with mode 0600. No token, cookie, PAN or session ID appears in the logs, audit, evidence or job records (scanned) | — |
| Data permissions | Database, uploads, logs and institution copies were 644/755 | **Fixed:** `umask 077` plus `chmod go-rwx` at start |
| Public exposure | Anyone could choose "AICTE officer" and reset or stop services | **Fixed:** optional `SCOURGE_ACCESS_CODE` (constant-time compare, 0.5 s delay on failure) |
| Cookies over HTTPS | Not marked Secure | **Fixed:** `SCOURGE_COOKIE_SECURE=1` makes them Secure |
| Open redirect | `next=//evil.example` after sign-in | **Fixed**, with a regression test |
| Internal API | Allowed from loopback with the key only | nginx also returns 404 for `/internal/` |
| Binding to a public address | The new host override broke **every** proof: the verifier reports to 127.0.0.1:8080 | **Fixed:** the app always binds loopback; nginx publishes it |
| Resource limits | No body-size limit | **Fixed:** 2.25 MB cap (413) in the app and nginx. Also: 2 concurrent proofs, 90 s prover timeout, 15-minute challenges |
| Python dependencies | Unpinned | **Fixed:** pinned in `requirements.txt`; pip-audit found no known vulnerabilities |
| Rust dependencies | cargo-audit: 20 advisories, including certificate-path issues in rustls-webpki and the emSign root distrust | **Fixed 12** by in-range updates (rustls 0.23.46, rustls-webpki 0.103.16, webpki-roots 1.0.11, h2, crypto-bigint 0.7.10 and others). Rebuilt; all tests rerun |
| Rust dependencies, remaining | 8 remain. 7 are in rustls 0.22 / webpki 0.102, used only by TLSNotary's **test-server fixtures**, not by the prover or verifier. 1 is crypto-bigint 0.5 in `tlsn-attestation` k256 signing, which the verifier does not use | **Accepted;** needs an upstream TLSNotary upgrade |
| Debug endpoints | OpenAPI and docs disabled; health endpoint shows service up/down only | — |
| Error disclosure | Unhandled errors return a plain 500 without a traceback | — |
| Sign-in | Still a demo role chooser, not real authentication | **Open by design.** Documented |
| Prover command line | The challenge token is visible in the server's process list during demo-mode proofs | **Open.** Demo-only; real provers run on the institution's machine |

## 5. Deliberate decision-rule changes since brief v9

1. **Lien is required.** A bank page without lien details gives Insufficient evidence, not Verified.
2. **A proven contradiction outranks an unknown.** For example, a closed deposit whose page drops the lien is a Discrepancy.
3. **Strict value readers.** Unreadable text is unknown. "Cr" and "Dr" suffixes are refused because "Cr" can mean credit or crore.
4. **Three-way status.** The bank schema now declares inactive words, and anything unlisted is unknown.

## 6. Measurements [Measured, same machine, verifier on loopback, synthetic bank]

| Metric | Value |
|---|---|
| Proof time (verifier's view) | p50 0.58 s, p95 0.62 s (n=60) |
| Prover to verifier traffic | 38.7 MB per proof (request budget 2 KB, response budget 16 KB) |
| Peak memory | Verifier 274 MB, prover 189 MB, app 64 MB, mock bank 5 MB |
| Page latency, 64 FDRs | Register 6.9 ms, FDR 2.7 ms, evidence 2.3 ms, audit 5.0 ms (p50 of 30) |
| Register, 5,000 FDRs | 136 ms (paginated) |
| Clean install | Pinned Python dependencies in 2.9 s; Rust rebuild 1 min 45 s (cached) |

**[Estimate] Operating cost.**

- One 2 vCPU / 4 GB VM runs the demo. Two concurrent proofs need about 1 GB at peak.
- In real use, each proof uploads about 40 MB from the institution to AICTE's verifier, roughly 40 GB per 1,000 proofs.
- Inbound traffic is usually not billed; check the chosen provider.
- No provider price is quoted here, because none was checked.

**[Not run].**

- Remote-verifier timing.
- Concurrency above 2 proofs.
- A screen-reader pass.
- A real-bank FD page.
- A public deployment (section 8).

## 7. Remaining risks, by consequence

1. **The real-bank gate has not been run.** The whole value depends on it (K1/K2). If no proof-eligible FD response exists on 2 of 3 banks, the product reverts to the baseline. **Next action:** the field check with a team member's own authorised account.
2. **Legal and terms of use.** Bank terms and AICTE's acceptance of this evidence are unconfirmed.
3. **Demo sign-in.** Acceptable for judging; not for real data.
4. **The rival scan is 8 READMEs from earlier.** Re-check before claiming "no rival".
5. **Cosmetic.**
   - On mobile, the register's "Vouched for by" column sits off-screen until you scroll the table.
   - The fixed demo bar can cover the last line of a page in full-page screenshots.

## 8. Deployment

- **Release.** Built and verified from a clean unzip: checksums, pinned venv, fresh data.
- **Staged.** On the network address behind nginx with TLS, the access code and Secure cookies on. The full suite passes against that URL (102 of 102). Rollback rehearsed.
- **Public URL: not deployed.** No authorised hosting target, domain or credentials were provided, and this workspace cannot publish to the internet. See `docs/RUNBOOK.md` for the exact final step.
