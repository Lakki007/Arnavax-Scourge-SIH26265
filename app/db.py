"""SQLite persistence. The events table is append-only (enforced by triggers) and hash-chained:
each entry stores the SHA-256 of the previous entry, so any later edit to the file breaks the chain."""
import hashlib
import json
import sqlite3
import threading
import time

from .settings import DB_PATH

_lock = threading.RLock()

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS institutions(id TEXT PRIMARY KEY, name TEXT NOT NULL, city TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS fdrs(
  id TEXT PRIMARY KEY, institution_id TEXT NOT NULL REFERENCES institutions(id),
  fd_number TEXT NOT NULL UNIQUE, bank_host TEXT NOT NULL, principal TEXT NOT NULL,
  issue_date TEXT NOT NULL, maturity TEXT NOT NULL, created_at REAL NOT NULL, created_by TEXT NOT NULL,
  released_at REAL, renews_fdr_id TEXT);
CREATE TABLE IF NOT EXISTS challenges(
  id TEXT PRIMARY KEY, fdr_id TEXT NOT NULL REFERENCES fdrs(id), institution_id TEXT NOT NULL,
  token TEXT NOT NULL, token_sha256 TEXT NOT NULL, issued_at REAL NOT NULL, expires_at REAL NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('issued','consumed','withdrawn')), consumed_at REAL, issued_by TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS evidence(
  id INTEGER PRIMARY KEY AUTOINCREMENT, challenge_id TEXT NOT NULL UNIQUE REFERENCES challenges(id),
  fdr_id TEXT NOT NULL, received_at REAL NOT NULL, gate TEXT NOT NULL, reason TEXT, decision TEXT NOT NULL,
  facts_json TEXT NOT NULL, match_json TEXT NOT NULL, metrics_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS actions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, fdr_id TEXT NOT NULL, evidence_id INTEGER,
  action TEXT NOT NULL CHECK(action IN ('accept','escalate','release')), note TEXT, actor TEXT NOT NULL, at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS jobs(
  id TEXT PRIMARY KEY, challenge_id TEXT NOT NULL, fdr_id TEXT NOT NULL, institution_id TEXT NOT NULL,
  mode_json TEXT NOT NULL, state TEXT NOT NULL, started_at REAL NOT NULL, ended_at REAL,
  stages_json TEXT NOT NULL DEFAULT '[]', error TEXT, evidence_id INTEGER);
CREATE TABLE IF NOT EXISTS baseline_uploads(
  id INTEGER PRIMARY KEY AUTOINCREMENT, fdr_id TEXT NOT NULL, filename TEXT NOT NULL, mime TEXT NOT NULL,
  size INTEGER NOT NULL, sha256 TEXT NOT NULL, stored_as TEXT NOT NULL, uploaded_at REAL NOT NULL, actor TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, actor TEXT NOT NULL, kind TEXT NOT NULL,
  fdr_id TEXT, detail_json TEXT NOT NULL, prev_hash TEXT, hash TEXT);
CREATE TABLE IF NOT EXISTS release_requests(
  id INTEGER PRIMARY KEY AUTOINCREMENT, fdr_id TEXT NOT NULL REFERENCES fdrs(id), evidence_id INTEGER NOT NULL,
  proposed_by TEXT NOT NULL, proposed_at REAL NOT NULL, note TEXT NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('pending','approved','rejected','withdrawn')),
  decided_by TEXT, decided_at REAL, decision_note TEXT, letter_no TEXT, signed_json TEXT, signature TEXT);
CREATE TABLE IF NOT EXISTS eadvices(
  id INTEGER PRIMARY KEY AUTOINCREMENT, fdr_id TEXT NOT NULL REFERENCES fdrs(id), evidence_id INTEGER,
  uploaded_at REAL NOT NULL, actor TEXT NOT NULL, ok INTEGER NOT NULL, reason TEXT, fields_json TEXT NOT NULL,
  signer TEXT, issued_at REAL, sha256 TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS holds(
  id INTEGER PRIMARY KEY AUTOINCREMENT, fdr_id TEXT NOT NULL REFERENCES fdrs(id), placed_by TEXT NOT NULL,
  placed_at REAL NOT NULL, reason TEXT NOT NULL, lifted_by TEXT, lifted_at REAL, lift_note TEXT);
CREATE TABLE IF NOT EXISTS institution_profile(
  institution_id TEXT PRIMARY KEY REFERENCES institutions(id), programmes_json TEXT NOT NULL DEFAULT '[]',
  leased INTEGER NOT NULL DEFAULT 0, exempt TEXT);
CREATE TABLE IF NOT EXISTS notifications(
  id INTEGER PRIMARY KEY AUTOINCREMENT, fdr_id TEXT NOT NULL DEFAULT '', institution_id TEXT NOT NULL, kind TEXT NOT NULL,
  step TEXT NOT NULL, audience TEXT NOT NULL, channel TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
  created_at REAL NOT NULL, as_of TEXT NOT NULL, status TEXT NOT NULL,
  UNIQUE(fdr_id, institution_id, kind, step, audience, channel));
CREATE TABLE IF NOT EXISTS release_clearances(
  id INTEGER PRIMARY KEY AUTOINCREMENT, release_id INTEGER NOT NULL REFERENCES release_requests(id), dept TEXT NOT NULL,
  decision TEXT NOT NULL CHECK(decision IN ('clear','object')), officer TEXT NOT NULL, at REAL NOT NULL, note TEXT,
  UNIQUE(release_id, dept));
CREATE TABLE IF NOT EXISTS accounts(
  username TEXT PRIMARY KEY, role TEXT NOT NULL CHECK(role IN ('officer','institution')), institution_id TEXT, officer TEXT,
  salt TEXT NOT NULL, pw_hash TEXT NOT NULL, created_at REAL NOT NULL, disabled INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS release_approvals(
  id INTEGER PRIMARY KEY AUTOINCREMENT, release_id INTEGER NOT NULL REFERENCES release_requests(id), officer TEXT NOT NULL,
  at REAL NOT NULL, fp TEXT, UNIQUE(release_id, officer, fp));
CREATE TABLE IF NOT EXISTS branch_requests(
  id TEXT PRIMARY KEY, fdr_id TEXT NOT NULL REFERENCES fdrs(id), branch_code TEXT NOT NULL, contact TEXT NOT NULL,
  token_sha256 TEXT NOT NULL, code_sha256 TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('sent','answered','expired','withdrawn')), attempts INTEGER NOT NULL DEFAULT 0,
  answered_at REAL, answer_json TEXT, evidence_id INTEGER, requested_by TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS branch_outbox(
  id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL, to_addr TEXT NOT NULL, subject TEXT NOT NULL,
  body TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS documents(
  id INTEGER PRIMARY KEY AUTOINCREMENT, fdr_id TEXT NOT NULL REFERENCES fdrs(id), kind TEXT NOT NULL, filename TEXT NOT NULL,
  mime TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL, stored_as TEXT NOT NULL, wrapped_key TEXT NOT NULL,
  nonce TEXT NOT NULL, uploaded_by TEXT NOT NULL, uploaded_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS officer_mfa(
  officer TEXT PRIMARY KEY, secret TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL,
  last_step INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS login_failures(
  id INTEGER PRIMARY KEY AUTOINCREMENT, who TEXT NOT NULL, at REAL NOT NULL);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT,'events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT,'events are append-only'); END;
"""


def connect() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


_conn = None


def conn() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            _conn = connect()
            _conn.executescript(SCHEMA)
            _migrate(_conn)
        return _conn


def _migrate(c):
    """Add columns introduced after a database was first created (older demo databases)."""
    cols = {r[1] for r in c.execute("PRAGMA table_info(events)")}
    for col in ("prev_hash", "hash"):
        if col not in cols:
            c.execute(f"ALTER TABLE events ADD COLUMN {col} TEXT")
    rcols = {r[1] for r in c.execute("PRAGMA table_info(release_requests)")}
    for col in ("signed_json", "signature"):
        if col not in rcols:
            c.execute(f"ALTER TABLE release_requests ADD COLUMN {col} TEXT")
    if "fp" not in {r[1] for r in c.execute("PRAGMA table_info(release_approvals)")}:
        c.execute("ALTER TABLE release_approvals ADD COLUMN fp TEXT")
    ncols = {r[1] for r in c.execute("PRAGMA table_info(notifications)")}
    for col, typ in (("attempts", "INTEGER NOT NULL DEFAULT 0"), ("next_try", "REAL"), ("last_error", "TEXT")):
        if col not in ncols:
            c.execute(f"ALTER TABLE notifications ADD COLUMN {col} {typ}")
    if "renews_fdr_id" not in {r[1] for r in c.execute("PRAGMA table_info(fdrs)")}:
        c.execute("ALTER TABLE fdrs ADD COLUMN renews_fdr_id TEXT")


def q(sql, args=()):
    with _lock:
        return [dict(r) for r in conn().execute(sql, args).fetchall()]


def q1(sql, args=()):
    r = q(sql, args)
    return r[0] if r else None


def x(sql, args=()):
    with _lock:
        cur = conn().execute(sql, args)
        return cur.lastrowid


def tx(fn):
    """Run fn(connection) in one IMMEDIATE transaction (serialises writers)."""
    with _lock:
        c = conn()
        c.execute("BEGIN IMMEDIATE")
        try:
            r = fn(c)
            c.execute("COMMIT")
            return r
        except Exception:
            c.execute("ROLLBACK")
            raise


GENESIS = "0" * 64


def chain_hash(prev: str, at: float, actor: str, kind: str, fdr_id, detail_json: str) -> str:
    """SHA-256 over the previous hash and this entry's fields, in a fixed canonical form."""
    body = json.dumps([prev, repr(float(at)), actor, kind, fdr_id, detail_json], separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode()).hexdigest()


def event(actor: str, kind: str, fdr_id=None, **detail):
    with _lock:  # read-then-append must not interleave with another writer in this process
        last = conn().execute("SELECT hash FROM events ORDER BY id DESC LIMIT 1").fetchone()
        prev = (last["hash"] if last and last["hash"] else GENESIS)
        at, dj = time.time(), json.dumps(detail, default=str)
        x("INSERT INTO events(at,actor,kind,fdr_id,detail_json,prev_hash,hash) VALUES(?,?,?,?,?,?,?)",
          (at, actor, kind, fdr_id, dj, prev, chain_hash(prev, at, actor, kind, fdr_id, dj)))


def verify_chain():
    """Recompute every link. Returns {ok, count, head, broken_at, problem}."""
    prev, n, head = GENESIS, 0, GENESIS
    for r in q("SELECT * FROM events ORDER BY id"):
        n += 1
        if r["prev_hash"] != prev:
            return {"ok": False, "count": n, "head": head, "broken_at": r["id"], "problem": "link to the previous entry does not match"}
        h = chain_hash(prev, r["at"], r["actor"], r["kind"], r["fdr_id"], r["detail_json"])
        if r["hash"] != h:
            return {"ok": False, "count": n, "head": head, "broken_at": r["id"], "problem": "entry contents changed after it was written"}
        prev = head = h
    return {"ok": True, "count": n, "head": head, "broken_at": None, "problem": None}


def wipe():
    """Demo reset: drop everything (including the append-only log) and recreate."""
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None
        for suffix in ("", "-wal", "-shm"):
            p = DB_PATH.with_name(DB_PATH.name + suffix)
            if p.exists():
                p.unlink()
        conn()
