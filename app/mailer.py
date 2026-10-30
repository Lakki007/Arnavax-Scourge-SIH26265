"""E-mail delivery for queued alerts, with retry.

If SCOURGE_SMTP_HOST is set, queued e-mail notifications are sent over SMTP (STARTTLS when
SCOURGE_SMTP_STARTTLS=1, login when SCOURGE_SMTP_USER is set). Failures retry with exponential
backoff (1, 2, 4, 8 minutes) and are marked "failed" after MAX_ATTEMPTS. Every send/failure is
written to the audit log. Without SMTP configured, e-mail stays queued and is shown as such."""
import json
import os
import smtplib
import time
from email.message import EmailMessage

from . import db
from .settings import ROOT

MAX_ATTEMPTS = 5
CONTACTS = json.loads((ROOT / "config" / "contacts.json").read_text())


def configured() -> bool:
    return bool(os.environ.get("SCOURGE_SMTP_HOST"))


def recipient(n) -> str | None:
    if n["audience"] == "institution":
        return CONTACTS["institution"].get(n["institution_id"])
    return CONTACTS.get(n["audience"])


def _send(to, subject, body):
    msg = EmailMessage()
    msg["From"] = os.environ.get("SCOURGE_SMTP_FROM", "scourge-alerts@aicte.example.test")
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body + "\n\n-- Scourge, AICTE security-deposit portal (automated alert)")
    host, port = os.environ["SCOURGE_SMTP_HOST"], int(os.environ.get("SCOURGE_SMTP_PORT", "25"))
    with smtplib.SMTP(host, port, timeout=10) as s:
        if os.environ.get("SCOURGE_SMTP_STARTTLS") == "1":
            s.starttls()
        if os.environ.get("SCOURGE_SMTP_USER"):
            s.login(os.environ["SCOURGE_SMTP_USER"], os.environ.get("SCOURGE_SMTP_PASSWORD", ""))
        s.send_message(msg)


def deliver_pending(now: float | None = None, limit: int = 200) -> dict:
    """Send due e-mails. Safe to call repeatedly (the daily job and the Alerts page call it)."""
    if not configured():
        return {"sent": 0, "failed": 0, "skipped": "SMTP not configured"}
    now = now or time.time()
    sent = failed = 0
    rows = db.q("SELECT * FROM notifications WHERE channel='email' AND status IN ('queued','queued (demo: not sent)','retrying') "
                "AND (next_try IS NULL OR next_try<=?) ORDER BY id LIMIT ?", (now, limit))
    for n in rows:
        to = recipient(n)
        if not to:
            db.x("UPDATE notifications SET status='failed', last_error=? WHERE id=?", ("no address on file", n["id"]))
            db.event("system", "email_failed", n["fdr_id"] or None, notification=n["id"], error="no address on file")
            failed += 1
            continue
        try:
            _send(to, n["title"], n["body"])
        except Exception as e:  # network, auth, refusal
            att = n["attempts"] + 1
            final = att >= MAX_ATTEMPTS
            db.x("UPDATE notifications SET status=?, attempts=?, next_try=?, last_error=? WHERE id=?",
                 ("failed" if final else "retrying", att, None if final else now + 60 * 2 ** (att - 1), str(e)[:200], n["id"]))
            if final:
                db.event("system", "email_failed", n["fdr_id"] or None, notification=n["id"], to=to, attempts=att, error=str(e)[:200])
                failed += 1
            continue
        db.x("UPDATE notifications SET status='sent', attempts=attempts+1, next_try=NULL, last_error=NULL WHERE id=?", (n["id"],))
        db.event("system", "email_sent", n["fdr_id"] or None, notification=n["id"], to=to, alert=n["kind"], step=n["step"])
        sent += 1
    return {"sent": sent, "failed": failed}
