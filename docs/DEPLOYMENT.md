# Deploying Scourge: portal and an independent AICTE verifier

The verifier is the component AICTE must trust most, so it runs separately from the portal: its own container
or VM, no database, no documents, no officer accounts. It co-runs the MPC-TLS session with the institution's
prover, checks the bank's certificate, host and record, and reports **facts** (never decisions) to the portal.

```
 institution's computer                 AICTE
 ┌──────────────┐  MPC-TLS :7047   ┌────────────────────┐  private network   ┌──────────────────────────┐
 │ scourge_prove├─────────────────►│ verifier container ├───────────────────►│ portal container         │
 │ (own login,  │                  │ scourge_verifierd  │ POST /internal/*   │ app + SQLite + vault     │
 │  own OTP)    │◄──── bank TLS ──►│ no DB, no documents│ X-Internal-Key     │ :8000 public (nginx)     │
 └──────────────┘    (prover↔bank) └────────────────────┘                    │ :8081 internal only      │
                                                                             └──────────────────────────┘
```

## Run it

```bash
export SCOURGE_INTERNAL_KEY=$(openssl rand -hex 32)      # shared by portal and verifier only
docker compose -f deploy/compose.two-host.yml up -d --build
python3 scripts/smoke_proof.py http://127.0.0.1:8000    # one proof through the independent verifier
```

For separate machines, run the `verifier` service on AICTE's verifier host and the `portal` service on the
portal host, joined by a private network or VPN (WireGuard/IPsec), and set `SCOURGE_APP_INTERNAL` to the
portal's private address. Only port 7047 of the verifier and 443 of the portal face the internet.

## What each side enforces

| Boundary | Control |
|---|---|
| Verifier → portal | Private network only (`aicte-internal` is `internal: true`); nginx `:8081` serves `/internal/` only, from private ranges; the app still requires `X-Internal-Key` (constant-time compare). `/internal/` returns 404 on the public port. |
| Portal → verifier | None needed: the portal never controls the verifier. In `SCOURGE_VERIFIER=external` mode it cannot start or stop it (409), only checks that it is reachable. |
| Prover → verifier | MPC-TLS; single-use challenge consumed by the portal before the session; bank certificate checked against roots configured on the verifier; host allow-list on the verifier. |
| Data on the verifier | None persisted. Its config (allow-list, bank field schemas, lien rules, trust roots) is read-only. |

## Evidence

| Check | Where |
|---|---|
| A proof verified through a verifier on a different address, started independently of the portal | `scripts/smoke_proof.py`; CI job `two-host` (containers) |
| Portal never spawns or stops an external verifier | `tests/test_units.py::test_external_verifier_mode_is_never_started_or_stopped_by_the_portal` |
| Full lifecycle in production sign-in mode | `tests/test_production_mode.py`; CI job `production-mode` |
| Single-host deployment, backup, restart | `docker-compose.yml`; CI job `container` |

## Environment reference

| Variable | Side | Meaning |
|---|---|---|
| `SCOURGE_VERIFIER=external` | portal | Do not start a local verifier |
| `SCOURGE_VERIFIER_ADDR` | portal | Address provers are given (`host:7047`) |
| `SCOURGE_INTERNAL_KEY` | both | Shared secret for `/internal/*` |
| `SCOURGE_APP_INTERNAL` | verifier | Portal's internal address on the private network (`portal:8081`) |
| `SCOURGE_AUTH=password` | portal | Production sign-in: named accounts, mandatory officer 2FA |
