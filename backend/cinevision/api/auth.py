from __future__ import annotations

import json
from datetime import timedelta

from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .. import audit, db, security
from ..config import settings
from ..keys import keyring
from ..web import ApiError, client_ip, current, ok, parse

GENERIC_LOGIN_ERROR = "Email or password is incorrect."


class Credentials(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class CodeBody(BaseModel):
    code: str = Field(min_length=6, max_length=32)


class PasswordBody(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class ChangePassword(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class DisableMfa(BaseModel):
    password: str = Field(min_length=1, max_length=256)
    code: str = Field(min_length=6, max_length=32)


def _norm_email(e: str) -> str:
    e = e.strip().lower()
    if "@" not in e or e.startswith("@") or e.endswith("@") or " " in e:
        raise ApiError(422, "validation_error", "Enter a valid email address.")
    return e


def registration_open() -> bool:
    if settings().allow_registration:
        return True
    return db.one("SELECT 1 FROM users LIMIT 1") is None


def _set_cookie(resp: JSONResponse, token: str) -> None:
    resp.set_cookie(security.SESSION_COOKIE, token, httponly=True, secure=settings().cookie_secure,
                    samesite="strict", path="/", max_age=settings().session_absolute_hours * 3600)


def _rate(key: str, limit: int, window: int) -> None:
    wait = security.limiter.hit(key, limit, window)
    if wait is not None:
        raise ApiError(429, "rate_limited", "Too many attempts. Try again shortly.", {"Retry-After": str(wait)})


async def session_info(request: Request):
    sess = security.load_session(request.cookies.get(security.SESSION_COOKIE))
    out = {"authenticated": False, "mfa_required": False, "user": None, "csrf_token": None,
           "registration_open": registration_open(), "mode": settings().mode}
    if sess:
        user = db.one("SELECT id,email,mfa_enabled FROM users WHERE id = ?", (sess["user_id"],))
        if user:
            out.update(authenticated=bool(sess["mfa_passed"]), mfa_required=not sess["mfa_passed"],
                       csrf_token=sess["csrf_token"],
                       user={"id": user["id"], "email": user["email"], "mfa_enabled": bool(user["mfa_enabled"])}
                       if sess["mfa_passed"] else None)
    return ok(out)


async def register(request: Request):
    ip = client_ip(request)
    _rate(f"register:{ip}", 5, 3600)
    body = await parse(request, Credentials)
    if not registration_open():
        raise ApiError(403, "registration_closed", "Registration is closed on this private instance.")
    email = _norm_email(body.email)
    problems = security.password_problems(body.password, email)
    if problems:
        raise ApiError(422, "weak_password", " ".join(problems))
    uid = db.new_id()
    try:
        with db.tx() as c:
            c.execute("INSERT INTO users(id,email,password_hash,created_at) VALUES (?,?,?,?)",
                      (uid, email, security.hash_password(body.password), db.now_iso()))
            c.execute("INSERT INTO privacy_settings(user_id) VALUES (?)", (uid,))
    except Exception:
        # don't reveal whether the address exists
        raise ApiError(409, "registration_failed", "Could not register with these details.")
    audit.record("auth.register", user_id=uid, ip=ip)
    token, csrf = security.create_session(uid, True, request.headers.get("user-agent"))
    resp = ok({"authenticated": True, "csrf_token": csrf, "user": {"id": uid, "email": email, "mfa_enabled": False}}, 201)
    _set_cookie(resp, token)
    return resp


async def login(request: Request):
    ip = client_ip(request)
    _rate(f"login-ip:{ip}", 20, 300)
    body = await parse(request, Credentials)
    email = body.email.strip().lower()
    _rate(f"login-acct:{email}", 10, 300)
    user = db.one("SELECT * FROM users WHERE email = ?", (email,))
    if user is not None and security.is_locked(user):
        security.verify_password(body.password, None)
        audit.record("auth.login", user_id=user["id"], outcome="denied", ip=ip, details={"reason": "locked"})
        raise ApiError(401, "invalid_credentials", GENERIC_LOGIN_ERROR)
    if not security.verify_password(body.password, user["password_hash"] if user else None):
        if user is not None:
            fails = user["failed_logins"] + 1
            locked = db.now_iso(timedelta(minutes=security.LOCKOUT_MINUTES)) \
                if fails >= security.LOCKOUT_THRESHOLD else None
            db.run("UPDATE users SET failed_logins = ?, locked_until = ? WHERE id = ?",
                   (0 if locked else fails, locked, user["id"]))
        audit.record("auth.login", user_id=user["id"] if user else None, outcome="failure", ip=ip)
        raise ApiError(401, "invalid_credentials", GENERIC_LOGIN_ERROR)
    db.run("UPDATE users SET failed_logins = 0, locked_until = NULL, last_login_at = ? WHERE id = ?",
           (db.now_iso(), user["id"]))
    old = request.cookies.get(security.SESSION_COOKIE)
    if old:
        security.revoke_session(old)
    mfa = bool(user["mfa_enabled"])
    token, csrf = security.create_session(user["id"], not mfa, request.headers.get("user-agent"))
    audit.record("auth.login", user_id=user["id"], ip=ip, details={"status": "mfa_pending" if mfa else "ok"})
    resp = ok({"authenticated": not mfa, "mfa_required": mfa, "csrf_token": csrf,
               "user": None if mfa else {"id": user["id"], "email": user["email"], "mfa_enabled": False}})
    _set_cookie(resp, token)
    return resp


async def mfa_verify(request: Request):
    ctx = current(request, require_mfa=False)
    _rate(f"mfa:{ctx.uid}", 6, 300)
    body = await parse(request, CodeBody)
    user = db.one("SELECT * FROM users WHERE id = ?", (ctx.uid,))
    if not user["mfa_enabled"]:
        raise ApiError(400, "mfa_not_enabled", "Two-factor is not enabled.")
    secret = keyring().dec(user["mfa_secret_enc"], f"users:mfa_secret:{user['id']}")
    passed, method = security.verify_totp(secret, body.code), "totp"
    if not passed:
        codes = json.loads(user["recovery_codes"] or "[]")
        h = security.hash_recovery(body.code)
        if h in codes:
            codes.remove(h)
            db.run("UPDATE users SET recovery_codes = ? WHERE id = ?", (json.dumps(codes), user["id"]))
            passed, method = True, "recovery_code"
    if not passed:
        audit.record("auth.mfa_verify", user_id=ctx.uid, outcome="failure", ip=ctx.ip)
        raise ApiError(401, "invalid_code", "That code is not valid.")
    # rotate the session on privilege change
    security.revoke_session(request.cookies.get(security.SESSION_COOKIE))
    token, csrf = security.create_session(ctx.uid, True, request.headers.get("user-agent"))
    audit.record("auth.mfa_verify", user_id=ctx.uid, ip=ctx.ip, details={"method": method})
    resp = ok({"authenticated": True, "csrf_token": csrf,
               "user": {"id": user["id"], "email": user["email"], "mfa_enabled": True}})
    _set_cookie(resp, token)
    return resp


async def logout(request: Request):
    tok = request.cookies.get(security.SESSION_COOKIE)
    sess = security.load_session(tok)
    if tok:
        security.revoke_session(tok)
    if sess:
        audit.record("auth.logout", user_id=sess["user_id"], ip=client_ip(request))
    resp = ok()
    resp.delete_cookie(security.SESSION_COOKIE, path="/")
    return resp


async def logout_all(request: Request):
    ctx = current(request)
    security.revoke_user_sessions(ctx.uid)
    audit.record("auth.logout_all", user_id=ctx.uid, ip=ctx.ip)
    resp = ok()
    resp.delete_cookie(security.SESSION_COOKIE, path="/")
    return resp


async def change_password(request: Request):
    ctx = current(request)
    _rate(f"pw:{ctx.uid}", 5, 900)
    body = await parse(request, ChangePassword)
    user = db.one("SELECT * FROM users WHERE id = ?", (ctx.uid,))
    if not security.verify_password(body.current_password, user["password_hash"]):
        audit.record("auth.password_change", user_id=ctx.uid, outcome="failure", ip=ctx.ip)
        raise ApiError(401, "invalid_credentials", "Current password is incorrect.")
    problems = security.password_problems(body.new_password, user["email"])
    if problems:
        raise ApiError(422, "weak_password", " ".join(problems))
    db.run("UPDATE users SET password_hash = ? WHERE id = ?", (security.hash_password(body.new_password), ctx.uid))
    security.revoke_user_sessions(ctx.uid, except_hash=ctx.session["token_hash"])
    audit.record("auth.password_change", user_id=ctx.uid, ip=ctx.ip)
    return ok()


async def mfa_setup(request: Request):
    ctx = current(request)
    body = await parse(request, PasswordBody)
    user = db.one("SELECT * FROM users WHERE id = ?", (ctx.uid,))
    if not security.verify_password(body.password, user["password_hash"]):
        raise ApiError(401, "invalid_credentials", "Password is incorrect.")
    if user["mfa_enabled"]:
        raise ApiError(400, "mfa_already_enabled", "Two-factor is already enabled.")
    secret = security.new_totp_secret()
    db.run("UPDATE users SET mfa_secret_enc = ? WHERE id = ?",
           (keyring().enc(secret, f"users:mfa_secret:{ctx.uid}"), ctx.uid))
    return ok({"secret": secret, "otpauth_uri": security.totp_uri(secret, user["email"])})


async def mfa_enable(request: Request):
    ctx = current(request)
    _rate(f"mfa-enable:{ctx.uid}", 6, 300)
    body = await parse(request, CodeBody)
    user = db.one("SELECT * FROM users WHERE id = ?", (ctx.uid,))
    if user["mfa_enabled"] or not user["mfa_secret_enc"]:
        raise ApiError(400, "mfa_setup_required", "Start two-factor setup first.")
    secret = keyring().dec(user["mfa_secret_enc"], f"users:mfa_secret:{ctx.uid}")
    if not security.verify_totp(secret, body.code):
        raise ApiError(401, "invalid_code", "That code is not valid. Check your authenticator's clock.")
    codes = security.new_recovery_codes()
    db.run("UPDATE users SET mfa_enabled = 1, recovery_codes = ? WHERE id = ?",
           (json.dumps([security.hash_recovery(c) for c in codes]), ctx.uid))
    security.revoke_user_sessions(ctx.uid, except_hash=ctx.session["token_hash"])
    audit.record("auth.mfa_enable", user_id=ctx.uid, ip=ctx.ip)
    return ok({"recovery_codes": codes})


async def mfa_disable(request: Request):
    ctx = current(request)
    body = await parse(request, DisableMfa)
    user = db.one("SELECT * FROM users WHERE id = ?", (ctx.uid,))
    if not user["mfa_enabled"]:
        raise ApiError(400, "mfa_not_enabled", "Two-factor is not enabled.")
    secret = keyring().dec(user["mfa_secret_enc"], f"users:mfa_secret:{ctx.uid}")
    if not (security.verify_password(body.password, user["password_hash"]) and security.verify_totp(secret, body.code)):
        audit.record("auth.mfa_disable", user_id=ctx.uid, outcome="failure", ip=ctx.ip)
        raise ApiError(401, "invalid_credentials", "Password or code is incorrect.")
    db.run("UPDATE users SET mfa_enabled = 0, mfa_secret_enc = NULL, recovery_codes = NULL WHERE id = ?", (ctx.uid,))
    audit.record("auth.mfa_disable", user_id=ctx.uid, ip=ctx.ip)
    return ok()


routes = [
    Route("/api/auth/session", session_info, methods=["GET"]),
    Route("/api/auth/register", register, methods=["POST"]),
    Route("/api/auth/login", login, methods=["POST"]),
    Route("/api/auth/mfa/verify", mfa_verify, methods=["POST"]),
    Route("/api/auth/logout", logout, methods=["POST"]),
    Route("/api/auth/logout-all", logout_all, methods=["POST"]),
    Route("/api/auth/password", change_password, methods=["POST"]),
    Route("/api/auth/mfa/setup", mfa_setup, methods=["POST"]),
    Route("/api/auth/mfa/enable", mfa_enable, methods=["POST"]),
    Route("/api/auth/mfa/disable", mfa_disable, methods=["POST"]),
]
