"""Password hashing, TOTP, sessions, CSRF and rate limiting."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import struct
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from . import db
from .config import settings

# ---------------------------------------------------------------- passwords
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 15, 8, 1
_MAXMEM = 128 * SCRYPT_N * SCRYPT_R * 2

COMMON_PASSWORDS = {
    "password", "password123", "123456789012", "qwertyuiop12", "letmein12345", "iloveyou1234",
    "administrator", "welcome12345", "passw0rd1234", "changeme1234", "000000000000", "111111111111",
    "abcdefghijkl", "qwerty123456", "1q2w3e4r5t6y", "cinevision123", "trustno1trustno1",
}


def password_problems(pw: str, email: str = "") -> list[str]:
    problems = []
    if len(pw) < 12:
        problems.append("Use at least 12 characters.")
    if len(pw) > 256:
        problems.append("Use at most 256 characters.")
    if pw.lower() in COMMON_PASSWORDS or len(set(pw)) < 5:
        problems.append("This password is too common or repetitive.")
    if email and email.split("@")[0].lower() in pw.lower() and len(email.split("@")[0]) >= 4:
        problems.append("Don't include your email name in the password.")
    return problems


def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(pw.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, maxmem=_MAXMEM, dklen=32)
    return "scrypt${}${}${}${}${}".format(
        SCRYPT_N, SCRYPT_R, SCRYPT_P, base64.b64encode(salt).decode(), base64.b64encode(dk).decode()
    )


_DUMMY_HASH: str | None = None


def verify_password(pw: str, stored: str | None) -> bool:
    """Constant-ish time: an unknown user still costs one scrypt evaluation."""
    global _DUMMY_HASH
    if stored is None:
        if _DUMMY_HASH is None:
            _DUMMY_HASH = hash_password("dummy-password-for-timing")
        stored, pw = _DUMMY_HASH, pw + "\x00"
        verify_password(pw, stored)
        return False
    try:
        _, n, r, p, salt_b, dk_b = stored.split("$")
        dk = hashlib.scrypt(pw.encode(), salt=base64.b64decode(salt_b), n=int(n), r=int(r), p=int(p),
                            maxmem=128 * int(n) * int(r) * 2, dklen=32)
        return hmac.compare_digest(dk, base64.b64decode(dk_b))
    except Exception:
        return False


# ---------------------------------------------------------------- TOTP (RFC 6238)
def new_totp_secret() -> str:
    return base64.b32encode(os.urandom(20)).decode().rstrip("=")


def _hotp(secret: str, counter: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    off = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[off:off + 4])[0] & 0x7FFFFFFF) % 1_000_000
    return f"{code:06d}"


def totp_now(secret: str, t: float | None = None) -> str:
    return _hotp(secret, int((t or time.time()) // 30))


def verify_totp(secret: str, code: str, window: int = 1) -> bool:
    code = (code or "").strip().replace(" ", "")
    if not (len(code) == 6 and code.isdigit()):
        return False
    counter = int(time.time() // 30)
    return any(hmac.compare_digest(_hotp(secret, counter + d), code) for d in range(-window, window + 1))


def totp_uri(secret: str, email: str) -> str:
    return f"otpauth://totp/CineVision%20Studio:{quote(email)}?secret={secret}&issuer=CineVision%20Studio"


def new_recovery_codes(n: int = 8) -> list[str]:
    return ["-".join(secrets.token_hex(2) for _ in range(3)) for _ in range(n)]


def hash_recovery(code: str) -> str:
    return hashlib.sha256(("cv-recovery:" + code.strip().lower()).encode()).hexdigest()


# ---------------------------------------------------------------- sessions
SESSION_COOKIE = "cv_session"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(user_id: str, mfa_passed: bool, ua: str | None) -> tuple[str, str]:
    s = settings()
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    now = db.now_iso()
    db.run(
        "INSERT INTO sessions(token_hash,user_id,csrf_token,mfa_passed,created_at,last_seen_at,expires_at,ua_hint)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (token_hash(token), user_id, csrf, int(mfa_passed), now, now,
         db.now_iso(timedelta(hours=s.session_absolute_hours)), _ua_hint(ua)),
    )
    return token, csrf


def _ua_hint(ua: str | None) -> str:
    ua = (ua or "").lower()
    for name in ("firefox", "edg", "chrome", "safari"):
        if name in ua:
            return {"edg": "edge"}.get(name, name)
    return "other"


def load_session(token: str | None):
    if not token or len(token) > 128:
        return None
    th = token_hash(token)
    row = db.one("SELECT * FROM sessions WHERE token_hash = ?", (th,))
    if row is None:
        return None
    now = db.utcnow()
    idle_limit = datetime.fromisoformat(row["last_seen_at"]) + timedelta(minutes=settings().session_idle_minutes)
    if datetime.fromisoformat(row["expires_at"]) < now or idle_limit < now:
        db.run("DELETE FROM sessions WHERE token_hash = ?", (th,))
        return None
    # throttle last_seen writes to once a minute
    if (now - datetime.fromisoformat(row["last_seen_at"])).total_seconds() > 60:
        db.run("UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?", (db.now_iso(), th))
    return row


def revoke_session(token: str) -> None:
    db.run("DELETE FROM sessions WHERE token_hash = ?", (token_hash(token),))


def revoke_user_sessions(user_id: str, except_hash: str | None = None) -> None:
    if except_hash:
        db.run("DELETE FROM sessions WHERE user_id = ? AND token_hash != ?", (user_id, except_hash))
    else:
        db.run("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def purge_expired_sessions() -> None:
    db.run("DELETE FROM sessions WHERE expires_at < ?", (db.now_iso(),))


# ---------------------------------------------------------------- rate limiting
class RateLimiter:
    """Sliding-window limiter kept in memory (single API process in local mode)."""

    def __init__(self):
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window_s: int) -> int | None:
        """Record a hit. Returns None if allowed, else seconds to wait."""
        limit *= settings().rate_limit_multiplier
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= now - window_s:
                q.popleft()
            if len(q) >= limit:
                return int(q[0] + window_s - now) + 1
            q.append(now)
            return None

    def reset(self, prefix: str = "") -> None:
        with self._lock:
            for k in [k for k in self._hits if k.startswith(prefix)]:
                del self._hits[k]


limiter = RateLimiter()

LOCKOUT_THRESHOLD = 10
LOCKOUT_MINUTES = 15


def is_locked(user_row) -> bool:
    lu = user_row["locked_until"]
    return bool(lu) and datetime.fromisoformat(lu) > datetime.now(timezone.utc)
