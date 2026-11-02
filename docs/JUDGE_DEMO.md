# Judge demonstration kit

Every synthetic element is labelled on screen. Say "synthetic bank, real proofs" once and keep to it.

**Live hosted demo:** https://scourge-sih26265.onrender.com (free plan: open it a few minutes early, since the first load after idle takes about a minute; each proof takes 10–30 s there instead of ~1 s locally).

**Backup.** `docs/demo/scourge_demo_replay.mp4` (2 min 14 s). It is a recording of this exact path on the live app, and the top of every frame reads "RECORDED REPLAY · SYNTHETIC BANK, REAL PROOFS". Play it only if the live demo fails, and say so.

## 1. 30-second pitch

> An AICTE security deposit is checked once and then trusted for ten years on a paper receipt. The CBI found joint FDRs encashed or loaned against without AICTE's consent; in Haryana, ₹201 crore of FDRs were not in the bank's records. Scourge lets AICTE ask the bank's own server, at any time, without a bank agreement. The institution logs into its own net banking and proves, cryptographically, what the bank's server says about that one deposit; AICTE's verifier sees only that record, never the password or other accounts. It works end to end on a synthetic bank with real proofs, and the same split-key session already runs against a real bank's logged-in deposits API: 200 OK in 0.4 s.

## 2. Three-minute sequence (live)

**Before judges arrive.**

1. Sign in as the officer.
2. Reset the demo.
3. Open `/how` in a second tab.

| Time | Beat | Do | Say |
|---|---|---|---|
| 0:00–0:20 | Problem and stakes | Home page | "Checked once, trusted for ten years. Fraud surfaces at release or never." Name the CBI and Haryana cases |
| 0:20–0:40 | Simple baseline | **Baseline** → FDR-002 → upload `samples/edited_receipt_FD-0000777777.png` | "An edited screenshot is approved. A portal can only look at the picture" |
| 0:40–0:50 | Set the bank state | **Mock bank** → FD-0000777777 → "Loan raised against the deposit" → Save | "This plays the bank. The verifier never reads this console" |
| 0:50–1:25 | Central mechanism | **Register** → FD-0000777777 → **Request bank proof** → **Switch to INST-42 to answer it** → **Accounts summary** → **Prove from my bank** | Point at the three lanes: "One TLS session, its keys split between the institution and AICTE's verifier, so the institution cannot forge the bank's answer" |
| 1:25–2:00 | Visible evidence | **Open the evidence** (sign back in as the officer if asked) | "The bank's server contradicts the paper." Tab to the Lien row: the loan bytes light up. Point at the black bars: "PAN, savings, other deposits: AICTE never received them" |
| 2:00–2:20 | Useful action | Crumb back to FD-0000777777 → **Escalate** with a note | "Release stays blocked; the bank's evidence is on the record." Register headline: "for 1, the bank's server contradicts the paper" |
| 2:20–2:40 | Never a false Verified | Mock bank FD-0000123456 → "Bank page does not show lien details"; prove FDR-001 | "It shows no loan, but it can't show loans at all, so we say Insufficient evidence, not Verified" |
| 2:40–3:00 | Sponsor value and the real bank | `/how`, then `tools/realbank/RESULTS.md` | "For AICTE: re-check the existing book on demand and before every release, with no bank agreement. And this isn't only a lab: the same split-key session runs against a real bank's logged-in deposits API, 200 OK in 0.4 seconds" |

**Optional, if time allows** (about 10 s): under "Try to cheat", choose **Your own fake bank**. The result is Rejected on the certificate.

## 3. Sample inputs

- **Seeded register.**
  - FDR-001 FD-0000123456 (₹10,00,000)
  - FDR-002 FD-0000777777 (₹25,00,000)
  - FDR-003 FD-0000555555 (INST-77)
  - FDR-004 FD-0000444444
- **Judge's own deposit.**
  1. **Register an FDR**: INST-77, `FD-JUDGE-0001`, `1234567.89`, issue `2025-03-01`, maturity `2031-03-01`.
  2. **Mock bank → Open at mock bank.**
     - Keep `1234567.89` for **Verified**.
     - Type `1000000` for **Discrepancy** on principal and balance; maturity still matches.
- **Invalid register inputs (all refused):**
  - FD number `bad no!`;
  - principal `-5`, `1e5` or `25 Lakh`;
  - issue date `2024-02-30`;
  - a maturity in the past;
  - duplicate `fd-0000123456`.
- **Uploads.**
  - `samples/edited_receipt_FD-0000777777.png` is accepted by the baseline.
  - `samples/not_an_image.png` is refused (checked by content).
  - Anything over 2 MB gets 413.
- **Failure recovery.** System → stop the AICTE verifier → prove: it fails and the challenge stays unused. Start the verifier → retry gives Verified.

## 4. Reset procedure

1. Signed in as the officer, choose **Reset demo** in the bottom bar.
2. It reloads 4 sample FDRs and resets the mock bank, then clears proofs, decisions, the audit, uploads and institution copies.
3. If it says a proof is running, wait up to 90 s.
4. If the app is wedged, run `sudo systemctl restart scourge` (data persists; interrupted runs are cleared), then Reset.

## 5. Likely judge questions and evidence-based answers

**Q: Has this worked on a real bank?**
Yes, at the session level. The split-key session ran against a real bank's production API on 30 Oct and its **logged-in deposits API** on 1 Nov (first bank tested: SBI): certificate verified against public roots, **200 OK in 0.4 s, 61 MB**. The field test also showed that SBI wraps deposit data in a second cipher inside HTTPS; so the deposit fields were **not** read there. An in-browser prover that would read them is planned; only its portal bridge is built and tested. Until then such banks use the weaker, labelled fallback channels. Log: `tools/realbank/RESULTS.md`.

**Q: Isn't this just a screenshot with extra steps?**
No. The TLS session keys are shared with AICTE's verifier, so the institution cannot edit what the bank sent. In the demo, an institution-run "bank" with its own certificate authority is rejected: 14 of 14 in our fresh case book.

**Q: What does AICTE see of our banking?**
Only the method and approved path of the request, the status line and headers, and that one deposit's record. The query string, cookies, PAN and other accounts never leave the institution. Tests assert the officer's page does not contain them.

**Q: What if the bank page doesn't show liens?**
Then we say Insufficient evidence, not Verified. We changed this rule in this phase after testing showed the old rule would have verified a deposit whose loans the page couldn't show.

**Q: How accurate is it?**
On 180 fresh randomised deposits on our synthetic bank: 0 of 119 bad deposits verified, 0 of 61 honest deposits accused. The baseline approved all 180. That shows the logic is consistent on our model of fraud. It is not a real-world accuracy figure.

**Q: Couldn't the institution show a different deposit, or an old page?**
The FD number must equal the challenge's. The challenge is single use, expires in 15 minutes, and is consumed before the bank session starts. A replayed challenge is refused (tested).

**Q: Is this legal? Do bank terms allow it?**
The institution uses its own authorised session to look at its own deposit, the way it already does in its browser. It never shares its password or OTP, nothing bypasses bank controls, and only the one record reaches AICTE, with consent recorded per proof. AICTE can make bank proofs a condition of approval by circular; we'd take the bank-terms point through AICTE's legal review during the pilot (`docs/FEASIBILITY.md`, section 2).

**Q: What does it cost?**
Software: ₹0 in licences (TLSNotary MIT/Apache-2.0). One small VM runs the verifier. Each proof moves about 60 MB between the institution and AICTE's verifier, measured on the real bank (61 MB) and against public roots (`tools/realbank/BUDGETS.md`); 10,000 deposits checked quarterly is about 2.4 TB a year.

**Q: Why not Account Aggregator or NeSL?**
Account Aggregator needs AICTE to be a financial information user (FIU), which it isn't. NeSL e-FDR covers new instruments. We cover the existing book.

**Q: What if a bank uses TLS 1.3 only?**
This TLSNotary build needs TLS 1.2 on the bank's server. Most Indian hosts we probed accepted it; a TLS 1.3-only bank is unsupported today.

**Q: What else does the problem statement ask for?**
Our MVP covers:

- verification and discrepancy;
- the release gate;
- escalation;
- an audit trail;
- two roles.

Reminders, renewals, dashboards and full RBAC are on the roadmap slide. We cut them to make the verification real.
