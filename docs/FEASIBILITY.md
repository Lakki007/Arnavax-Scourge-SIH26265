# Feasibility: bank coverage, legal basis, cost and operations

Labels: **[Measured]** we ran it. **[Estimate]** our arithmetic, not measured. **[Open]** not done yet.

## 1. What if a bank's page can't be proven?

Scourge never needs every bank to work on day one. Each deposit is checked through the strongest tier available for its bank, and the tier is shown on the evidence:

| Tier | Evidence | When | Status |
|---|---|---|---|
| 1. Bank-session proof | MPC-TLS proof of the bank's own response, from the institution's net banking | Bank page shows the FD and its lien, TLS 1.2 is supported | Workflow [Measured] on synthetic bank; live real-bank session [Measured] (first bank tested: SBI); FD data at a real bank [Open] |
| 2. Signed bank e-advice | Bank-signed advice checked against the bank's registered key; old, altered or wrong-FD advice refused | Bank issues e-advice / signed statements | [Measured] in tests (`test_eadvice_*`) |
| 3. Branch confirmation | Signed, expiring, version-bound link to the branch's registered contact; structured lien answer | Everything else | [Measured] in tests (`test_branch_confirmation_tier3`) |

Rules that hold across tiers: a lower tier can **fill in** a field a proof could not show (e.g. lien), but can never **override** a proven discrepancy (`test_branch_cannot_override_session_discrepancy`). A deposit with no adequate evidence stays *Insufficient evidence*, never *Verified*.

**Why real-bank FD data is not proven yet, and the fix.** On 30 Oct 2026 the split-key session with a real bank's production API completed (certificate verified against public roots, HTTP 200, about 1 s, ~357 MB traffic). The request was a replay of the browser's own call, and the bank answered without account data — most likely because it refuses replayed requests. The fix is a browser-extension prover that notarises the browser's original request (TLSNotary already ships one), plus disclosure of the per-session app-layer key for banks that encrypt payloads inside HTTPS. Raw output: `tools/realbank/RESULTS.md`.

**Onboarding a bank** is configuration, not code: host allow-list, field paths, and the bank's exact lien wordings (`config/bank_directory.json`, `lien_rules` in `config/scourge.json`). Unknown wording fails closed.

## 2. Legal and consent position

*We are not lawyers; this is the position we would take to AICTE's legal review.* [Open]

- **No bank agreement or API is needed for tier 1.** The institution signs in to its own net banking, as its own customer, and looks at its own deposit. It never shares its password or OTP with AICTE or Scourge; the verifier only co-holds TLS session keys and sees the disclosed record.
- **Consent is explicit and per deposit.** Each proof starts from a single-use challenge the institution opens and accepts; it is recorded in the audit log.
- **Data minimisation.** Only the FD record is disclosed; PAN and other accounts stay hidden (`test_record_disclosure_hides_pan_and_other_accounts`). This fits the purpose-limitation and minimisation principles of the DPDP Act 2023, though institution account data is largely not personal data.
- **AICTE already has the right to this information.** The approval process requires the deposit to be pledged to AICTE; Scourge changes how it is checked, not what is asked.
- **Open points for review:** each bank's net-banking terms on automated access; whether AICTE's handbook should name bank-session proof as an accepted evidence form; retention period for proofs.

## 3. Cost at scale [Estimate]

Inputs: ~357 MB prover↔verifier traffic and ~1 s per proof (real-bank run); 2 concurrent proofs per verifier process on 2 vCPU (prototype setting).

| Deposits checked | Checks per year | Traffic per year | Verifier time |
|---|---|---|---|
| 10,000 | 4 (quarterly) = 40,000 | ~14 TB | ~11 CPU-hours of proof time; one small VM handles the peak if checks are spread over a week |

- Traffic is mostly upload from the institution to AICTE's verifier; inbound traffic is free on major clouds and on NIC networks. The institution's upload is ~357 MB per check, about one HD video.
- Software licences: ₹0 (TLSNotary MIT/Apache-2.0; FastAPI, SQLite).
- Hosting: one small server (2 vCPU, 8 GB) for the portal and one verifier; add verifier processes linearly.
- Tier 3 costs officer and branch time only when tiers 1–2 are unavailable.

## 4. Deployment and failure recovery

| Concern | How | Evidence |
|---|---|---|
| Reproducible deployment | `Dockerfile`, `docker-compose.yml` (persistent volume, health check, `restart: unless-stopped`), `render.yaml` | CI job `container`: compose up, health, backup + verify, restart |
| Verifier or bank down | Proof fails with a reason; challenge stays unused; retry works | `test_verifier_down_keeps_challenge_and_retry_works`, `test_bank_down_is_rejected_with_reason_not_faked`, `test_failed_dependency_and_retry` |
| Crash during release approval | One transaction; everything rolls back | `test_g07_g08_failure_mid_approval_rolls_everything_back` |
| Orphaned jobs after restart | Do not block reset or new proofs | `test_orphaned_running_job_does_not_block_reset` |
| E-mail outage | Retries, then marked failed and visible | `test_mailer_sends_retries_and_fails` |
| Data loss | `scripts/backup.py create` (online SQLite backup + vault + keys), `verify` and `restore` check integrity, audit chain and that every vault document decrypts; `docker compose run --rm backup` | `test_backup.py::test_backup_restore_roundtrip_and_tamper` |
| Production identities | `SCOURGE_AUTH=password`: named accounts, officer 2FA mandatory, enrolled at creation (`scripts/create_account.py`) | `test_mfa_mandatory_for_officers_in_password_mode` |

## 5. Pilot path

1. ✓ Prototype with the full lifecycle (release types, settlement, regional scoping) and 266 automated tests.
2. ✓ Live real-bank MPC-TLS session (first bank tested: SBI).
3. Browser-extension prover → FD data proven at 3 large banks. [Open]
4. One AICTE regional office, tiers 1–3 together, against its current register. [Open]
5. National rollout. [Open]
