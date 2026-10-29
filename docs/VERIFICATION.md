# Verification evidence (28 Oct 2026, this environment: Linux x86_64, 2 vCPU, Python 3.13.18, Rust 1.95.0, Chromium via Playwright)

## Commands actually executed

| Command | Result |
|---|---|
| `./scripts/setup_certs.sh` | 3 server certificates and 2 CAs generated (synthetic) |
| `./scripts/build_rust.sh` (clean checkout of TLSNotary `eb19c82`) | Built `scourge_prove`, `scourge_verifierd`, `scourge_mockbank` in 4 min 38 s; parser unit tests: 6 passed. These binaries are the ones in `bin/` |
| `./scripts/run.sh` | App on :8080 and all 4 supervised services healthy: `{"verifier":true,"genuine":true,"fake":true,"unlisted":true}` |
| `python3 -m pytest -v tests/` | **48 passed** in 56.6 s (`docs/test_run.txt`) |
| `python3 scripts/shots.py var/shots` | 24 pages, desktop and mobile: "no console errors / 5xx" (`docs/screenshots/`) |

## Required checks

| Check | Where | Observed |
|---|---|---|
| Fresh valid input | `test_full_journey` (browser) | Register → request → prove (10 real stages, ~0.9 s) → Verified → Accept → Release |
| Benign / negative control | `test_bank_behaviours[closed/reduced/maturity_changed/lien_loan]`; `test_negative_control_bank_shows_loan` | Discrepancy with the mismatching field named; PAN, savings and other FDR not disclosed |
| Malformed input | `test_fdr_form_validation`, `test_baseline_upload_validation`, `test_bank_behaviours[malformed]` | 422 with field messages; uploads rejected by content and size; malformed bank data → insufficient evidence |
| Failed dependency | `test_verifier_down_keeps_challenge_and_retry_works`, `test_bank_down_is_rejected_with_reason_not_faked`, `test_failed_dependency_and_retry` (browser) | Verifier down: run fails, challenge stays unused, retry after restart → Verified. Bank down: Rejected with reason. Never a fabricated success |
| Reset and repeat | `test_reset_repeat_cycle`, `test_reset_and_repeat` (browser) | Register back to 4 sample FDRs; the journey runs again |
| Attacks | `test_fake_bank_rejected_on_certificate`, `test_unapproved_host_rejected`, `test_replay_wrong_institution_unknown`, `test_proof_for_another_fdr_rejected`, `test_hidden_field_is_insufficient`, `test_body_disclosure_privacy_guard` | All rejected or insufficient, as specified |
| Cancellation | `test_cancel_running_proof` (slow bank) | Cancelled |
| Permissions / CSRF / internal API | `test_permissions` | 404 / 403 as specified |
| Secrets | `test_audit_append_only_and_secrets_not_stored` | Bank session tokens absent from events, evidence and logs; events table rejects UPDATE/DELETE |
| Keyboard, mobile | `test_keyboard_only_home_search`, `test_mobile_layout_no_horizontal_scroll` | Skip link first in tab order; hero search works by keyboard; no horizontal scroll at 390 px on 9 pages |

## Contrast (computed WCAG ratios on #000 / panel #0f0f11)

| Colour | Use | On #000 | On panel |
|---|---|---|---|
| `#8a8a93` | muted text | 6.1 | 5.6 |
| `#a3a3aa` | secondary text | 8.4 | 7.6 |
| `#a6f3d2` | mint, verified | 16.4 | 14.9 |
| `#ff8a6b` | coral, discrepancy | 9.1 | 8.3 |
| `#ffcf70` | amber, insufficient | 14.4 | 13.1 |
| `#ff6b8b` | rose, rejected | 7.7 | 7.0 |
| `#8b6cff` | violet | 5.7 | 5.2 |

## Bugs found during verification and fixed

- **Register page 500:** a template variable shadowed the status macro.
- **Hidden spinner still visible:** CSS overrode the `hidden` attribute.
- **Long values clipped:** values in evidence tables were cut off because cells did not wrap.
- **Mobile nav and hero:** nav labels wrapped, the brand chip overflowed, and the demo bar was too large on phones.
- **Contrast:** muted text was below 4.5:1.
- **Hero glow:** it was clipped to a rectangle.
- **Test fixture:** it held a SQLite write lock, which caused app 500s during that test. The fixture was fixed.
- **Journey test:** the test depended on the previous test's sign-in state.

## Not verifiable here (stated honestly)

- **Real bank:** proof of a logged-in FD page at a real bank. Needs an authorised account; see `EXPERIMENT_V2.md`.
- **Network timing:** with the verifier on another machine.
- **Browsers:** behaviour in browsers other than Chromium.
- **Screen readers:** testing with assistive technology.
- **Load:** beyond 2 concurrent proofs.
