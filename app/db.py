"""SQLite persistence. The events table is append-only (enforced by triggers)."""
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
  released_at REAL);
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
  fdr_id TEXT, detail_json TEXT NOT NULL);
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
        return _conn


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


def event(actor: str, kind: str, fdr_id=None, **detail):
    x("INSERT INTO events(at,actor,kind,fdr_id,detail_json) VALUES(?,?,?,?,?)",
      (time.time(), actor, kind, fdr_id, json.dumps(detail, default=str)))


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
