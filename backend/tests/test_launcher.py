"""The desktop launcher (what the one-click Windows zip runs): per-user folder, keys on first run, loopback only,
restart keeps the data readable, a second start just reuses the running server."""
import base64
import http.client
import json
import os
import subprocess
import sys
import threading
import time
import uuid

from conftest import BACKEND, PASSWORD, _free_port

SMOKE = BACKEND.parent / "scripts" / "smoke_server.py"


def _env(home):
    env = {k: v for k, v in os.environ.items() if not k.startswith("CINEVISION_")}
    env.update({"CINEVISION_HOME": str(home), "PYTHONPATH": str(BACKEND)})
    return env


def _start(home, port):
    proc = subprocess.Popen([sys.executable, "-m", "cinevision.launcher", "--no-browser", "--port", str(port)],
                            cwd=BACKEND, env=_env(home), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace")
    proc.lines = []          # read continuously so the "ready" line is captured however fast the test runs
    threading.Thread(target=lambda: proc.lines.extend(proc.stdout), daemon=True).start()
    return proc


def _wait(port, proc, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        assert proc.poll() is None, "".join(proc.lines)
        if any("CineVision is ready" in line for line in proc.lines):
            return               # the launcher only says "ready" after the server answers
        time.sleep(0.2)
    raise TimeoutError("".join(proc.lines))


def _stop(proc):
    proc.terminate()
    try:
        proc.wait(15)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(10)
    time.sleep(0.2)
    return "".join(proc.lines)


def _post(port, path, body, cookie=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    h = {"Origin": f"http://127.0.0.1:{port}", "Content-Type": "application/json"}
    if cookie:
        h["Cookie"] = cookie
    c.request("POST", path, json.dumps(body), h)
    r = c.getresponse()
    return r.status, r.getheader("set-cookie"), r.read()


def test_ensure_keys_creates_once(tmp_path):
    from cinevision import launcher
    master = launcher.ensure_keys(tmp_path / "keys")
    first = master.read_bytes()
    assert len(base64.b64decode(first.strip())) == 32
    assert len(base64.b64decode((tmp_path / "keys" / "backup_key").read_bytes().strip())) == 32
    assert first != (tmp_path / "keys" / "backup_key").read_bytes()
    launcher.ensure_keys(tmp_path / "keys")
    assert master.read_bytes() == first   # never overwritten


def test_check_mode_reports_loopback_and_per_user_folder(tmp_path):
    out = subprocess.run([sys.executable, "-m", "cinevision.launcher", "--check", "--port", "8123"], cwd=BACKEND,
                         env=_env(tmp_path / "home"), capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr
    info = json.loads(out.stdout)
    assert info["host"] == "127.0.0.1" and info["url"] == "http://127.0.0.1:8123"
    assert info["data_dir"] == str((tmp_path / "home" / "data").resolve())
    assert (tmp_path / "home" / "keys" / "master_key").is_file()


def test_launcher_starts_restarts_and_reuses_running_instance(tmp_path):
    home, port = tmp_path / "home", _free_port()
    email = f"desk-{uuid.uuid4().hex[:6]}@example.test"
    proc = _start(home, port)
    try:
        _wait(port, proc)
        status, _, body = _post(port, "/api/auth/register", {"email": email, "password": PASSWORD})
        assert status == 201, body
        # second start while running: just reuses it
        again = subprocess.run([sys.executable, "-m", "cinevision.launcher", "--no-browser", "--port", str(port)],
                               cwd=BACKEND, env=_env(home), capture_output=True, text=True, encoding="utf-8", timeout=60)
        assert again.returncode == 0 and "already running" in again.stdout
    finally:
        out = _stop(proc)
    assert "CineVision is ready" in out and f"http://127.0.0.1:{port}" in out
    assert (home / "data" / "cinevision.db").is_file() and (home / "keys" / "master_key").is_file()
    assert not (home / "keys").resolve().is_relative_to((home / "data").resolve())  # keys outside the data folder

    # restart with the same folder: same keys, the account still works
    proc = _start(home, port)
    try:
        _wait(port, proc)
        status, _, body = _post(port, "/api/auth/login", {"email": email, "password": PASSWORD})
        assert status == 200 and json.loads(body)["authenticated"]
    finally:
        _stop(proc)


def test_smoke_script_against_launcher(tmp_path):
    """scripts/smoke_server.py is what CI runs on Windows; make sure it passes against a good server here."""
    port = _free_port()
    r = subprocess.run([sys.executable, str(SMOKE), "--url", f"http://127.0.0.1:{port}", "--cwd", str(BACKEND),
                        "--timeout", "120", "--", sys.executable, "-m", "cinevision.launcher", "--no-browser",
                        "--port", str(port)], env=_env(tmp_path / "home"), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=200)
    frontend_built = (BACKEND.parent / "frontend" / "dist" / "index.html").exists()
    if frontend_built:
        assert r.returncode == 0, r.stdout + r.stderr
        assert "OK: health, web interface" in r.stdout
    else:  # without a built UI the check must fail loudly rather than pass
        assert r.returncode != 0 and "web interface not served" in (r.stdout + r.stderr)
