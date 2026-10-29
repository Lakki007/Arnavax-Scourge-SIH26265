# Central result: reproduced, root-caused, fixed (29 Oct 2026)

**Failing case.** I fed the synthetic bank realistic Indian net-banking value formats, ran each through the real prover and verifier, and compared the decision with an answer worked out by hand (`scripts/fresh_cases.py`).

**Before the fix, 11 of 19 cases were wrong** (`1_before_fix.txt`). Three were unsafe:

- `25 Lakh` was read as Rs 25 and gave a **false Discrepancy**.
- Status wording outside `ACTIVE` (for example `OPEN`, `ACTIVE - LIEN MARKED`) was treated as "not active", giving a **false Discrepancy**.
- `25,00,000.00 Dr` (a debit) was read as a deposit balance and gave a **false Verified**.

Eight more were wrong but failed safe: `Rs. 25,00,000.00`, `25,00,000/-` and month-name dates (`15-Jan-2029`) were unreadable. That also hid two real contradictions (an amount really lower, a date really different).

**Trace.**

1. Bank bytes → MPC-TLS → the verifier extracts the field text. This step was correct: the verifier passes the exact bytes.
2. The app's policy reads the text. **The error is here.**
   - `money()` deleted every character except digits, `.` and `-`. So `Rs.` added a dot, `/-` left a dash, `Dr` vanished, and `Lakh` vanished, leaving 25.
   - `norm_date()` knew no month names.
   - Status was a two-way test: in the active list, or an accusation.

**Root cause.** The readers were written against our own mock bank's output format (`2500000.00`, `2029-01-15`, `ACTIVE`). They guessed instead of declining.

**Fix (smallest layer: `app/policy.py`, plus `inactive_values` in the bank schema).**

- Strict readers: an amount must be an optional currency prefix (₹ / Rs / Rs. / INR) followed by a number with or without grouping commas, optionally ending in `/-`.
- Day-first numeric dates and month names are read. Anything else returns unknown.
- Status is three-way:
  - in the declared active list: match;
  - in the declared inactive list: contradiction;
  - anything else: unknown, giving insufficient evidence.
- No threshold was changed, and no answer is cached. Every change makes the reader stricter or adds a format it reads exactly.

**After.** 19 of 19 (`2_after_fix.txt`). On 13 held-out cases written after the fix (`3_holdout_after_fix.txt`), 12 of 13 matched on the first run. The miss: `2.5 Cr` was read as Rs 2.50 (Cr as credit) and gave a false Discrepancy. `Cr` is genuinely ambiguous (credit or crore), so both `Cr` and `Dr` suffixes are now unreadable. All 13 then matched.

**Expectations I changed, and why.**

- `OPEN`: I first expected Verified. No source shows any bank uses it for an active FD, and adding it would tune to my own test. It is now expected as unknown.
- `25,00,000.00CR`: I first expected Verified; it is now unknown, because of the crore ambiguity.

Both changes make the system stricter, not more lenient.

**What this does not show.** Every case is still our synthetic bank. Real banks' field names, formats and status vocabulary are unknown until the field check in `EXPERIMENT_V2.md` (the go/no-go gate). That check has **not been run**: it needs a person's own authorised net-banking login, which this work must never ask for.
