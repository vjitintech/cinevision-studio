"""Integration test harness: runs the real ASGI app under uvicorn in a subprocess (with the embedded worker)
and talks to it over HTTP, so middleware, cookies, CSRF and streaming are exercised exactly as in production."""
from __future__ import annotations

import base64
import http.client
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from http.cookies import SimpleCookie
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ORIGIN = "http://testserver.local"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def server(tmp_path_factory):
    data = tmp_path_factory.mktemp("data")
    port = _free_port()
    key = base64.b64encode(os.urandom(32)).decode()
    env = {**os.environ, "CINEVISION_MASTER_KEY": key, "CINEVISION_DATA_DIR": str(data),
           "CINEVISION_COOKIE_SECURE": "false", "CINEVISION_PUBLIC_ORIGIN": ORIGIN,
           "CINEVISION_EMBEDDED_WORKER": "true", "CINEVISION_ALLOW_REGISTRATION": "true",
           "CINEVISION_MAX_UPLOAD_MB": "40", "CINEVISION_RATE_LIMIT_MULTIPLIER": "50", "CINEVISION_MAX_DURATION_S": "60", "PYTHONPATH": str(BACKEND)}
    log = open(data / "server.log", "w")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "cinevision.app:create_app", "--factory",
                             "--port", str(port), "--no-access-log"], cwd=BACKEND, env=env, stdout=log, stderr=log)
    for _ in range(100):
        try:
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            c.request("GET", "/api/health")
            if c.getresponse().status == 200:
                break
        except OSError:
            time.sleep(0.1)
    else:
        proc.kill()
        raise RuntimeError((data / "server.log").read_text())
    yield {"port": port, "data": data, "key": key}
    proc.terminate()
    proc.wait(10)


class Client:
    def __init__(self, port: int):
        self.port = port
        self.cookies: dict[str, str] = {}
        self.csrf: str | None = None
        self.origin = ORIGIN

    def request(self, method, path, body=None, headers=None, raw=None, csrf=True):
        h = {"Origin": self.origin} if self.origin else {}
        if self.cookies:
            h["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        if csrf and self.csrf:
            h["X-CSRF-Token"] = self.csrf
        data = raw
        if body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=120)
        c.request(method, path, body=data, headers=h)
        r = c.getresponse()
        payload = r.read()
        for sc in r.headers.get_all("set-cookie") or []:
            ck = SimpleCookie(sc)
            for k, m in ck.items():
                if m.value == "" or m["max-age"] == "0" or "1970" in (m["expires"] or ""):
                    self.cookies.pop(k, None)
                else:
                    self.cookies[k] = m.value
        r.body = payload
        try:
            r.json = json.loads(payload) if payload and "json" in (r.headers.get("content-type") or "") else None
        except json.JSONDecodeError:
            r.json = None
        if isinstance(r.json, dict) and r.json.get("csrf_token"):
            self.csrf = r.json["csrf_token"]
        return r

    get = lambda self, p, **kw: self.request("GET", p, **kw)  # noqa: E731
    post = lambda self, p, body=None, **kw: self.request("POST", p, body if body is not None else {}, **kw)  # noqa: E731
    put = lambda self, p, body=None, **kw: self.request("PUT", p, body, **kw)  # noqa: E731
    patch = lambda self, p, body=None, **kw: self.request("PATCH", p, body, **kw)  # noqa: E731
    delete = lambda self, p, **kw: self.request("DELETE", p, **kw)  # noqa: E731

    def upload(self, pid: str, path: Path, filename: str | None = None):
        boundary = uuid.uuid4().hex
        content = path.read_bytes()
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename or path.name}\"\r\n"
                f"Content-Type: application/octet-stream\r\n\r\n").encode() + content + f"\r\n--{boundary}--\r\n".encode()
        return self.request("POST", f"/api/projects/{pid}/video", raw=body,
                            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})


PASSWORD = "correct horse battery staple"


@pytest.fixture
def client(server):
    return Client(server["port"])


@pytest.fixture
def user(server):
    c = Client(server["port"])
    email = f"dir-{uuid.uuid4().hex[:8]}@example.test"
    r = c.post("/api/auth/register", {"email": email, "password": PASSWORD})
    assert r.status == 201, r.body
    c.email = email
    return c


def texture(path: Path, w=2400, h=1350, seed=1) -> Path:
    """Deterministic, richly textured still (rectangles, circles, lines) — ideal for verifying motion tracking."""
    import cv2
    import numpy as np
    rng = np.random.default_rng(seed)
    img = np.full((h, w, 3), (40, 60, 80), np.uint8)
    for _ in range(900):
        c = tuple(int(x) for x in rng.integers(0, 255, 3))
        x, y = int(rng.integers(0, w)), int(rng.integers(0, h))
        kind = rng.integers(0, 3)
        if kind == 0:
            cv2.rectangle(img, (x, y), (x + int(rng.integers(8, 90)), y + int(rng.integers(8, 90))), c, -1)
        elif kind == 1:
            cv2.circle(img, (x, y), int(rng.integers(4, 40)), c, -1)
        else:
            cv2.line(img, (x, y), (x + int(rng.integers(-150, 150)), y + int(rng.integers(-150, 150))), c, 3)
    cv2.imwrite(str(path), img)
    return path


def _zoom_clip(path, tex, duration, w, h, fps, codec):
    """Exact centre zoom rendered in Python (12% per second) and piped to ffmpeg."""
    import cv2
    img = cv2.imread(str(tex))
    H, W = img.shape[:2]
    proc = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}",
                             "-r", str(fps), "-i", "-", "-c:v", codec, "-pix_fmt", "yuv420p", str(path)], stdin=subprocess.PIPE)
    for i in range(int(duration * fps)):
        z = 1 + 0.12 * i / fps
        cw, ch = 1280 / z, 720 / z
        x0, y0 = (W - cw) / 2, (H - ch) / 2
        M = cv2.getAffineTransform(
            __import__("numpy").float32([[x0, y0], [x0 + cw, y0], [x0, y0 + ch]]),
            __import__("numpy").float32([[0, 0], [w, 0], [0, h]]))
        proc.stdin.write(cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_AREA).tobytes())
    proc.stdin.close()
    assert proc.wait() == 0
    return path


def make_clip(path: Path, *, duration=4, size="640x360", pattern="pan", fps=24, codec="libx264", extra=None):
    """Synthetic clips with known ground truth, built from still textures so the only motion is the camera's.
    pan: camera pans right · static: locked off · zoom: push in · cut: two different scenes."""
    tex = texture(path.with_suffix(".tex1.png"), seed=1)
    w, h = (int(x) for x in size.split("x"))
    if pattern == "pan":
        cmd = ["-loop", "1", "-framerate", str(fps), "-t", str(duration), "-i", str(tex),
               "-vf", f"crop=1280:720:x='t*160':y=300,scale={w}:{h}"]
    elif pattern == "static":
        cmd = ["-loop", "1", "-framerate", str(fps), "-t", str(duration), "-i", str(tex),
               "-vf", f"crop=1280:720:400:300,scale={w}:{h}"]
    elif pattern == "zoom":
        return _zoom_clip(path, tex, duration, w, h, fps, codec)
    elif pattern == "cut":
        tex2 = texture(path.with_suffix(".tex2.png"), seed=9)
        half = duration / 2
        cmd = ["-loop", "1", "-framerate", str(fps), "-t", str(half), "-i", str(tex),
               "-loop", "1", "-framerate", str(fps), "-t", str(half), "-i", str(tex2),
               "-filter_complex", f"[0:v]crop=1280:720:400:300,scale={w}:{h},setsar=1[a];"
                                  f"[1:v]eq=brightness=-0.25,crop=1280:720:0:0,scale={w}:{h},setsar=1[b];[a][b]concat=n=2:v=1:a=0[v]",
               "-map", "[v]"]
    else:
        raise ValueError(pattern)
    full = ["ffmpeg", "-y", "-loglevel", "error", *cmd, "-c:v", codec, "-pix_fmt", "yuv420p", "-r", str(fps),
            *(extra or []), str(path)]
    subprocess.run(full, check=True)
    return path


@pytest.fixture(scope="session")
def clips(tmp_path_factory):
    d = tmp_path_factory.mktemp("clips")
    return {
        "pan": make_clip(d / "pan.mp4", pattern="pan"),
        "static": make_clip(d / "static.mp4", pattern="static", duration=3),
        "cut": make_clip(d / "cut.mp4", pattern="cut", duration=6),
        "zoom": make_clip(d / "zoom.mp4", pattern="zoom", duration=4),
        "long": make_clip(d / "long.mp4", pattern="static", duration=70, size="160x90", fps=5),
        "tiny": make_clip(d / "tiny.mp4", pattern="static", duration=2, size="32x32"),
    }


def wait_jobs(c: Client, pid: str, timeout=180):
    deadline = time.time() + timeout
    while time.time() < deadline:
        jobs = c.get(f"/api/projects/{pid}/jobs").json["jobs"]
        if jobs and all(j["status"] in ("completed", "failed", "cancelled") for j in jobs):
            return jobs
        time.sleep(0.5)
    raise TimeoutError(jobs)
