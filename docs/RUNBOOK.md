# Scourge runbook: setup, deploy, operate, roll back

Scourge is one Linux host running four processes: the app, the verifier, and the synthetic banks.

- The **app** (FastAPI) listens on `127.0.0.1:8080` only, and supervises the verifier and banks.
- The **verifier** listens on `127.0.0.1:7047`.
- The **synthetic banks** listen on `127.0.0.1:4400`, `4401` and `4402`.
- **nginx** publishes the app over HTTPS. Never bind the app to a public address: the verifier reports to 127.0.0.1:8080.

## 1. Requirements

- Linux x86_64, 2 vCPU, 4 GB RAM, about 1 GB disk.
- Python 3.11 or later (tested on 3.13), nginx.
- A DNS name for TLS (Let's Encrypt), or the team's own certificate.
- Rust is needed only to rebuild `bin/` (`scripts/build_rust.sh`).

## 2. First install

```bash
sudo useradd --system --create-home --home-dir /opt/scourge-home scourge
sudo mkdir -p /opt/scourge-releases /var/lib/scourge && sudo chown scourge: /opt/scourge-releases /var/lib/scourge
cd /opt/scourge-releases && sudo -u scourge unzip /path/to/scourge_prototype.zip && sudo -u scourge mv scourge v1.0.0-rc1
sha256sum /path/to/scourge_prototype.zip            # must match docs/RELEASE.txt
cd v1.0.0-rc1 && ./scripts/check_bins.sh            # binaries match bin/SHA256SUMS
sudo -u scourge python3 -m venv venv && sudo -u scourge venv/bin/pip install -r requirements.txt
sudo ln -sfn /opt/scourge-releases/v1.0.0-rc1 /opt/scourge
```

### Secrets

Secrets live in `/etc/scourge.env` (mode 0600, owner root). They are never committed or logged.

```
SCOURGE_VAR=/var/lib/scourge
SCOURGE_ACCESS_CODE=<generate: python3 -c "import secrets;print(secrets.token_urlsafe(9))">
SCOURGE_COOKIE_SECURE=1
```

The session and internal keys are generated on first start in `$SCOURGE_VAR/*.key` (mode 0600). To set them yourself, use `SCOURGE_SESSION_KEY` and `SCOURGE_INTERNAL_KEY` (at least 32 characters).

### Service and proxy

1. **Service.** Copy `deploy/scourge.service` to `/etc/systemd/system/`. Edit `ReadWritePaths=` to `/var/lib/scourge`. Then run `sudo systemctl enable --now scourge`.
2. **Proxy.** Copy `deploy/nginx.conf` into `/etc/nginx/sites-enabled/` and set `server_name` and the certificate paths. Then run `sudo nginx -t && sudo systemctl reload nginx`.

### Data

The database and the synthetic bank state are created and seeded on first start. There are no migrations: the schema is created if absent.

## 3. Verify a deployment (on the public URL, not localhost)

```bash
curl -s https://<host>/api/health        # all four services true
curl -s -o /dev/null -w "%{http_code}\n" -X POST https://<host>/internal/evidence   # 404 (blocked at nginx)
# full suite against the live URL (from a checkout of the same release):
SCOURGE_URL=https://<host> SCOURGE_ACCESS_CODE=<code> SCOURGE_COOKIE_SECURE=1 SCOURGE_VAR=/var/lib/scourge \
  python3 -m pytest -q tests/          # run ON the host: the tests read the database
```

Then do the central journey by hand, from section 2 of `docs/JUDGE_DEMO.md`. Also check:

- refresh the job page mid-proof;
- open an evidence link directly in a new tab;
- restart the service (`systemctl restart scourge`) and confirm the records are still there.

## 4. Operate

- **Reset between judging sessions.** Sign in as the officer, then use **Reset demo** in the bottom bar. Reset is refused while a proof runs; wait for it to finish (at most 90 s).
- **Logs.**
  - App: `journalctl -u scourge`.
  - Verifier and banks: `$SCOURGE_VAR/logs/*.log`.
  - Proxy: nginx access and error logs.
- **Health.** `/api/health`. The System page (officer) can start and stop services. Stopping the verifier is the failure-recovery demo beat.
- **Backup.**

  ```bash
  sqlite3 /var/lib/scourge/scourge.db ".backup /var/lib/scourge/backup-$(date +%F-%H%M).db"
  ```

  Or stop the service and copy `$SCOURGE_VAR`.
- **Restart during a proof.** The interrupted run is marked failed on start. If its challenge was already used, request a new one.

## 5. Upgrade and roll back

Rehearsed on staging, 29 Oct 2026: v0 (phase-6 build) → v1.0.0-rc1 → v0 → v1, on the same data, with proofs in each version.

```bash
# upgrade
sudo systemctl stop scourge
sqlite3 /var/lib/scourge/scourge.db ".backup /var/lib/scourge/pre-<new>.db"
sudo ln -sfn /opt/scourge-releases/<new> /opt/scourge && sudo systemctl start scourge
# roll back
sudo systemctl stop scourge && sudo ln -sfn /opt/scourge-releases/<previous> /opt/scourge && sudo systemctl start scourge
# if a future release changes the schema: also restore the pre-upgrade backup before starting the old release
```

## 6. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Every proof fails: "verifier did not report" | App not on the `app_internal` address | Do not change the app's bind; publish through nginx |
| "AICTE's verifier is unreachable" | Verifier stopped | System page → Start, or `systemctl restart scourge`. The challenge stays unused; retry |
| Reset refused | A proof is running | Wait or cancel; interrupted runs are cleared automatically |
| 413 on upload | File over 2 MB | Expected |
| "Wrong access code" | Missing or old `SCOURGE_ACCESS_CODE` | Check `/etc/scourge.env`; restart |
