#!/usr/bin/env python3
"""Start a CineVision launcher non-interactively, check it really works, stop it. Used by CI on Windows.

    python scripts/smoke_server.py --url http://127.0.0.1:8765 [--cwd DIR] [--timeout 300] -- <command> [args...]

Checks: /api/health answers, the web interface is served, a weak password is refused, an account can be
created and is signed in. The command's whole process tree is stopped afterwards (taskkill /T on Windows,
process group on Linux/macOS). Exit code 0 = all good. Standard library only.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from urllib.parse import urlsplit

WINDOWS = sys.platform == "win32"


def request(base, method, path, body=None, headers=None, timeout=10):
    u = urlsplit(base)
    c = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=timeout)
    h = dict(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    payload = r.read()
    return r.status, dict(r.getheaders()), payload


def wait_health(base, deadline, proc):
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise SystemExit(f"FAIL: the command exited early with code {proc.returncode}")
        try:
            st, _, body = request(base, "GET", "/api/health", timeout=3)
            if st == 200 and json.loads(body).get("status") == "ok":
                return
        except (OSError, ValueError):
            pass
        time.sleep(1)
    raise SystemExit("FAIL: /api/health did not answer in time")


def check(base):
    origin = base.rstrip("/")
    st, hdr, body = request(base, "GET", "/")
    assert st == 200 and b"<html" in body.lower(), f"web interface not served (HTTP {st})"
    st, _, body = request(base, "GET", "/api/auth/session")
    session = json.loads(body)
    assert st == 200 and "registration_open" in session, body
    email = f"smoke-{uuid.uuid4().hex[:8]}@example.test"
    st, _, body = request(base, "POST", "/api/auth/register", {"email": email, "password": "short"}, {"Origin": origin})
    assert st == 422, f"weak password not refused (HTTP {st}): {body[:200]!r}"
    st, hdr, body = request(base, "POST", "/api/auth/register",
                            {"email": email, "password": "smoke test passphrase 2026"}, {"Origin": origin}, timeout=60)
    assert st == 201, f"registration failed (HTTP {st}): {body[:300]!r}"
    cookie = (hdr.get("set-cookie") or hdr.get("Set-Cookie") or "").split(";")[0]
    st, _, body = request(base, "GET", "/api/auth/session", headers={"Cookie": cookie})
    s = json.loads(body)
    assert s.get("authenticated") and s["user"]["email"] == email, body
    st, _, body = request(base, "GET", "/api/admin/security", headers={"Cookie": cookie})
    assert st == 200, f"/api/admin/security HTTP {st}"
    sec = json.loads(body)
    assert sec["me"]["is_owner"] or not s.get("signup", {}).get("first_account"), sec
    print(f"OK: health, web interface, password rules, sign-up ({sec['signup']['mode']}), "
          f"network exposed: {sec['exposure']['network']}")


def stop(proc):
    if proc.poll() is not None:
        return
    if WINDOWS:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        proc.wait(30)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(10)


def main():
    if "--" not in sys.argv:
        raise SystemExit(__doc__)
    i = sys.argv.index("--")
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--cwd", default=None)
    ap.add_argument("--timeout", type=int, default=300)
    args = ap.parse_args(sys.argv[1:i])
    cmd = sys.argv[i + 1:]
    kw = {"cwd": args.cwd, "stdin": subprocess.DEVNULL}
    if WINDOWS:
        kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    print("starting:", " ".join(cmd), flush=True)
    proc = subprocess.Popen(cmd, **kw)
    try:
        wait_health(args.url, time.monotonic() + args.timeout, proc)
        check(args.url)
    except AssertionError as e:
        raise SystemExit(f"FAIL: {e}") from None
    finally:
        stop(proc)


if __name__ == "__main__":
    main()
