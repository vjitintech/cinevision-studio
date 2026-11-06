"""Sign-up modes (open / owner_only / closed), invite codes, the admin model and the network-exposure warning."""
import sqlite3
import uuid

import pytest

from conftest import PASSWORD, Client, start_server, stop_server


def _email(tag="u"):
    return f"{tag}-{uuid.uuid4().hex[:8]}@example.test"


def _register(port, email=None, invite=None, password=PASSWORD):
    c = Client(port)
    body = {"email": email or _email(), "password": password}
    if invite is not None:
        body["invite"] = invite
    r = c.post("/api/auth/register", body)
    c.email = body["email"]
    return c, r


@pytest.fixture(scope="module")
def invite_server(tmp_path_factory):
    proc, info = start_server(tmp_path_factory.mktemp("owner_only"), {"CINEVISION_SIGNUP": "owner_only"})
    yield info
    stop_server(proc)


@pytest.fixture(scope="module")
def closed_server(tmp_path_factory):
    # rate-limit multiplier 1: the real limits, so the sign-up rate limit can be checked here too
    proc, info = start_server(tmp_path_factory.mktemp("closed"), {"CINEVISION_SIGNUP": "closed",
                                                                   "CINEVISION_RATE_LIMIT_MULTIPLIER": "1"})
    yield info
    stop_server(proc)


@pytest.fixture(scope="module")
def lan_server(tmp_path_factory):
    proc, info = start_server(tmp_path_factory.mktemp("lan"), {"CINEVISION_HOST": "0.0.0.0"})
    yield info
    stop_server(proc)


# ---------------------------------------------------------------- open (default)
def test_open_signup_is_default_and_safe_by_default(server):
    c = Client(server["port"])
    s = c.get("/api/auth/session").json
    assert s["registration_open"] and s["signup"]["mode"] == "open" and not s["signup"]["invite_required"]
    a, r1 = _register(server["port"])
    b, r2 = _register(server["port"])
    assert r1.status == r2.status == 201
    sec = a.get("/api/admin/security").json
    # tests run on 127.0.0.1 → not reachable from the network → no warning
    assert sec["exposure"]["network"] is False and sec["exposure"]["warning"] is False
    assert sec["exposure"]["host"] == "127.0.0.1"
    assert "reachable from your network" not in (server["data"] / "server.log").read_text(encoding="utf-8", errors="replace")


def test_security_status_needs_sign_in(server):
    assert Client(server["port"]).get("/api/admin/security").status == 401


# ---------------------------------------------------------------- owner_only + invites + admins
def test_owner_only_first_account_then_invites(invite_server):
    port = invite_server["port"]
    s = Client(port).get("/api/auth/session").json
    assert s["signup"] == {"mode": "owner_only", "open": True, "invite_required": False, "first_account": True}
    owner, r = _register(port, "owner@example.test")
    assert r.status == 201
    assert owner.get("/api/admin/security").json["me"] == {"role": "owner", "is_owner": True, "is_admin": True}
    s = Client(port).get("/api/auth/session").json
    assert s["signup"]["invite_required"] and not s["signup"]["first_account"]

    # no code / wrong code: refused, and no account is left behind
    _, r = _register(port, "nocode@example.test")
    assert r.status == 403 and r.json["error"]["code"] == "invite_required"
    _, r = _register(port, "badcode@example.test", invite="AAAAA-BBBBB-CCCCC")
    assert r.status == 403 and r.json["error"]["code"] == "invalid_invite"
    assert Client(port).post("/api/auth/login", {"email": "badcode@example.test", "password": PASSWORD}).status == 401

    # the owner creates a code; it works once (case and dashes don't matter), then never again
    r = owner.post("/api/admin/invites")
    assert r.status == 201
    code = r.json["code"]
    assert len(code.replace("-", "")) == 15
    member, r = _register(port, "member@example.test", invite=code.lower().replace("-", " "))
    assert r.status == 201, r.body
    _, r = _register(port, "second@example.test", invite=code)
    assert r.status == 403 and r.json["error"]["code"] == "invalid_invite"
    invites = owner.get("/api/admin/invites").json["invites"]
    assert invites[0]["status"] == "used" and "code" not in invites[0]
    # the code itself is never stored
    raw = (invite_server["data"] / "cinevision.db").read_bytes()
    wal = invite_server["data"] / "cinevision.db-wal"
    raw += wal.read_bytes() if wal.exists() else b""
    assert code.encode() not in raw and code.replace("-", "").encode() not in raw


def test_expired_and_removed_invites_do_not_work(invite_server):
    port = invite_server["port"]
    owner = Client(port)
    assert owner.post("/api/auth/login", {"email": "owner@example.test", "password": PASSWORD}).status == 200
    expired = owner.post("/api/admin/invites").json
    with sqlite3.connect(invite_server["data"] / "cinevision.db", timeout=15) as db:
        db.execute("UPDATE signup_invites SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (expired["id"],))
    _, r = _register(port, invite=expired["code"])
    assert r.status == 403 and r.json["error"]["code"] == "invalid_invite"
    removed = owner.post("/api/admin/invites").json
    assert owner.delete(f"/api/admin/invites/{removed['id']}").status == 200
    assert owner.delete(f"/api/admin/invites/{removed['id']}").status == 404
    _, r = _register(port, invite=removed["code"])
    assert r.status == 403
    statuses = {i["id"]: i["status"] for i in owner.get("/api/admin/invites").json["invites"]}
    assert statuses[expired["id"]] == "expired" and removed["id"] not in statuses


def test_admin_model(invite_server):
    port = invite_server["port"]
    owner = Client(port)
    owner.post("/api/auth/login", {"email": "owner@example.test", "password": PASSWORD})
    member = Client(port)
    assert member.post("/api/auth/login", {"email": "member@example.test", "password": PASSWORD}).status == 200

    # a member can't manage anything
    assert member.get("/api/admin/security").json["me"]["role"] == "member"
    assert member.get("/api/admin/users").status == 403
    assert member.post("/api/admin/invites").status == 403
    assert member.get("/api/ai/local").json["can_edit"] is False
    assert member.put("/api/ai/local", {"url": "http://127.0.0.1:9/v1", "enabled": False}).status == 403

    users = owner.get("/api/admin/users").json["users"]
    roles = {u["email"]: u["role"] for u in users}
    assert roles["owner@example.test"] == "owner" and roles["member@example.test"] == "member"
    mid = next(u["id"] for u in users if u["email"] == "member@example.test")
    oid = next(u["id"] for u in users if u["email"] == "owner@example.test")

    # the owner makes the member an admin: AI settings and invites, but not role changes
    r = owner.put(f"/api/admin/users/{mid}/role", {"admin": True})
    assert r.status == 200 and {u["email"]: u["role"] for u in r.json["users"]}["member@example.test"] == "admin"
    assert member.get("/api/ai/local").json["can_edit"] is True
    assert member.put("/api/ai/local", {"url": "http://127.0.0.1:9/v1", "enabled": False}).status == 200
    assert member.post("/api/admin/invites").status == 201
    assert member.get("/api/admin/users").status == 403
    assert member.put(f"/api/admin/users/{oid}/role", {"admin": False}).status == 403

    # the owner can't be demoted; unknown ids are 404
    assert owner.put(f"/api/admin/users/{oid}/role", {"admin": False}).status == 409
    assert owner.put(f"/api/admin/users/{uuid.uuid4()}/role", {"admin": True}).status == 404
    assert owner.put("/api/admin/users/not-a-uuid/role", {"admin": True}).status == 404

    # and can take it back
    assert owner.put(f"/api/admin/users/{mid}/role", {"admin": False}).status == 200
    assert member.get("/api/ai/local").json["can_edit"] is False
    assert member.post("/api/admin/invites").status == 403
    events = [e["event"] for e in owner.get("/api/audit").json["events"]]
    assert "admin.role_change" in events and "admin.invite_create" in events


def test_role_changes_need_csrf(invite_server):
    owner = Client(invite_server["port"])
    owner.post("/api/auth/login", {"email": "owner@example.test", "password": PASSWORD})
    owner.csrf = None
    assert owner.post("/api/admin/invites").status == 403


# ---------------------------------------------------------------- closed (+ the real sign-up rate limit)
def test_closed_allows_only_the_first_account_and_rate_limits(closed_server):
    port = closed_server["port"]
    s = Client(port).get("/api/auth/session").json
    assert s["registration_open"] and s["signup"]["first_account"]
    owner, r = _register(port, "closed-owner@example.test")
    assert r.status == 201
    assert Client(port).get("/api/auth/session").json["registration_open"] is False
    inv = owner.post("/api/admin/invites")
    assert inv.status == 201  # can be prepared, but closed mode accepts no new accounts at all
    for _ in range(4):
        _, r = _register(port, invite=inv.json["code"])
        assert r.status == 403 and r.json["error"]["code"] == "registration_closed"
    _, r = _register(port)  # 6th attempt from this address within the hour
    assert r.status == 429 and int(r.headers["retry-after"]) > 0


# ---------------------------------------------------------------- network exposure
def test_lan_open_signup_warns_at_startup_and_on_security_page(lan_server):
    log = (lan_server["data"] / "server.log").read_text(encoding="utf-8", errors="replace")
    assert "reachable from your network" in log
    assert "Sign-up is OPEN" in log and "plain http" in log
    c, r = _register(lan_server["port"])
    assert r.status == 201
    exp = c.get("/api/admin/security").json["exposure"]
    assert exp == {"host": "0.0.0.0", "network": True, "plain_http": True, "signup_mode": "open", "warning": True}


# ---------------------------------------------------------------- configuration parsing (no server)
def test_signup_mode_parsing(monkeypatch):
    from cinevision import config
    for k in ("CINEVISION_SIGNUP", "CINEVISION_ALLOW_REGISTRATION", "CINEVISION_SIGNUP_FILE"):
        monkeypatch.delenv(k, raising=False)
    assert config._signup_mode() == "open"
    monkeypatch.setenv("CINEVISION_ALLOW_REGISTRATION", "false")   # legacy switch keeps its meaning
    assert config._signup_mode() == "owner_only"
    monkeypatch.setenv("CINEVISION_SIGNUP", "Owner-Only")
    assert config._signup_mode() == "owner_only"
    monkeypatch.setenv("CINEVISION_SIGNUP", "closed")
    assert config._signup_mode() == "closed"
    monkeypatch.setenv("CINEVISION_SIGNUP", "everyone")
    with pytest.raises(RuntimeError):
        config._signup_mode()


def test_loopback_detection():
    from cinevision.config import Settings, is_loopback_host
    for h in ("127.0.0.1", "127.1.2.3", "localhost", "::1", "[::1]"):
        assert is_loopback_host(h), h
    for h in ("0.0.0.0", "::", "192.168.1.20", "10.0.0.5", "example.com", "127.0.0.1.evil.com"):
        assert not is_loopback_host(h), h
    assert Settings(host="0.0.0.0").network_exposed
    assert not Settings(host="0.0.0.0", trust_proxy=True).network_exposed   # Docker: Caddy (HTTPS) in front
    assert not Settings().network_exposed


def test_friendly_password_rules():
    from cinevision.security import password_problems
    assert password_problems("four unrelated words here") == []          # no forced digits or symbols
    short = password_problems("short one")
    assert short and "12" in short[0] and "sentence" in short[0]
    assert password_problems("password1234")
    assert password_problems("aaaaaaaaaaaaaaaa")
    assert password_problems("filmmaker rocks 2026!", "filmmaker@example.test")
