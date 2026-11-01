# In-browser prover: reading app-encrypted banks

*How Scourge reads deposit records from banks that encrypt them inside HTTPS, and how it plugs into the portal we already have. Designed from our 1 Nov 2026 field test on a real bank's logged-in deposits API (`tools/realbank/RESULTS.md`).*

## Two provers, every bank

Scourge has two ways to prove a deposit, and picks the one that fits the bank:

- **Command-line prover** (`bin/real_bank_probe`): proves the deposit request inside a split-key TLS session. Ideal for banks whose deposit page returns the record directly.
- **In-browser prover** (this design): runs the same split-key proof from inside the browser tab, on the request the page itself makes. Built for banks that add their own encryption **inside** HTTPS.

Our field test on 1 Nov 2026, on a team member's own ₹1,000 FD at SBI, gave us exactly the data to design the second one:

- the split-key session reaches SBI's **logged-in deposits API** and SBI returns **HTTP 200 OK in 0.4 s** (61 MB per proof) once the request carries the browser's own headers;
- SBI wraps the deposit record in an **app-layer cipher inside HTTPS** (`{"header":{…},"body":"<base64>"}`), keyed per request by the page's own JavaScript;
- so the record is best read **inside the page**, with that page's own session key — which is what the in-browser prover does.

## What the extension does

It runs the same split-key proof, but from **inside the browser tab**, on the request the page is **already making** — nothing is copied or re-sent:

1. The institution installs the Scourge extension and opens AICTE's challenge link, then logs in to its bank as normal and opens its FD page.
2. As the page sends its real FD request, the extension notarises **that** request and response with AICTE's verifier co-holding the TLS session (MPC-TLS). The bank sees one ordinary request from a logged-in customer.
3. For a bank that encrypts the body, the extension also reads the page's **per-session app-layer key** (the key the bank's own JavaScript already holds to decrypt the response) and discloses **only that key**, plus the proven ciphertext, to AICTE's verifier.
4. AICTE's verifier decrypts the proven ciphertext with that key, extracts the FD record, and posts the same **facts** the Terminal path posts today.

The institution's password and OTP never leave the browser. AICTE receives the one FD record and the session key needed to read it, nothing else.

## What is reused, and what is Scourge's part

- **Reused (TLSNotary):** the browser-extension MPC-TLS prover and its verifier server already exist (`tlsnotary.org/docs/extension`). The extension runs the WASM prover; the verifier joins the session over a WebSocket and includes a proxy to the bank. We pin the same TLSNotary commit as `bin/`.
- **Scourge's part (new):**
  1. a small **plugin** that tells the extension which request to notarise (the bank's FD endpoint), binds it to AICTE's single-use challenge, and sets the reveal/redact rules (reveal the FD record; never reveal auth headers);
  2. **app-layer key disclosure** — read the page's per-session key and reveal it to the verifier only, so the proven ciphertext can be decrypted. This is the one genuinely new piece and is bank-specific;
  3. a **verifier→portal bridge**: the verifier server turns the decrypted record into the facts shape the portal already accepts.

## How it plugs into the portal (contract already exists)

The portal's internal endpoint `POST /internal/evidence` (loopback + internal key) already accepts the proof result as **facts** and runs the full policy + decision + evidence pipeline — it does not care whether the facts came from `scourge_verifierd` or from the extension's verifier. The facts shape is unchanged:

```json
{ "challenge_id": "...", "gate": "facts_ok",
  "fields": { "status": {"state":"proven","value":"ACTIVE"},
              "maturity": {"state":"proven","value":"2026-11-08"},
              "lien": {"state":"proven","value":"..."} },
  "metrics": { "elapsed_s": 0.4, "prover_to_verifier_MB": 61 } }
```

So the extension work is: (plugin + key disclosure) → verifier produces these facts → `POST /internal/evidence`. Everything downstream (register match, four verdicts, release gate) already works and is tested.

## Design principles

- This proves the **FD record as the bank's server sent it**, read with the key the bank's own page already uses. It does not break the bank's encryption or touch credentials.
- Reading the per-session key is configured per bank, like the field schemas and lien rules are today.
- Banks that serve deposit pages without an extra cipher are covered today by the command-line prover; app-encrypted banks are covered by the in-browser prover as it lands.
- Delivery: the bridge is done; the plugin for a first bank and the session-key step for SBI follow, ahead of a pilot.

## Build order

1. **Bridge — DONE.** `tools/extension/bridge.py` turns a decrypted deposit record into the portal's facts and posts it; `tests/test_extension_bridge.py` (6 tests) drives it against the running portal and checks every verdict: verified, discrepancy on a loan lien, insufficient on a missing lien, rejected on the wrong FD, a record nested in a list as SBI returns it, and no replay of a consumed challenge.
2. **Plugin.** Notarise the page's own deposit request; end-to-end into the portal through the bridge.
3. **Session-key disclosure for SBI.** Read the record with the page's own key, inside the verifier only.
