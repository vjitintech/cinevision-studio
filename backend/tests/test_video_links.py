"""Bring a video in by pasting a link: safety checks, direct files, share links, video sites (stubbed yt-dlp)."""
import sys
import threading
import types
import uuid
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from cinevision.media import fetch as F
from conftest import PASSWORD, Client, start_server, stop_server, wait_jobs


# ---------------------------------------------------------------- link checks (unit)
def test_clean_url_and_share_links():
    assert F.clean_url("youtu.be/abc123") == "https://youtu.be/abc123"
    for bad in ("ftp://x.test/a.mp4", "file:///etc/passwd", "javascript:alert(1)", "not a link", "https://u:p@x.test/a.mp4"):
        with pytest.raises(F.FetchError):
            F.check_public(F.normalise(F.clean_url(bad)))
    assert F.normalise("https://drive.google.com/file/d/1AbCdEfGhIjKlMnOp/view?usp=sharing") == \
        "https://drive.usercontent.google.com/download?id=1AbCdEfGhIjKlMnOp&export=download&confirm=t"
    assert F.normalise("https://www.dropbox.com/scl/fi/xyz/shot.mp4?rlkey=k&dl=0").endswith("rlkey=k&dl=1")
    assert F.is_page_site("https://www.youtube.com/watch?v=x") and F.is_page_site("https://youtu.be/x")
    assert not F.is_page_site("https://notyoutube.com.evil.test/x.mp4")
    assert F.name_from("https://cdn.test/films/My%20Shot.mov?sig=1") == "My Shot.mov"
    assert F.name_from("https://cdn.test/watch?id=3") == "video-from-link.mp4"


@pytest.mark.parametrize("url", ["http://127.0.0.1:8000/a.mp4", "http://localhost/a.mp4", "http://10.0.0.5/a.mp4",
                                 "http://192.168.1.10/a.mp4", "http://169.254.169.254/latest/meta-data/",
                                 "http://[::1]/a.mp4", "http://0.0.0.0/a.mp4"])
def test_private_addresses_are_refused(url):
    with pytest.raises(F.FetchError, match="private network"):
        F.check_public(url)
    F.check_public(url, allow_private=True)          # the owner can allow the LAN explicitly


def test_redirect_to_private_address_is_refused():
    h = F._CheckedRedirects(allow_private=False)
    with pytest.raises(F.FetchError, match="private network"):
        h.redirect_request(None, None, 302, "Found", {}, "http://127.0.0.1/secret")


def test_video_site_without_ytdlp_says_how_to_enable(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "yt_dlp", None)
    monkeypatch.setattr(F, "check_public", lambda *a, **k: None)
    with pytest.raises(F.FetchError, match="yt-dlp"):
        F.fetch("https://www.youtube.com/watch?v=abc", tmp_path, 10 ** 8, lambda *a: None)
    assert F.page_links_supported() is False


def test_video_site_with_stubbed_ytdlp(monkeypatch, tmp_path):
    seen = {}

    class YDL:
        def __init__(self, opts):
            seen.update(opts)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download):
            seen["url"] = url
            for hook in seen["progress_hooks"]:
                hook({"status": "downloading", "downloaded_bytes": 10, "total_bytes": 20})
            (tmp_path / "download.mp4").write_bytes(b"x" * 20)
            return {"title": "Rain / Night: take 2"}

    stub = types.SimpleNamespace(YoutubeDL=YDL, utils=types.SimpleNamespace(DownloadError=Exception))
    monkeypatch.setitem(sys.modules, "yt_dlp", stub)
    monkeypatch.setattr(F, "check_public", lambda *a, **k: None)
    progress = []
    path, name = F.fetch("https://vimeo.com/123", tmp_path, 10 ** 8, lambda p, s: progress.append(p))
    assert path.name == "download.mp4" and name == "Rain _ Night_ take 2.mp4"
    assert seen["allowed_extractors"] == ["default", "-generic"] and seen["noplaylist"] is True
    assert seen["max_filesize"] == 10 ** 8 and progress


# ---------------------------------------------------------------- through the app
@pytest.fixture(scope="module")
def files(tmp_path_factory, clips):
    d = tmp_path_factory.mktemp("site")
    (d / "scene.mp4").write_bytes(Path(clips["pan"]).read_bytes())
    (d / "page.html").write_text("<html><body>not a video</body></html>")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(d)))
    srv.RequestHandlerClass.log_message = lambda *a: None
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture(scope="module")
def lan_server(tmp_path_factory):
    """A server whose owner allowed links to the local network (needed: the test file server is on 127.0.0.1)."""
    proc, info = start_server(tmp_path_factory.mktemp("lan"), {"CINEVISION_URL_IMPORT_ALLOW_PRIVATE": "true"})
    yield info
    stop_server(proc)


def _account(info):
    c = Client(info["port"])
    assert c.post("/api/auth/register", {"email": f"links{uuid.uuid4().hex[:8]}@example.test", "password": PASSWORD}).status == 201
    return c


def test_default_server_refuses_local_links(user, files):
    pid = user.post("/api/projects", {"title": "link"}).json["id"]
    r = user.post(f"/api/projects/{pid}/video/link", {"url": f"{files}/scene.mp4"})
    assert r.status == 422 and "private network" in r.json["error"]["message"]
    assert user.get(f"/api/projects/{pid}/jobs").json["jobs"] == []


def test_link_to_a_video_file_is_downloaded_and_analysed(lan_server, files):
    c = _account(lan_server)
    caps = c.get("/api/system/capabilities").json
    assert caps["video_links"]["files"] is True
    pid = c.post("/api/projects", {"title": "from a link"}).json["id"]
    r = c.post(f"/api/projects/{pid}/video/link", {"url": f"{files}/scene.mp4"})
    assert r.status == 202 and r.json["job"]["kind"] == "fetch"
    jobs = wait_jobs(c, pid, timeout=240)
    kinds = {j["kind"]: j["status"] for j in jobs}
    assert kinds.get("fetch") == "completed" and kinds.get("ingest") == "completed", jobs
    proj = c.get(f"/api/projects/{pid}").json
    assert proj["video"]["filename"] == "scene.mp4"
    assert c.get(f"/api/projects/{pid}/analysis").status == 200
    # the project now has a clip: another link is refused, like a second upload
    assert c.post(f"/api/projects/{pid}/video/link", {"url": f"{files}/scene.mp4"}).status == 409


def test_link_to_a_web_page_fails_with_a_clear_message(lan_server, files):
    c = _account(lan_server)
    pid = c.post("/api/projects", {"title": "page"}).json["id"]
    assert c.post(f"/api/projects/{pid}/video/link", {"url": f"{files}/page.html"}).status == 202
    jobs = wait_jobs(c, pid)
    fetch = [j for j in jobs if j["kind"] == "fetch"][0]
    assert fetch["status"] == "failed" and "web page, not a video file" in fetch["error"]
    assert c.post(f"/api/projects/{pid}/video/link", {"url": f"{files}/missing.mp4"}).status == 202
    jobs = wait_jobs(c, pid)
    assert any(j["status"] == "failed" and "404" in (j["error"] or "") for j in jobs if j["kind"] == "fetch")


def test_link_is_not_written_to_logs(lan_server, files):
    log = Path(lan_server["data"]) / "server.log" if "data" in lan_server else None
    if log and log.exists():
        assert "scene.mp4" not in log.read_text(encoding="utf-8", errors="replace")
