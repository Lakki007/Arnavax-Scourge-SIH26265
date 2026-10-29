# Acceptance criteria: approved MVP (brief v8.1) turned into observable checks

**Scope.** One journey: registered FDR → AICTE challenge → prover → independent verifier → evidence → Accept or Escalate, with a release gate.

**Components.** Four, as approved: `scourge_prove`, `scourge_verifierd`, the app (FastAPI + SQLite), and `scourge_mockbank`.

**Evidence labels:**
- **[R]** real computation;
- **[S]** synthetic bank or test CA;
- **[D]** demo-only convenience.

**Test files:**
- `tests/test_acceptance.py`: HTTP plus the real binaries;
- `tests/test_ui_journey.py`: Playwright, operated like a judge;
- `tests/test_policy.py`: unit tests;
- `cargo test --example scourge_verifierd`: parser unit tests.

## Core journey

| # | Criterion | Observable behaviour | How it is verified |
|---|---|---|---|
| AC1 | The officer can register an FDR, with validation | Invalid fields show inline errors and HTTP 422; nothing is saved. A valid FDR appears as "Submitted by institution" | `test_fdr_form_validation`; screenshot `fdrnew_d.png` |
| AC2 | A bank proof request creates a single-use challenge bound to one FDR and institution, with expiry | A challenge row (`issued`, 15 min) appears; the button disables with a reason while it is open | `test_full_journey` (UI); `test_expired_challenge` |
| AC3 | The institution answers with the **real** MPC-TLS prover; progress reflects actual execution | 10 stage lines appear, each emitted by `scourge_prove` with its own timestamp; nothing is animated without an event | `test_full_journey` (`#stages li` == 10); `journey_3_job.png` |
| AC4 | The verifier is a separate process; it consumes the challenge via the app before the session, and posts facts only | `/internal/challenges/consume` before the TLS session; `/internal/evidence` after; the registry decision is made in Python | `procs.py` (separate PIDs); `test_verifier_down_keeps_challenge_and_retry_works` |
| AC5 | Evidence shows what was authenticated, matched, hidden and not shown | Field table (bank vs register), disclosure map drawn from the real byte ranges, the exact disclosed bytes, measured cost, a limitations list | `evidence_d.png`; `test_record_disclosure_hides_pan_and_other_accounts` |
| AC6 | Accept only for verified evidence; Escalate otherwise; release only after an accepted confirmation within 30 days | Disabled buttons state the reason; the server returns 409 if bypassed | `test_release_gate`; `test_full_journey` (accept → release) |

## Bank outcomes and attacks

| # | Criterion | Observable behaviour | How it is verified |
|---|---|---|---|
| AC7 | A bank-side change changes the outcome (negative controls) | closed / reduced / maturity changed / loan lien → discrepancy; missing status, duplicate key, malformed, redirect, login page → insufficient evidence; unrelated FD → rejected | `test_bank_behaviours` (14 cases); `test_negative_control_bank_shows_loan` (UI) |
| AC8 | Attacks are rejected | Fake bank with the prover's own CA → rejected (certificate); unapproved host → rejected; replayed, wrong-institution or unknown challenge → refused before the session; a proof of another FDR → rejected; a hidden field → insufficient | `test_fake_bank_rejected_on_certificate`, `test_unapproved_host_rejected`, `test_replay_wrong_institution_unknown`, `test_proof_for_another_fdr_rejected`, `test_hidden_field_is_insufficient` |
| AC9 | Failed dependencies are reported, never faked | Verifier down → run fails, challenge stays unused, retry succeeds. Bank down → rejected with reason. Slow bank → cancellable | `test_verifier_down_keeps_challenge_and_retry_works`, `test_bank_down_is_rejected_with_reason_not_faked`, `test_cancel_running_proof`, `test_failed_dependency_and_retry` (UI) |
| AC10 | Malformed input is handled | Malformed form → 422 with messages. Malformed bank data → insufficient evidence. Upload checked by content type and size (2 MB) | `test_fdr_form_validation`, `test_bank_behaviours[malformed]`, `test_baseline_upload_validation` |

## Platform and operations

| # | Criterion | Observable behaviour | How it is verified |
|---|---|---|---|
| AC11 | Permissions are enforced server-side | An institution cannot see another institution's FDR (404) or use officer actions (403). Internal API needs localhost plus a key. CSRF is required | `test_permissions`; `test_full_journey` (institution POST → 403) |
| AC12 | Audit and export | Every action is logged. The events table is append-only (database triggers). CSV and evidence JSON exports | `test_audit_append_only_and_secrets_not_stored` |
| AC13 | Reset and repeat | "Reset demo" restores 4 sample FDRs and the bank state; the journey works again | `test_reset_repeat_cycle`, `test_reset_and_repeat` (UI) |
| AC14 | Secrets are never stored or shown | Bank session cookies are absent from events, evidence and logs. PAN, savings and other FDRs are absent from evidence | `test_audit_append_only_and_secrets_not_stored`, `test_record_disclosure_hides_pan_and_other_accounts` |
| AC15 | The baseline weakness is demonstrable | An uploaded, edited screenshot is "Approved on the document", shown next to the bank-session status | `test_baseline_upload_validation`; `samples/edited_receipt_FD-0000777777.png` |
| AC16 | Accessible and responsive | Keyboard-only path through the hero search; skip link; visible focus; all text ≥ 4.5:1 contrast; no horizontal scroll at 390 px on 9 pages | `test_keyboard_only_home_search`, `test_mobile_layout_no_horizontal_scroll`; contrast table in `VERIFICATION.md` |

## Not covered (stated in the product and the brief)

- **The real-bank gate:** proof of a logged-in FD page at a real bank. **Not shown.**
- **Prover location:** runs on the server in demo mode, not on the institution's machine **[D]**.
- **Sign-in:** demo role chooser without passwords **[D]**. Production needs SSO.
- **Network timing:** with a remote verifier, not measured.
