"""Desktop launcher: start CineVision on this computer, wait until it answers, open the browser.

Used by the one-click Windows app (``CineVision.cmd`` runs ``python -m cinevision.launcher``) and usable from a
source checkout too. Everything is automatic:

* data, keys and logs live in one per-user folder: ``%LOCALAPPDATA%\\CineVision`` on Windows,
  ``~/Library/Application Support/CineVision`` on macOS, ``$XDG_DATA_HOME/CineVision`` elsewhere
  (override with ``CINEVISION_HOME``);
* encryption keys are generated on first run in ``<home>/keys`` (outside the data folder);
* a bundled FFmpeg (``<app root>/ffmpeg/bin``) and the bundled face model are used when present;
* the server listens on 127.0.0.1 only, unless ``CINEVISION_HOST`` says otherwise;
* if CineVision is already running, the browser is simply opened on it.

Only the standard library is imported before the environment is prepared, so settings are read correctly.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

PKG = Path(__file__).resolve().parent            # .../backend/cinevision
BACKEND = PKG.parent                              # .../backend
APP_ROOT = BACKEND.parent                         # repository root, or <portable>/app
PORTABLE_ROOT = APP_ROOT.parent                   # <portable> (only meaningful in the zip layout)


def default_home() -> Path:
    env = os.environ.get("CINEVISION_HOME")
    if env:
        return Path(env)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "CineVision"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "CineVision"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "CineVision"


def ensure_keys(keys_dir: Path) -> Path:
    """Create master_key and backup_key once (same format as scripts/gen_keys.py). Never overwrites."""
    keys_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(keys_dir, 0o700)  # POSIX; on Windows the per-user AppData folder is already private
    except OSError:
        pass
    for name in ("master_key", "backup_key"):
        p = keys_dir / name
        if p.exists() and p.stat().st_size > 0:
            continue
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(base64.b64encode(os.urandom(32)) + b"\n")
    return keys_dir / "master_key"


def find_ffmpeg() -> tuple[str | None, str | None]:
    bundled = PORTABLE_ROOT / "ffmpeg" / "bin"
    exe = ".exe" if sys.platform == "win32" else ""
    if (bundled / f"ffmpeg{exe}").is_file() and (bundled / f"ffprobe{exe}").is_file():
        return str(bundled / f"ffmpeg{exe}"), str(bundled / f"ffprobe{exe}")
    return shutil.which("ffmpeg"), shutil.which("ffprobe")


def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def is_cinevision(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/health", timeout=timeout) as r:  # noqa: S310 - loopback
            return r.status == 200 and json.loads(r.read() or b"{}").get("status") == "ok"
    except Exception:
        return False


def _browser_host(host: str) -> str:
    return "127.0.0.1" if host in ("0.0.0.0", "::", "") else host


def prepare(home: Path, port: int | None = None) -> dict:
    """Set the environment variables the server reads. Existing variables win, so power users can override."""
    home.mkdir(parents=True, exist_ok=True)
    env = os.environ
    host = env.get("CINEVISION_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = int(port or env.get("CINEVISION_PORT") or 8000)
    master = ensure_keys(home / "keys")
    ffmpeg, ffprobe = find_ffmpeg()
    if ffmpeg:
        env["PATH"] = str(Path(ffmpeg).parent) + os.pathsep + env.get("PATH", "")
        env.setdefault("CINEVISION_FFMPEG", ffmpeg)
        env.setdefault("CINEVISION_FFPROBE", ffprobe or "ffprobe")
    if sys.platform == "win32":  # optional OCR engine from the usual installer location
        for tess in (Path(env.get("ProgramFiles", r"C:\Program Files")) / "Tesseract-OCR", PORTABLE_ROOT / "tesseract"):
            if (tess / "tesseract.exe").is_file():
                env["PATH"] = str(tess) + os.pathsep + env["PATH"]
                break
    face = PKG / "models" / "face_detection_yunet_2023mar.onnx"
    if face.is_file():
        env.setdefault("CINEVISION_FACE_MODEL", str(face))
    if not env.get("CINEVISION_MASTER_KEY"):
        env.setdefault("CINEVISION_MASTER_KEY_FILE", str(master))
    env.setdefault("CINEVISION_DATA_DIR", str(home / "data"))
    origin = f"http://{_browser_host(host)}:{port}"
    env["CINEVISION_HOST"], env["CINEVISION_PORT"] = host, str(port)
    env.setdefault("CINEVISION_PUBLIC_ORIGIN", origin)
    extra = [o for o in env.get("CINEVISION_EXTRA_ORIGINS", "").split(",") if o.strip()]
    candidates = [f"http://localhost:{port}", f"http://127.0.0.1:{port}"]
    if host not in ("127.0.0.1", "localhost", "::1"):  # explicit network mode: accept this PC's own addresses too
        try:
            candidates += [f"http://{ip}:{port}" for ip in socket.gethostbyname_ex(socket.gethostname())[2]]
        except OSError:
            pass
    for o in candidates:
        if o not in extra and o != env["CINEVISION_PUBLIC_ORIGIN"]:
            extra.append(o)
    env["CINEVISION_EXTRA_ORIGINS"] = ",".join(extra)
    env.setdefault("CINEVISION_COOKIE_SECURE", "false")   # plain http is only acceptable on this computer
    env.setdefault("CINEVISION_EMBEDDED_WORKER", "true")
    dist = APP_ROOT / "frontend" / "dist"
    if dist.is_dir():
        env.setdefault("CINEVISION_FRONTEND_DIST", str(dist))
    return {"home": str(home), "host": host, "port": port, "url": origin, "ffmpeg": ffmpeg, "ffprobe": ffprobe,
            "data_dir": env["CINEVISION_DATA_DIR"], "keys_dir": str(home / "keys"),
            "frontend": env.get("CINEVISION_FRONTEND_DIST")}


def _say(msg: str = "") -> None:
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:  # old Windows consoles
        print(msg.encode("ascii", "replace").decode(), flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="CineVision", description="Start CineVision Studio on this computer.")
    ap.add_argument("--port", type=int, default=None, help="port to use (default 8000, or the next free one)")
    ap.add_argument("--home", type=Path, default=None, help="folder for data, keys and logs")
    ap.add_argument("--no-browser", action="store_true", help="don't open the browser")
    ap.add_argument("--check", action="store_true", help="prepare everything, print the settings as JSON and exit")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(errors="replace")
            except (ValueError, OSError):
                pass

    home = (args.home or default_home()).resolve()
    host = os.environ.get("CINEVISION_HOST", "127.0.0.1").strip() or "127.0.0.1"
    want = args.port or int(os.environ.get("CINEVISION_PORT") or 8000)
    port = want
    if not args.check and not _port_free(host, want):
        url = f"http://{_browser_host(host)}:{want}"
        if is_cinevision(url):
            _say(f"CineVision is already running at {url}")
            if not args.no_browser:
                webbrowser.open(url)
            return 0
        port = next((p for p in range(want + 1, want + 20) if _port_free(host, p)), 0)
        if not port:
            _say(f"Port {want} and the next 19 are busy. Close the program using them, or set CINEVISION_PORT.")
            return 3
        _say(f"Port {want} is used by another program, so CineVision uses port {port}.")
    info = prepare(home, port)
    if args.check:
        _say(json.dumps(info, indent=1))
        return 0
    if not info["ffmpeg"] or not info["ffprobe"]:
        _say("FFmpeg was not found. CineVision needs it to read videos.")
        _say("Use the one-click Windows zip (FFmpeg included), or install it:  winget install Gyan.FFmpeg")
        return 2

    logs = home / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s",
                        handlers=[logging.FileHandler(logs / "cinevision.log", encoding="utf-8")])

    _say("")
    _say("CineVision Studio is starting...")
    _say(f"  Your projects are stored in {info['data_dir']}")
    _say(f"  Encryption keys are in {info['keys_dir']} - keep a copy somewhere safe.")
    _say("")

    def announce():
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            if is_cinevision(info["url"], timeout=1):
                _say(f"CineVision is ready:  {info['url']}")
                _say("Keep this window open while you work. Close it (or press Ctrl+C) to stop CineVision.")
                if not args.no_browser:
                    webbrowser.open(info["url"])
                return
            time.sleep(0.5)
        _say(f"CineVision did not start. See {logs / 'cinevision.log'}")

    threading.Thread(target=announce, daemon=True, name="announce").start()
    import uvicorn
    config = uvicorn.Config("cinevision.app:create_app", factory=True, host=info["host"], port=info["port"],
                            access_log=False, proxy_headers=False, server_header=False, log_config=None)
    try:
        uvicorn.Server(config).run()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
