# Real-bank field test: prove one real FD page

**Goal.** Show that the request your net banking makes to load **your own** fixed-deposit details can be proven
with a split-key TLS session, revealing only the FD fields. This is the one claim the prototype has not yet shown
on a real bank.

## Safety rules (read first)

- Use **your own** account, or a family member's with their explicit permission and presence.
- Everything runs **on your laptop**. Nothing is uploaded anywhere.
- `headers.txt` holds your live session. **Never** share it, commit it, paste it into chat or screenshot it. Delete it afterwards and log out.
- By default the tool prints **no values**, only "found / length" per field. Share only that output line.

## 1. Build (once, ~20–40 min)

macOS (Apple Silicon or Intel) or Linux:

```bash
curl https://sh.rustup.rs -sSf | sh        # install Rust (accept defaults), then open a new terminal
git clone https://github.com/Lakki007/Arnavax-Scourge-SIH26265 && cd Arnavax-Scourge-SIH26265
./scripts/build_rust.sh                    # builds bin/real_bank_probe (+ prover, verifier, mock bank)
```

On Linux x86_64 you can skip the build: `bin/real_bank_probe` is prebuilt (check `cd bin && sha256sum -c SHA256SUMS`).

## 2. Find the FD request (~10 min)

1. Log in to net banking in Chrome. Open **DevTools → Network** (tick "Preserve log").
2. Open the FD / term-deposit **details** page.
3. Find the **one** request whose **Response** contains your FD number, status and maturity date (often a JSON/XHR call, sometimes the HTML page).
4. Note: the request URL, GET or POST, the exact field names (e.g. `depositNo`, `status`, `maturityDate`, `lienAmount`), and the TLS version (**Security** tab).
5. Right-click the request → **Copy → Copy request headers**. Paste into `tools/realbank/headers.txt`.

## 3. Run the probe (~2 min)

```bash
cd tools/realbank
URL='https://<bank host>/<path from DevTools>' \
HEADERS_FILE=headers.txt \
REVEAL='<fd number key>,<status key>,<maturity key>,<lien key>' \
ALLOW='<bank domain, e.g. onlinesbi.sbi>' \
MAX_SENT=16384 MAX_RECV=262144 \
../../bin/real_bank_probe
```

- For an HTML page, put the visible **labels** in `REVEAL` (e.g. `Maturity Date,Lien`).
- "data limit" error → double `MAX_SENT` / `MAX_RECV` and rerun. "unexpected end of file" → rerun once.
- POST requests are not supported by the probe yet: note it and tell the team (a quick fix).

## 4. Clean up

Log out of net banking. `rm headers.txt`. Close DevTools.

## 5. Report (safe to share)

Fill in and share **only** this:

| Item | Value |
|---|---|
| Bank | |
| Date | |
| TLS version (DevTools) | |
| Request type (GET/POST, JSON/HTML) | |
| Probe output line | (paste the single JSON line) |
| Fields the page shows: FD number / status / balance / maturity / holders / lien-pledge | yes/no each |

## What the result means

| Result | Meaning |
|---|---|
| `"ok":true`, FD fields found | **Proven on a real bank's FD page.** Add the bank to `config/scourge.json` and run the full Scourge flow |
| ok, but no lien field | Same headline; the lien comes from the bank-signed e-advice or branch confirmation tier |
| TLS 1.3-only / handshake error | That bank needs a TLS 1.3-capable prover or the browser-extension prover; try another bank |
| login/redirect page proven instead of FD data | Session headers expired or the FD data comes from a different request; retry with a fresh login |

## 6. Optional: the full Scourge flow on that bank

Add an entry for the bank host in `config/scourge.json` → `banks` (endpoint method + path prefix, `disclosure: "object"` for list
responses, the field-name map, `required`, `active_values`, `lien_ok_contains`, `lien_bad_contains`, `holder_must_contain`), add the
host to `allow_hosts`, switch `trust.mode` to public roots, register your FD in the portal, request a proof and run `bin/scourge_prove`
with `--url`, `--headers-file headers.txt`, `--challenge-id`, `--token` and `--fd-number` (see `rust/prove.rs --help`).
