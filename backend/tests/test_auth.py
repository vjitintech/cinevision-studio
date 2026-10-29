import base64
import time
import uuid

from conftest import ORIGIN, PASSWORD, Client


def test_register_login_logout_cycle(server):
    c = Client(server["port"])
    email = f"a-{uuid.uuid4().hex[:6]}@example.test"
    assert c.post("/api/auth/register", {"email": email, "password": PASSWORD}).status == 201
    s = c.get("/api/auth/session").json
    assert s["authenticated"] and s["user"]["email"] == email
    assert c.post("/api/auth/logout").status == 200
    assert c.get("/api/projects").status == 401
    r = c.post("/api/auth/login", {"email": email, "password": PASSWORD})
    assert r.status == 200 and r.json["authenticated"]
    assert c.get("/api/projects").status == 200


def test_session_cookie_flags(server):
    c = Client(server["port"])
    r = c.post("/api/auth/register", {"email": f"f-{uuid.uuid4().hex[:6]}@example.test", "password": PASSWORD})
    cookie = r.headers.get("set-cookie")
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie.replace("Strict", "strict")


def test_weak_password_rejected(client):
    r = client.post("/api/auth/register", {"email": "weak@example.test", "password": "short"})
    assert r.status == 422 and r.json["error"]["code"] == "weak_password"
    r = client.post("/api/auth/register", {"email": "weak2@example.test", "password": "password123"})
    assert r.status == 422


def test_login_error_is_uniform(user, server):
    c = Client(server["port"])
    a = c.post("/api/auth/login", {"email": user.email, "password": "wrong-password-xyz"})
    b = c.post("/api/auth/login", {"email": "nobody@example.test", "password": "wrong-password-xyz"})
    assert a.status == b.status == 401
    assert a.json["error"] == b.json["error"]


def test_lockout_after_repeated_failures(user, server):
    c = Client(server["port"])
    for _ in range(10):
        assert c.post("/api/auth/login", {"email": user.email, "password": "nope-nope-nope"}).status == 401
    # correct password is refused while locked
    assert c.post("/api/auth/login", {"email": user.email, "password": PASSWORD}).status == 401


def test_csrf_and_origin_enforced(user):
    good = user.post("/api/projects", {"title": "ok"})
    assert good.status == 201
    tok = user.csrf
    user.csrf = None
    r = user.post("/api/projects", {"title": "no token"})
    assert r.status == 403 and r.json["error"]["code"] == "csrf_failed"
    user.csrf = "forged"
    assert user.post("/api/projects", {"title": "bad token"}).status == 403
    user.csrf = tok
    user.origin = "https://evil.example"
    r = user.post("/api/projects", {"title": "cross origin"})
    assert r.status == 403 and r.json["error"]["code"] == "bad_origin"
    user.origin = None
    assert user.post("/api/projects", {"title": "no origin"}).status == 403
    user.origin = ORIGIN


def test_security_headers(user):
    r = user.get("/api/projects")
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["referrer-policy"] == "no-referrer"


def _totp(secret):
    import sys
    sys.path.insert(0, "")
    from cinevision.security import totp_now
    return totp_now(secret)


def test_mfa_enrolment_and_login(user, server):
    r = user.post("/api/auth/mfa/setup", {"password": PASSWORD})
    assert r.status == 200
    secret = r.json["secret"]
    assert user.post("/api/auth/mfa/enable", {"code": "000000"}).status == 401
    r = user.post("/api/auth/mfa/enable", {"code": _totp(secret)})
    assert r.status == 200
    codes = r.json["recovery_codes"]
    assert len(codes) == 8

    c = Client(server["port"])
    r = c.post("/api/auth/login", {"email": user.email, "password": PASSWORD})
    assert r.json["mfa_required"] and not r.json["authenticated"]
    assert c.get("/api/projects").status == 401  # half-authenticated session cannot read data
    assert c.post("/api/auth/mfa/verify", {"code": "123456"}).status == 401
    assert c.post("/api/auth/mfa/verify", {"code": _totp(secret)}).status == 200
    assert c.get("/api/projects").status == 200

    # recovery code works exactly once
    c2 = Client(server["port"])
    c2.post("/api/auth/login", {"email": user.email, "password": PASSWORD})
    assert c2.post("/api/auth/mfa/verify", {"code": codes[0]}).status == 200
    c3 = Client(server["port"])
    c3.post("/api/auth/login", {"email": user.email, "password": PASSWORD})
    assert c3.post("/api/auth/mfa/verify", {"code": codes[0]}).status == 401


def test_password_change_revokes_other_sessions(user, server):
    other = Client(server["port"])
    other.post("/api/auth/login", {"email": user.email, "password": PASSWORD})
    assert other.get("/api/projects").status == 200
    new_pw = "an even longer passphrase 42"
    assert user.post("/api/auth/password", {"current_password": PASSWORD, "new_password": new_pw}).status == 200
    assert other.get("/api/projects").status == 401
    assert user.get("/api/projects").status == 200


def test_audit_log_records_login(user):
    events = user.get("/api/audit").json["events"]
    assert any(e["event"] == "auth.register" for e in events)
    assert all("password" not in str(e) for e in events)
