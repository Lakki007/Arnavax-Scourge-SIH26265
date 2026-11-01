# Security controls: control → code → test

Every row is enforced server-side and has an automated test. File paths are relative to the repo root. Tests named `test_acceptance.py::…` drive the running app and the real proof binaries over HTTP; the others need no server.

## Bank evidence

| Control | Code | Test |
|---|---|---|
| Proof comes from the bank's TLS session; the institution cannot alter a byte (MPC-TLS, verifier co-holds keys) | `rust/` (scourge_prove, scourge_verifierd), `/internal/*` in `app/main.py` | `test_acceptance.py::test_lien_semantics_through_real_proofs`, `test_ui_journey.py::test_full_journey` |
| Fake bank (prover's own CA) rejected | verifier trust roots | `test_fake_bank_rejected_on_certificate`, `test_attack_fake_bank_rejected` |
| Valid certificate but unapproved host rejected | verifier allow-list, `config/bank_directory.json` | `test_unapproved_host_rejected` |
| Challenge single-use, expiring, bound to institution and FDR | `app/main.py` challenge consume | `test_replay_wrong_institution_unknown`, `test_expired_challenge`, `test_proof_for_another_fdr_rejected` |
| Only the deposit's record is disclosed; PAN and other accounts hidden | selective disclosure in prover; privacy guard | `test_record_disclosure_hides_pan_and_other_accounts`, `test_body_disclosure_privacy_guard` |
| Lien read exactly per bank; unknown wording never Verified; missing lien is Insufficient | `app/policy.py`, `lien_rules` in `config/scourge.json` | `test_policy.py::test_v01…test_v11`, `test_lien_not_shown_is_unknown_not_clear` |
| Money compared in integer paise; holders compared whole | `app/policy.py` | `test_money_is_exact_paise`, `test_holder_is_compared_whole` |
| Fallback evidence cannot override a proven discrepancy | e-advice / branch tiers | `test_branch_cannot_override_session_discrepancy`, `test_forged_or_altered_eadvice_is_rejected` |

## Identity and access

| Control | Code | Test |
|---|---|---|
| Named accounts, scrypt (n=2^14), 12+ character passwords, lockout after 5 failures | `app/accounts.py`, `/signin/password` | `test_password_hashing_and_strength`, `test_named_accounts_password_signin_and_lockout` |
| **Officer 2FA (TOTP, RFC 6238) mandatory in production** (`SCOURGE_AUTH=password`); demo role chooser refused | `MFA_REQUIRED` in `app/main.py`, `scripts/create_account.py` enrols at creation | `test_units.py::test_mfa_mandatory_for_officers_in_password_mode`, `test_two_factor_signin_lockout_and_step_up` |
| Fresh 2FA code (step-up) to approve a release | `step_up()` | `test_two_factor_signin_lockout_and_step_up` |
| Replayed TOTP code refused | `app/totp.py` | `test_totp_verify_window_and_replay` |
| Signed session cookies, CSRF token on every POST, safe redirects | `app/auth.py` | `test_signin_next_cannot_redirect_off_site`, `test_permissions` |
| Institutions see only their own deposits and proofs | `can_see_fdr()` | `test_no_proof_bytes_for_anonymous_or_other_institution`, `test_permissions` |
| Only officers control services and reset | role checks | `test_only_officer_controls_verifier_and_reset` |

## Release (separation of duties)

| Control | Code | Test |
|---|---|---|
| Only the scrutiny officer accepts evidence and proposes | `PROPOSERS` | `test_only_scrutiny_officer_accepts_and_proposes` |
| Each department reviewer clears only its own department, with reason and memo from this case | `OFFICER_DEPT` | `test_p01`–`test_p04` |
| Two different officers sign; second signer above ₹1 crore | `APPROVERS` | `test_release_needs_two_different_officers`, `test_p05…`, `test_clearance_objection_blocks_and_second_signer_above_one_crore` |
| Approval is atomic and version-checked; a failure mid-way rolls back | `db.tx`, `fp:count` | `test_g03`, `test_g05_g06…`, `test_g07_g08…`, `test_approval_version_is_mandatory` |
| Issued is not completed; completion needs a matching, new bank acknowledgement | `COMPLETERS` | `test_c01`–`test_c06`, `test_acknowledgement_must_name_case_or_be_attested` |
| Adverse bank evidence after issue blocks completion; stale confirmation blocks approval | gates | `test_adverse_bank_evidence_after_issue_blocks_completion`, `test_stale_confirmation_blocks_release` |
| One outstanding instruction; revoke shows on public verification | `/release/revoke`, `/verify` | `test_one_outstanding_release_and_revoke_to_supersede`, `test_revoked_letter_fails_public_verification` |
| Release letter signed (Ed25519), verifiable offline and by QR | release signing key | `test_release_letter_is_signed_and_verifiable_offline`, `test_release_letter_qr_and_public_verify` |

## Data protection and integrity

| Control | Code | Test |
|---|---|---|
| Documents encrypted at rest (AES-256-GCM, per-file key wrapped by master key); tamper detected | `app/vault.py` | `test_vault_roundtrip_and_tamper_detection`, `test_document_vault_encrypts_checks_and_detects_tampering` |
| Uploads checked on content (PDF active content refused, magic bytes) | `vault.check()` | `test_vault_content_checks`, `test_baseline_upload_validation` |
| Append-only, hash-chained audit log | `db.event`, `verify_chain()` | `test_audit_log_is_hash_chained_and_tamper_evident`, `test_chain_hash_is_deterministic_and_sensitive` |
| No secrets in the audit log or database | | `test_audit_append_only_and_secrets_not_stored` |
| Spreadsheet formula injection neutralised in exports | `app/reports.py` | `test_formula_injection_neutralised` |
| Oversized requests refused before reading | middleware | `test_oversized_request_refused_before_reading` |
| Backup archive checksummed; restore checks DB integrity, audit chain and that every vault document decrypts; never overwrites | `scripts/backup.py` | `test_backup.py::test_backup_restore_roundtrip_and_tamper` |

## Known limits (stated, not hidden)

- The demo bank is synthetic. At a real bank, the live session is proven (first bank tested: SBI); the account data is not yet. See `FEASIBILITY.md`.
- No AICTE single sign-on integration yet; named accounts are the production path today.
- SQLite with a single writer; adequate for one regulator instance (see sizing in `FEASIBILITY.md`), PostgreSQL is the scale-out path.
- Insider fraud inside the bank's own database is out of scope for any evidence that comes from the bank.
