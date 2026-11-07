"""Bring a video in from a link instead of an upload.

Two ways, chosen automatically:
- a direct link to a video file (…/clip.mp4, a Google Drive or Dropbox share link, a camera-card server):
  downloaded with the standard library;
- a page on a video site (YouTube, Vimeo, Instagram, …): handled by the optional `yt-dlp` package when it is
  installed (see requirements-optional.txt). Without it, such links are refused with a clear message.

Safety: only http(s); every host (including each redirect) must resolve to public internet addresses, so a
link can't be used to reach this computer, the LAN or cloud metadata services (unless the owner sets
CINEVISION_URL_IMPORT_ALLOW_PRIVATE=true, e.g. for a NAS on the LAN); size and time are capped; web pages are
never stored. The link itself is user data: callers keep it encrypted and it is never logged.
"""
from __future__ import annotations

import ipaddress
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

VIDEO_EXT = (".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi")
# sites whose links are web pages, not files: these need yt-dlp
PAGE_SITES = ("youtube.com", "youtu.be", "vimeo.com", "instagram.com", "facebook.com", "fb.watch", "x.com",
              "twitter.com", "tiktok.com", "dailymotion.com", "linkedin.com", "reddit.com", "twitch.tv",
              "streamable.com", "bilibili.com", "loom.com")
MAX_REDIRECTS = 5
CHUNK = 1024 * 1024


class FetchError(Exception):
    """A message a film-maker can act on. `retryable` = a network hiccup worth another try."""

    def __init__(self, msg: str, retryable: bool = False):
        super().__init__(msg)
        self.retryable = retryable


def page_links_supported() -> bool:
    try:
        import yt_dlp  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------- link checks
def clean_url(raw: str) -> str:
    url = (raw or "").strip()
    if not re.match(r"^https?://", url, re.I):
        if re.match(r"^[\w.-]+\.[a-z]{2,}(/|$)", url, re.I):
            url = "https://" + url               # people paste "youtu.be/…" without the scheme
        else:
            raise FetchError("Paste a full web link that starts with http:// or https://.")
    p = urllib.parse.urlsplit(url)
    if not p.hostname or p.username or p.password:
        raise FetchError("That link isn't a valid web address (links with a user name or password are not accepted).")
    if len(url) > 2000:
        raise FetchError("That link is too long.")
    return url


def _host_matches(host: str, sites) -> bool:
    host = host.lower().rstrip(".")
    return any(host == s or host.endswith("." + s) for s in sites)


def is_page_site(url: str) -> bool:
    return _host_matches(urllib.parse.urlsplit(url).hostname or "", PAGE_SITES)


def check_public(url: str, allow_private: bool = False) -> None:
    """Refuse links that point at this computer, the local network or other non-public addresses."""
    p = urllib.parse.urlsplit(url)
    if p.scheme.lower() not in ("http", "https"):
        raise FetchError("Only http:// and https:// links can be used.")
    host = p.hostname or ""
    try:
        infos = socket.getaddrinfo(host, p.port or (443 if p.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise FetchError("Couldn't find that website. Check the link, and that this computer is online.", retryable=True)
    if allow_private:
        return
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if getattr(ip, "ipv4_mapped", None):
            ip = ip.ipv4_mapped
        if not ip.is_global or ip.is_multicast:
            raise FetchError("That link points to this computer or a private network, which isn't allowed. "
                             "Use a public link, or download the file and upload it.")


def normalise(url: str) -> str:
    """Turn common share links into direct download links."""
    p = urllib.parse.urlsplit(url)
    host = (p.hostname or "").lower()
    if host in ("drive.google.com", "docs.google.com"):
        m = re.search(r"/file/d/([\w-]{10,})", p.path) or re.search(r"[?&]id=([\w-]{10,})", "?" + p.query)
        if m:
            return f"https://drive.usercontent.google.com/download?id={m.group(1)}&export=download&confirm=t"
    if host.endswith("dropbox.com"):
        q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query) if k != "dl"] + [("dl", "1")]
        return urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, urllib.parse.urlencode(q), ""))
    return url


def name_from(url: str, fallback: str = "video-from-link") -> str:
    """A file name for the project: the last part of the link if it looks like a video file."""
    base = urllib.parse.unquote(Path(urllib.parse.urlsplit(url).path).name)
    base = re.sub(r"[^\w .()\-]+", "_", base).strip(" ._")[:120]
    return base if base.lower().endswith(VIDEO_EXT) else fallback + ".mp4"


# ---------------------------------------------------------------- direct download
class _CheckedRedirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, allow_private: bool):
        super().__init__()
        self.allow_private = allow_private
        self.max_redirections = MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_public(newurl, self.allow_private)          # every hop must be public too
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _filename_from_headers(headers, url: str) -> str:
    cd = headers.get("Content-Disposition") or ""
    m = re.search(r"filename\*=UTF-8''([^;]+)", cd, re.I) or re.search(r'filename="?([^";]+)"?', cd, re.I)
    if m:
        name = re.sub(r"[^\w .()\-]+", "_", urllib.parse.unquote(m.group(1))).strip(" ._")[:120]
        if name.lower().endswith(VIDEO_EXT):
            return name
    return name_from(url)


def download_direct(url: str, dest: Path, limit: int, progress: Callable[[float, str], None],
                    allow_private: bool = False, timeout: int = 30) -> str:
    """Stream a video file to `dest`. Returns a file name. Raises FetchError(…, html=True-ish) for web pages."""
    check_public(url, allow_private)
    opener = urllib.request.build_opener(_CheckedRedirects(allow_private))
    req = urllib.request.Request(url, headers={"User-Agent": "CineVision-Studio/1 (video import)", "Accept": "video/*,*/*;q=0.5"})
    try:
        resp = opener.open(req, timeout=timeout)
    except FetchError:
        raise
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise FetchError("The website refused access to that video. Make sure the link is shared publicly "
                             "(\"anyone with the link\"), or download the file and upload it.")
        if e.code == 404:
            raise FetchError("Nothing was found at that link (404). Check the link.")
        raise FetchError(f"The website answered with an error ({e.code}). Try again later.", retryable=e.code >= 500)
    except (urllib.error.URLError, TimeoutError, OSError):
        raise FetchError("Couldn't connect to that website. Check the link and your internet connection.", retryable=True)
    with resp:
        ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype.startswith("text/") or ctype in ("application/xhtml+xml", "application/json"):
            raise NotAVideoFile()
        size = resp.headers.get("Content-Length")
        total = int(size) if size and size.isdigit() else None
        if total and total > limit:
            raise FetchError(f"That video is larger than the {limit // (1024 * 1024)} MB limit.")
        name = _filename_from_headers(resp.headers, resp.geturl())
        done, last = 0, 0.0
        with open(dest, "wb") as f:
            while True:
                try:
                    chunk = resp.read(CHUNK)
                except (TimeoutError, OSError):
                    raise FetchError("The download stopped part-way. Try again.", retryable=True)
                if not chunk:
                    break
                done += len(chunk)
                if done > limit:
                    raise FetchError(f"That video is larger than the {limit // (1024 * 1024)} MB limit.")
                f.write(chunk)
                now = time.monotonic()
                if now - last > 1:
                    last = now
                    mb = done / (1024 * 1024)
                    progress(min(0.95, done / total) if total else min(0.9, 0.05 + mb / 2000),
                             f"Downloading the video ({mb:.0f} MB{f' of {total / (1024 * 1024):.0f} MB' if total else ''})")
    if done == 0:
        raise FetchError("The link gave an empty file.")
    return name


class NotAVideoFile(FetchError):
    def __init__(self):
        super().__init__("That link opens a web page, not a video file.")


# ---------------------------------------------------------------- video sites (optional yt-dlp)
def download_page(url: str, dest_dir: Path, limit: int, progress: Callable[[float, str], None],
                  ffmpeg_bin: str = "ffmpeg", allow_private: bool = False) -> tuple[Path, str]:
    try:
        import yt_dlp
    except Exception:  # noqa: BLE001
        raise FetchError("Links to video websites (YouTube, Vimeo, Instagram…) need the free yt-dlp add-on, which "
                         "isn't installed. Install it (see the install guide), or download the video and upload it.")
    check_public(url, allow_private)

    def hook(d):
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            got = d.get("downloaded_bytes") or 0
            if got > limit:
                raise yt_dlp.utils.DownloadError("too large")
            progress(min(0.95, got / total) if total else 0.3, f"Downloading the video ({got / (1024 * 1024):.0f} MB)")

    class _Quiet:                       # yt-dlp messages can contain the link and title: keep them out of logs
        def debug(self, msg): pass
        def info(self, msg): pass
        def warning(self, msg): pass
        def error(self, msg): pass

    opts = {
        "outtmpl": str(dest_dir / "download.%(ext)s"),
        "format": "bv*[height<=2160][ext=mp4]+ba[ext=m4a]/bv*[height<=2160]+ba/b[height<=2160]/b",
        "merge_output_format": "mp4",
        "noplaylist": True, "playlist_items": "1",
        "max_filesize": limit,
        "allowed_extractors": ["default", "-generic"],   # known video sites only; no fetching of arbitrary pages
        "ffmpeg_location": ffmpeg_bin,
        "logger": _Quiet(), "quiet": True, "no_warnings": True, "noprogress": True,
        "progress_hooks": [hook], "socket_timeout": 30, "retries": 3,
        "cachedir": False, "writeinfojson": False, "writethumbnail": False, "writesubtitles": False,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except FetchError:
        raise
    except Exception as e:  # noqa: BLE001  (yt-dlp raises many types; its text may echo the link: don't pass it on)
        text = str(e).lower()
        if "too large" in text or "max_filesize" in text or "larger than" in text:
            raise FetchError(f"That video is larger than the {limit // (1024 * 1024)} MB limit.")
        if "private" in text or "sign in" in text or "login" in text or "members" in text:
            raise FetchError("That video is private or needs a sign-in. Make it public or unlisted, or download it "
                             "and upload the file.")
        if "unsupported url" in text or "no suitable" in text:
            raise FetchError("That website isn't supported for links. Download the video and upload the file.")
        raise FetchError("The video site didn't give the video. Try again later, or download it and upload the file.",
                         retryable=True)
    if info and info.get("_type") == "playlist":
        raise FetchError("That link is a playlist. Paste the link of one video.")
    files = [p for p in dest_dir.glob("download.*") if p.suffix.lower() in VIDEO_EXT]
    if not files:
        raise FetchError("The video site didn't give a video file. Download it and upload the file instead.")
    title = (info or {}).get("title") or "video-from-link"
    safe = re.sub(r"[^\w .()\-]+", "_", title).strip(" ._")[:100] or "video-from-link"
    return files[0], safe + files[0].suffix.lower()


# ---------------------------------------------------------------- one entry point
def fetch(raw_url: str, dest_dir: Path, limit: int, progress: Callable[[float, str], None],
          ffmpeg_bin: str = "ffmpeg", allow_private: bool = False) -> tuple[Path, str]:
    """Download the video behind a link into `dest_dir`. Returns (path, file name for the project)."""
    url = normalise(clean_url(raw_url))
    if is_page_site(url):
        return download_page(url, dest_dir, limit, progress, ffmpeg_bin, allow_private)
    dest = dest_dir / "download.bin"
    try:
        name = download_direct(url, dest, limit, progress, allow_private)
    except NotAVideoFile:
        if page_links_supported():   # maybe a site yt-dlp knows that isn't in PAGE_SITES
            dest.unlink(missing_ok=True)
            return download_page(url, dest_dir, limit, progress, ffmpeg_bin, allow_private)
        raise FetchError("That link opens a web page, not a video file. Use a direct link to the file, a Google Drive "
                         "or Dropbox share link, or download the video and upload it.")
    return dest, name
