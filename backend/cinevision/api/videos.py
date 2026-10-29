from __future__ import annotations

import os
import re
from pathlib import Path

from pydantic import BaseModel, Field
from python_multipart import MultipartParser
from python_multipart.multipart import parse_options_header
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route

from .. import audit, db, jobs, security, storage
from ..config import settings
from ..keys import keyring
from ..media import probe, scan
from ..web import ApiError, current, not_found, ok, owned_project, parse

MEDIA_TOKEN_TTL = 300


class _UploadSink:
    """Streams exactly one multipart file field named 'file' to a private temp file with a hard byte cap."""

    def __init__(self, dest: Path, limit: int):
        self.dest, self.limit = dest, limit
        self.fh = None
        self.size = 0
        self.filename: str | None = None
        self.files_seen = 0
        self._hdr_field = b""
        self._hdr_value = b""
        self._headers: dict[bytes, bytes] = {}
        self._in_file = False

    def callbacks(self):
        return {
            "on_part_begin": self._part_begin,
            "on_header_field": lambda d, s, e: setattr(self, "_hdr_field", self._hdr_field + d[s:e]),
            "on_header_value": lambda d, s, e: setattr(self, "_hdr_value", self._hdr_value + d[s:e]),
            "on_header_end": self._header_end,
            "on_headers_finished": self._headers_done,
            "on_part_data": self._data,
            "on_part_end": self._part_end,
        }

    def _part_begin(self):
        self._headers, self._in_file = {}, False

    def _header_end(self):
        self._headers[self._hdr_field.lower()] = self._hdr_value
        self._hdr_field = self._hdr_value = b""

    def _headers_done(self):
        _, opts = parse_options_header(self._headers.get(b"content-disposition", b""))
        if opts.get(b"name") == b"file" and b"filename" in opts:
            self.files_seen += 1
            if self.files_seen > 1:
                raise ApiError(422, "one_file_only", "Upload one clip at a time.")
            self.filename = opts[b"filename"].decode("utf-8", "replace")[:255]
            fd = os.open(self.dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            self.fh = os.fdopen(fd, "wb")
            self._in_file = True

    def _data(self, d, s, e):
        if not self._in_file:
            return
        self.size += e - s
        if self.size > self.limit:
            raise ApiError(413, "too_large", f"Clip exceeds the {self.limit // (1024 * 1024)} MB limit.")
        self.fh.write(d[s:e])

    def _part_end(self):
        if self._in_file and self.fh:
            self.fh.close()
            self.fh = None
        self._in_file = False

    def close(self):
        if self.fh:
            self.fh.close()


def _store_upload(ctx, pid: str, tmp_path: Path, filename: str) -> dict:
    s = settings()
    meta = probe.validate(tmp_path, filename, s)
    try:
        scan_status = scan.scan_file(tmp_path, s.clamd_socket)
    except Exception:
        raise ApiError(503, "scanner_unavailable", "The malware scanner is configured but unavailable. Try again later.")
    if scan_status == "infected":
        audit.record("video.upload", user_id=ctx.uid, outcome="denied", target_type="project", target_id=pid,
                     ip=ctx.ip, details={"reason": "malware_detected"})
        raise ApiError(422, "malware_detected", "The file was rejected by the malware scanner.")
    ext = Path(filename).suffix.lower()
    ctype = {".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
             ".mkv": "video/x-matroska", ".avi": "video/x-msvideo"}[ext]
    blob_id = storage.put_file(tmp_path, owner_id=ctx.uid, project_id=pid, kind="original", content_type=ctype)
    vid = db.new_id()
    k = keyring()
    with db.tx() as c:
        c.execute("INSERT INTO uploaded_videos(id,owner_id,project_id,filename_enc,original_blob_id,status,scan_status,created_at)"
                  " VALUES (?,?,?,?,?,?,?,?)",
                  (vid, ctx.uid, pid, k.enc(filename, f"uploaded_videos:filename:{vid}"), blob_id, "validated",
                   scan_status, db.now_iso()))
        c.execute("INSERT INTO video_metadata(video_id,owner_id,container,video_codec,audio_codec,width,height,display_aspect,"
                  "fps,duration_s,bitrate,pix_fmt,rotation,frame_count) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (vid, ctx.uid, meta["container"], meta["video_codec"], meta["audio_codec"], meta["width"], meta["height"],
                   meta["display_aspect"], meta["fps"], meta["duration_s"], meta["bitrate"], meta["pix_fmt"],
                   meta["rotation"], meta["frame_count"]))
        c.execute("UPDATE projects SET updated_at=? WHERE id=?", (db.now_iso(), pid))
    job = jobs.enqueue(owner_id=ctx.uid, project_id=pid, kind="ingest", idem_key=f"ingest:{vid}", params={"video_id": vid})
    audit.record("video.upload", user_id=ctx.uid, target_type="project", target_id=pid, ip=ctx.ip,
                 details={"bytes": tmp_path.stat().st_size})
    return {"video_id": vid, "metadata": meta, "scan_status": scan_status, "job": jobs.public(job)}


async def upload_video(request: Request):
    ctx = current(request)
    pid = request.path_params["pid"]
    owned_project(ctx, pid)
    wait = security.limiter.hit(f"upload:{ctx.uid}", 20, 3600)
    if wait:
        raise ApiError(429, "rate_limited", "Too many uploads. Try again later.", {"Retry-After": str(wait)})
    if db.one("SELECT 1 FROM uploaded_videos WHERE project_id=?", (pid,)):
        raise ApiError(409, "video_exists", "This project already has a clip. Remove it first to replace it.")
    s = settings()
    clen = request.headers.get("content-length")
    if clen and clen.isdigit() and int(clen) > s.max_upload_bytes + 64 * 1024:
        raise ApiError(413, "too_large", f"Clip exceeds the {s.max_upload_bytes // (1024 * 1024)} MB limit.")
    ctype, opts = parse_options_header(request.headers.get("content-type", ""))
    if ctype != b"multipart/form-data" or b"boundary" not in opts:
        raise ApiError(415, "unsupported_media_type", "Send the clip as multipart/form-data.")

    with storage.private_tempdir("upload-") as tmpdir:
        sink = _UploadSink(tmpdir / "upload.bin", s.max_upload_bytes)
        parser = MultipartParser(opts[b"boundary"], sink.callbacks(), max_size=s.max_upload_bytes + 64 * 1024)
        try:
            async for chunk in request.stream():
                parser.write(chunk)
            parser.finalize()
        except ApiError:
            raise
        except Exception:
            raise ApiError(400, "bad_upload", "The upload was malformed or interrupted.")
        finally:
            sink.close()
        if not sink.filename or sink.size == 0:
            raise ApiError(422, "no_file", "No clip was included in the upload.")
        try:
            result = await run_in_threadpool(_store_upload, ctx, pid, tmpdir / "upload.bin", sink.filename)
        except probe.ValidationError as e:
            audit.record("video.upload", user_id=ctx.uid, outcome="failure", target_type="project", target_id=pid,
                         ip=ctx.ip, details={"reason": e.code})
            raise ApiError(422, e.code, str(e))
    return ok(result, 201)


async def delete_video(request: Request):
    """Remove the clip and everything derived from it (frames, analysis, concepts, jobs)."""
    ctx = current(request)
    pid = request.path_params["pid"]
    owned_project(ctx, pid)
    v = db.one("SELECT * FROM uploaded_videos WHERE project_id=? AND owner_id=?", (pid, ctx.uid))
    if not v:
        raise not_found()
    db.run("UPDATE render_jobs SET cancel_requested=1 WHERE project_id=? AND status IN ('queued','processing')", (pid,))
    blob_ids = [r["id"] for r in db.all_("SELECT id FROM blobs WHERE project_id=? AND kind IN ('original','proxy','frame')", (pid,))]
    with db.tx() as c:
        c.execute("DELETE FROM render_jobs WHERE project_id=?", (pid,))
        c.execute("DELETE FROM cinematography_concepts WHERE project_id=?", (pid,))
        c.execute("DELETE FROM extracted_frames WHERE project_id=?", (pid,))
        c.execute("DELETE FROM uploaded_videos WHERE id=?", (v["id"],))
        c.executemany("DELETE FROM blobs WHERE id=?", [(b,) for b in blob_ids])
    for b in blob_ids:
        storage.destroy_blob_file(b)
    audit.record("video.delete", user_id=ctx.uid, target_type="project", target_id=pid, ip=ctx.ip,
                 details={"count": len(blob_ids)})
    return Response(status_code=204)


class TokenReq(BaseModel):
    blob_id: str = Field(min_length=36, max_length=36)


def _token_for(ctx, blob_id: str) -> str:
    return keyring().media_token(blob_id, ctx.uid, ctx.session["token_hash"], MEDIA_TOKEN_TTL)


async def media_token(request: Request):
    ctx = current(request)
    pid = request.path_params["pid"]
    owned_project(ctx, pid)
    body = await parse(request, TokenReq)
    b = db.owned_one("blobs", body.blob_id, ctx.uid)
    if b is None or b["project_id"] != pid or b["kind"] not in ("original", "proxy", "frame"):
        raise not_found()
    audit.record("media.token", user_id=ctx.uid, target_type="blob", target_id=b["id"], ip=ctx.ip, details={"kind": b["kind"]})
    return ok({"url": f"/api/media/{b['id']}?t={_token_for(ctx, b['id'])}", "expires_in": MEDIA_TOKEN_TTL})


_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


async def media(request: Request):
    ctx = current(request)
    blob_id = request.path_params["bid"]
    b = db.owned_one("blobs", blob_id, ctx.uid)
    tok = request.query_params.get("t", "")
    if b is None or not keyring().verify_media_token(tok, blob_id, ctx.uid, ctx.session["token_hash"]):
        raise not_found()
    proj = db.one("SELECT status FROM projects WHERE id=?", (b["project_id"],))
    if not proj or proj["status"] != "active":
        raise not_found()
    rdr = storage.reader(b)
    size = b["plaintext_size"]
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "private, no-store", "Content-Type": b["content_type"],
               "Content-Disposition": "inline", "X-Content-Type-Options": "nosniff"}
    rng = request.headers.get("range")
    if rng:
        m = _RANGE.match(rng.strip())
        if not m or (not m.group(1) and not m.group(2)):
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        if m.group(1):
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else size - 1
        else:
            start, end = max(0, size - int(m.group(2))), size - 1
        end = min(end, size - 1)
        if start > end or start >= size:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        headers.update({"Content-Range": f"bytes {start}-{end}/{size}", "Content-Length": str(end - start + 1)})
        return StreamingResponse(rdr.iter_range(start, end), status_code=206, headers=headers)
    headers["Content-Length"] = str(size)
    return StreamingResponse(rdr.iter_range(0, size - 1), status_code=200, headers=headers)


async def frames(request: Request):
    ctx = current(request)
    pid = request.path_params["pid"]
    owned_project(ctx, pid)
    rows = db.all_("SELECT id, blob_id, idx, t_s, width, height, is_keyframe, shot_index FROM extracted_frames"
                   " WHERE project_id=? AND owner_id=? ORDER BY idx", (pid, ctx.uid))
    out = []
    for r in rows:
        d = dict(r)
        d["url"] = f"/api/media/{r['blob_id']}?t={_token_for(ctx, r['blob_id'])}"
        out.append(d)
    return ok({"frames": out, "expires_in": MEDIA_TOKEN_TTL})


routes = [
    Route("/api/projects/{pid}/video", upload_video, methods=["POST"]),
    Route("/api/projects/{pid}/video", delete_video, methods=["DELETE"]),
    Route("/api/projects/{pid}/media-token", media_token, methods=["POST"]),
    Route("/api/projects/{pid}/frames", frames, methods=["GET"]),
    Route("/api/media/{bid}", media, methods=["GET"]),
]
