# Adversarial audit, 29 Oct 2026

**Method.** I assumed a judge who ignores the scripted path. The audit drove the running app (real prover, separate verifier, synthetic bank) with:

- an HTTP probe;
- a link and button crawler for four roles (anonymous, officer, INST-42, INST-77);
- browser checks for refresh, direct links and slow responses.

**Result.** Every consequential failure is now fixed and has a regression test. Suite: **59 passed** (`python3 -m pytest -q tests/`). Screenshot sweep: no console errors or 5xx.

## Found and fixed

| # | Finding | How it was found | Fix | Regression test |
|---|---|---|---|---|
| 1 | **Core promise.** The bank page hid lien/loan details, yet the result was **Verified**. That could lead to accepting and releasing a deposit with a hidden loan. | Ran every bank behaviour; `no_lien` gave *verified*. | `lien` is required; an unknown required field gives *Insufficient evidence*, with a plain reason. | `test_lien_not_shown_is_unknown_not_clear`, `test_lien_absent_is_unknown_not_clear` |
| 2 | **Wrong precedence.** After fix 1, a *closed* deposit (whose page drops the lien) became *Insufficient* instead of *Discrepancy*. | The full suite caught it. | A proven contradiction now outranks an unknown. | `test_bank_behaviours[closed]`, `test_contradiction_outranks_unknown` |
| 3 | **Data exposure.** Anonymous visitors, and other institutions, saw the latest proof's disclosed FD record on the home page, with a link to evidence they could not open. | HTTP probe and crawler: INST-77 got a 404 link. | The home page shows proofs only to roles allowed to see that FDR. | `test_no_proof_bytes_for_anonymous_or_other_institution` |
| 4 | **Privilege.** Any institution could stop AICTE's verifier, or reset the whole demo for everyone. | HTTP probe: 303 for INST-77. | `/system/service` and `/system/reset` are officer-only; the controls are hidden for other roles. | `test_only_officer_controls_verifier_and_reset` |
| 5 | **Race.** Concurrent "Prove" clicks could start two provers on one challenge. The second failed confusingly. | 4 parallel posts gave two 303s. | Check-and-start happens under the DB lock. | `test_concurrent_prove_clicks_start_one_prover` |
| 6 | **Stale state.** During a running proof, the challenge page said "already used, see the last run". | Browser: reload mid-run against a slow bank. | The page shows "running now" with a link to the live run. | `test_challenge_page_mid_run_says_running_not_used` |
| 7 | **Input could not change the bank's answer.** "Open at mock bank" copied the register's principal, so a judge's own FDR always agreed. | Read the code path from mock bank open to bank state. | The officer enters the principal the bank holds (default: the register's). The input is validated. | `test_input_drives_output_bank_principal_differs`, `test_bank_principal_input_validated` |
| 8 | **Misleading reason.** When the institution's prover refused to disclose two deposits, the institution saw only "TLS proof failed". | Behaviour sweep (`two_fdrs`). | The institution sees "Your prover stopped: privacy guard…". AICTE's record still shows only what the verifier saw. | `test_body_disclosure_privacy_guard` |
| 9 | **Raw JSON errors.** Malformed IDs (`/evidence/abc`) returned raw FastAPI JSON. | HTTP probe. | Handler renders the normal error page (422). | `test_malformed_ids_render_error_page` |
| 10 | **Overstated label.** The comparison headed "The paper says" also listed status, holder and lien. Those are AICTE's requirements, not paper values. | Traced each claim to its source. | Header now says "Amounts and dates from the register; status, holder and lien are what AICTE requires". | (template text) |
| 11 | **Missing customer.** Opening a deposit for an institution with no bank customer was a KeyError. | Code trace. | It now returns 409 with a message. | — |

## Checked and already correct

- **Cross-institution access:** INST-77 gets 404 on INST-42's challenges, FDRs, evidence, exports, receipt fragments and prove actions, and 403 on its bank deposits.
- **CSRF:** missing on a decision gives 403. Unknown actions give 400; missing fields give 422.
- **Forged evidence:** posting without the internal key gives 403.
- **Register form:** rejects bad dates (`2024-02-30`), duplicates differing only in case or spacing, and principals `1e5`, `0.001` and `99999999999`. Accepts `₹750000` and ` 7,50,000 `.
- **Reflected input:** search input is escaped (no reflected markup).
- **Decision gates:**
  - repeated accept → 409;
  - accept on superseded evidence → 409;
  - release when the confirmation is older than 30 days → 409, with status *Confirmation expired*;
  - release after a later contradicting proof → 409.
- **Live job page:** survives a reload mid-run, the officer can open it by direct link, and the slow bank (8 s) finishes correctly.
- **Metrics:** elapsed time and MB come from the verifier process (`verifierd.rs`). None are hard-coded.
- **Register headline counts:** computed from the database on every request.

## Removing the mechanism

| Removed or broken | Outcome |
|---|---|
| Verifier stopped | Proof fails, the challenge stays unused, no evidence is recorded; a retry after restart is *Verified*. |
| Prover's own CA (fake bank saying ACTIVE) | *Rejected*: the certificate does not chain to the verifier's roots. |
| Host not on AICTE's list | *Rejected*. |
| Institution withholds the lien bytes | *Insufficient evidence*. |
| No mechanism at all (baseline upload) | The edited image is *Approved*; the same deposit, proven from the bank, is a *Discrepancy*. |

## Not run / remaining

- **Real bank:** a proof of a logged-in page at a real bank is not run; this is the open go/no-go gate.
- **Network timing:** with the verifier on another machine, not measured.
- **Load:** beyond 2 concurrent proofs, not tested.
- **Assistive tech:** no screen-reader pass (only keyboard focus and contrast were checked).
- **Real time:** expiry beyond 30 days is simulated by back-dating `received_at` in the database.
- **Demo sign-in:** anyone can choose any role, so the role restrictions show the design, not real access control.
