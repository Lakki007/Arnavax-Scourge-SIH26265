# Browser-extension prover: the plan

*Why Scourge needs an in-browser prover for some banks, what it does, and how it plugs into the portal we already have. Written 1 Nov 2026 after the SBI field test (`tools/realbank/RESULTS.md`).*

## Why the Terminal probe isn't enough for every bank

The command-line probe (`bin/real_bank_probe`) proves one request by **re-sending a copy** of it inside a split-key TLS session. That works on any bank whose FD page returns readable data. It does **not** work when a bank's web app adds its own encryption **inside** HTTPS.

SBI's OnlineYONO is such a bank. On 1 Nov 2026 we confirmed, on a team member's own ₹1,000 FD:

- the split-key session reaches SBI's logged-in deposits API and SBI returns **HTTP 200** (with the browser's own headers);
- but both the request and the response bodies are ciphertext (`{"header":{…},"body":"<base64>"}`), encrypted by SBI's own JavaScript with a key it derives **per request, in the browser**;
- so a request copied out of the browser and re-sent is answered with a short, stale reply — never the deposit record.

No change to the Terminal probe can fix this, because the plaintext only ever exists **inside the page**. That is what the extension is for.

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

## Scope and honesty

- This proves the **FD record as the bank's server sent it**, read with the key the bank's own page already uses. It does not break the bank's encryption or touch credentials.
- Reading the per-session key out of the page is bank-specific and needs care; it is the main unknown.
- Until the extension exists, banks that encrypt payloads stay at "live real-bank session" (transport proven, data not). Banks that serve readable FD pages can be proven today with the Terminal probe.
- Effort estimate: a working plugin + bridge against one readable bank is a few days; the app-layer key path for SBI is longer and is the research item.

## Build order

1. **Bridge first (no bank needed).** A verifier→`/internal/evidence` adapter + a fixture proof, tested against the running portal. This is pure Scourge code and can be built and tested now.
2. **Plugin against a readable bank.** Notarise a real FD page that returns plaintext; end-to-end into the portal.
3. **App-layer key disclosure for SBI.** The research step.
