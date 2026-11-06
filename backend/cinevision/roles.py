"""Who may do what on this instance: the owner, admins, and sign-up invites.

* The **owner** is the first account ever created (oldest ``created_at``, then insertion order). It can't be
  demoted or replaced from the app.
* **Admins** are accounts the owner promoted. Admins may change instance-wide settings (local AI) and create
  invite codes. Only the owner can make or remove admins.
* **Invite codes** let new people sign up when ``CINEVISION_SIGNUP=owner_only``. Codes are random (72 bits),
  single use, expire after 7 days, and only their SHA-256 hash is stored.

The tables live here (not in schema.sql) and are created idempotently at start-up by ``ensure_schema``.
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta

from . import db
from .config import settings

INVITE_DAYS = 7
MAX_OPEN_INVITES = 50

DDL = """
CREATE TABLE IF NOT EXISTS user_roles (
    user_id     TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    role        TEXT NOT NULL CHECK (role IN ('admin')),
    granted_by  TEXT,
    granted_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS signup_invites (
    id          TEXT PRIMARY KEY,
    code_hash   TEXT NOT NULL UNIQUE,       -- sha256 of the code; the code itself is shown once and never stored
    created_by  TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    used_by     TEXT REFERENCES users(id) ON DELETE SET NULL,
    used_at     TEXT
);
"""


def ensure_schema() -> None:
    db.conn().executescript(DDL)


# ---------------------------------------------------------------- roles
def owner_id() -> str | None:
    row = db.one("SELECT id FROM users ORDER BY created_at, rowid LIMIT 1")
    return row["id"] if row else None


def is_owner(uid: str) -> bool:
    return bool(uid) and owner_id() == uid


def is_admin(uid: str) -> bool:
    """The owner, or an account the owner made an admin."""
    if not uid:
        return False
    if is_owner(uid):
        return True
    try:
        return db.one("SELECT 1 FROM user_roles WHERE user_id = ? AND role = 'admin'", (uid,)) is not None
    except sqlite3.OperationalError:  # table not created yet (e.g. a process that never ran ensure_schema)
        return False


def role_of(uid: str) -> str:
    return "owner" if is_owner(uid) else "admin" if is_admin(uid) else "member"


def set_admin(uid: str, admin: bool, granted_by: str) -> None:
    if admin:
        db.run("INSERT INTO user_roles(user_id, role, granted_by, granted_at) VALUES (?, 'admin', ?, ?)"
               " ON CONFLICT(user_id) DO UPDATE SET role='admin', granted_by=excluded.granted_by, granted_at=excluded.granted_at",
               (uid, granted_by, db.now_iso()))
    else:
        db.run("DELETE FROM user_roles WHERE user_id = ?", (uid,))


def list_users() -> list[dict]:
    oid = owner_id()
    admins = {r["user_id"] for r in db.all_("SELECT user_id FROM user_roles WHERE role='admin'")}
    rows = db.all_("SELECT id, email, created_at, last_login_at FROM users ORDER BY created_at, rowid")
    return [{"id": r["id"], "email": r["email"], "created_at": r["created_at"], "last_login_at": r["last_login_at"],
             "role": "owner" if r["id"] == oid else "admin" if r["id"] in admins else "member"} for r in rows]


# ---------------------------------------------------------------- sign-up policy
def has_users() -> bool:
    return db.one("SELECT 1 FROM users LIMIT 1") is not None


def signup_state() -> dict:
    """What the sign-in page should offer. The very first (owner) account can always be created, otherwise a
    fresh install would be unusable; after that the configured mode applies."""
    mode = settings().signup_mode
    first = not has_users()
    if first or mode == "open":
        return {"mode": mode, "open": True, "invite_required": False, "first_account": first}
    if mode == "owner_only":
        return {"mode": mode, "open": True, "invite_required": True, "first_account": False}
    return {"mode": mode, "open": False, "invite_required": False, "first_account": False}


# ---------------------------------------------------------------- invites
def _hash(code: str) -> str:
    norm = "".join(ch for ch in (code or "").strip().upper() if ch.isalnum())
    return hashlib.sha256(("cv-invite:" + norm).encode()).hexdigest()


_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I: easy to read out and type


def _new_code() -> str:
    raw = "".join(secrets.choice(_ALPHABET) for _ in range(15))  # 15 × 5 bits = 75 bits
    return "-".join(raw[i:i + 5] for i in range(0, 15, 5))


def create_invite(created_by: str) -> dict:
    open_count = db.one("SELECT COUNT(*) n FROM signup_invites WHERE used_at IS NULL AND expires_at > ?",
                        (db.now_iso(),))["n"]
    if open_count >= MAX_OPEN_INVITES:
        raise ValueError("Too many unused invite codes. Remove some first.")
    code, iid = _new_code(), db.new_id()
    expires = db.now_iso(timedelta(days=INVITE_DAYS))
    db.run("INSERT INTO signup_invites(id, code_hash, created_by, created_at, expires_at) VALUES (?,?,?,?,?)",
           (iid, _hash(code), created_by, db.now_iso(), expires))
    return {"id": iid, "code": code, "expires_at": expires}


def list_invites() -> list[dict]:
    rows = db.all_("SELECT i.id, i.created_at, i.expires_at, i.used_at, u.email AS created_by_email FROM signup_invites i"
                   " LEFT JOIN users u ON u.id = i.created_by ORDER BY i.created_at DESC LIMIT 200")
    now = db.utcnow()
    out = []
    for r in rows:
        status = "used" if r["used_at"] else "expired" if datetime.fromisoformat(r["expires_at"]) < now else "unused"
        out.append({"id": r["id"], "created_at": r["created_at"], "expires_at": r["expires_at"], "used_at": r["used_at"],
                    "created_by": r["created_by_email"], "status": status})
    return out


def delete_invite(iid: str) -> bool:
    return db.run("DELETE FROM signup_invites WHERE id = ? AND used_at IS NULL", (iid,)) > 0


def consume_invite(c: sqlite3.Connection, code: str, uid: str) -> bool:
    """Inside the registration transaction: mark a valid, unused, unexpired code as used by ``uid``."""
    cur = c.execute("UPDATE signup_invites SET used_by = ?, used_at = ? WHERE code_hash = ? AND used_at IS NULL"
                    " AND expires_at > ?", (uid, db.now_iso(), _hash(code), db.now_iso()))
    return cur.rowcount == 1


# ---------------------------------------------------------------- exposure
def exposure() -> dict:
    """How reachable this server is, for the start-up warning and the Security page banner."""
    s = settings()
    risky = s.network_exposed and s.plain_http and s.signup_mode == "open"
    return {"host": s.host, "network": s.network_exposed, "plain_http": s.plain_http,
            "signup_mode": s.signup_mode, "warning": risky}


def startup_warnings() -> list[str]:
    s = settings()
    out = []
    if s.network_exposed:
        out.append(f"CineVision is listening on {s.host}:{s.port}, so OTHER COMPUTERS on your network can reach it. "
                   "Set CINEVISION_HOST=127.0.0.1 (the default) to keep it on this computer only.")
        if s.plain_http:
            out.append("The connection is plain http: passwords and footage cross the network unencrypted. "
                       "Use the Docker set-up (HTTPS) for network access.")
        if s.signup_mode == "open":
            out.append("Sign-up is OPEN: anyone who can reach this address can create an account. "
                       "Set CINEVISION_SIGNUP=owner_only (invite codes) or CINEVISION_SIGNUP=closed.")
    return out
