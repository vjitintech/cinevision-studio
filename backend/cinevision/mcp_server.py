"""CineVision MCP server: lets an AI assistant (any Model Context Protocol client) use CineVision as a tool.

Standard library only, stdio transport (one JSON-RPC 2.0 message per line), like colibri's `coli mcp`.
It talks to a running CineVision over its normal HTTP API with your account, so every privacy rule still applies:

* video files are read from paths you give and uploaded to *your* CineVision (default http://127.0.0.1:8000);
* the assistant receives text results only (review, analysis summary, order) — never frames or footage;
* nothing else on the disk is read.

Run:   python -m cinevision.mcp_server          (from the backend folder)   or   python backend/cinevision/mcp_server.py
Env:   CINEVISION_URL (default http://127.0.0.1:8000), CINEVISION_EMAIL, CINEVISION_PASSWORD,
       CINEVISION_TOTP_SECRET (only if two-factor is on for that account)

Claude Desktop / other clients:
  {"mcpServers": {"cinevision": {"command": "python", "args": ["C:/path/to/cinevision-studio/backend/cinevision/mcp_server.py"],
                                 "env": {"CINEVISION_EMAIL": "you@example.com", "CINEVISION_PASSWORD": "..."}}}}
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import http.cookiejar
import json
import os
import struct
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

PROTOCOL = "2025-06-18"
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}
LANGS = ["en", "hi", "te", "ta", "kn", "ml", "bn", "mr", "es", "fr", "de", "pt", "ar", "ja", "ko", "zh"]


class ToolError(Exception):
    pass


def _totp(secret: str, t: float | None = None) -> str:
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    msg = struct.pack(">Q", int((t or time.time()) // 30))
    h = hmac.new(key, msg, hashlib.sha1).digest()
    o = h[-1] & 0x0F
    return str((struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % 1_000_000).zfill(6)


class CineVision:
    def __init__(self):
        self.base = os.environ.get("CINEVISION_URL", "http://127.0.0.1:8000").rstrip("/")
        self.origin = os.environ.get("CINEVISION_ORIGIN", self.base)     # must match CineVision's public origin
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.csrf: str | None = None
        self.signed_in = False

    def _req(self, method: str, path: str, body=None, raw: bytes | None = None, ctype: str | None = None, timeout=600):
        headers = {"Origin": self.origin}
        data = raw
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if ctype:
            headers["Content-Type"] = ctype
        if method != "GET" and self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with self.opener.open(req, timeout=timeout) as r:
                txt = r.read()
                out = json.loads(txt) if txt and "json" in (r.headers.get("Content-Type") or "") else {}
        except urllib.error.HTTPError as e:
            try:
                err = json.loads(e.read()).get("error", {})
            except Exception:  # noqa: BLE001
                err = {}
            raise ToolError(err.get("message") or f"CineVision answered {e.code}.") from None
        except urllib.error.URLError:
            raise ToolError(f"Can't reach CineVision at {self.base}. Is it running?") from None
        if isinstance(out, dict) and out.get("csrf_token"):
            self.csrf = out["csrf_token"]
        return out

    def login(self):
        if self.signed_in:
            return
        email, pw = os.environ.get("CINEVISION_EMAIL"), os.environ.get("CINEVISION_PASSWORD")
        if not email or not pw:
            raise ToolError("Set CINEVISION_EMAIL and CINEVISION_PASSWORD in the MCP server's environment.")
        self._req("GET", "/api/auth/session")
        r = self._req("POST", "/api/auth/login", {"email": email, "password": pw})
        if r.get("mfa_required"):
            secret = os.environ.get("CINEVISION_TOTP_SECRET")
            if not secret:
                raise ToolError("This account uses two-factor sign-in: set CINEVISION_TOTP_SECRET for the MCP server.")
            self._req("POST", "/api/auth/mfa/verify", {"code": _totp(secret)})
        self._req("GET", "/api/auth/session")
        self.signed_in = True

    def call(self, method, path, body=None, **kw):
        self.login()
        return self._req(method, path, body, **kw)

    def upload(self, pid: str, path: Path):
        b = uuid.uuid4().hex
        head = (f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n").encode()
        raw = head + path.read_bytes() + f"\r\n--{b}--\r\n".encode()
        return self.call("POST", f"/api/projects/{pid}/video", raw=raw, ctype=f"multipart/form-data; boundary={b}", timeout=3600)

    def wait(self, pid: str, timeout: float = 3600):
        end = time.time() + timeout
        while time.time() < end:
            jobs = self.call("GET", f"/api/projects/{pid}/jobs")["jobs"]
            if jobs and all(j["status"] in ("completed", "failed", "cancelled") for j in jobs):
                failed = [j for j in jobs if j["status"] == "failed"]
                if failed:
                    raise ToolError(f"{failed[0]['kind']} failed: {failed[0].get('error')}")
                return jobs
            time.sleep(2)
        raise ToolError("Still processing — ask again later with the project id.")


def _video(path: str) -> Path:
    p = Path(path).expanduser()
    if not p.is_file():
        raise ToolError(f"No file at {p}")
    if p.suffix.lower() not in VIDEO_EXT:
        raise ToolError("Only video files (MP4, MOV, M4V, MKV, WEBM, AVI) can be sent.")
    return p


def _review_text(r: dict) -> dict:
    """Compact review for an assistant: verdict, each department's findings and the edit options."""
    out = {"verdict": r.get("verdict_label"), "summary": r.get("summary"), "language": r.get("language"), "departments": []}
    for d in r.get("departments", []):
        out["departments"].append({"department": d["title"], "status": d["status_label"],
                                   "findings": [{k: f.get(k) for k in ("observation", "suggestion", "why", "basis")} for f in d["findings"]]})
    out["ways_to_edit"] = [{"name": o["name"], "idea": o["idea"], "steps": o["steps"]} for o in r.get("edit_options", [])]
    out["alternatives"] = r.get("alternatives", [])
    if r.get("ai") and r["ai"].get("status") == "used":
        out["ai_director_notes"] = {k: r["ai"].get(k) for k in ("overall", "story", "alternative_ways", "edit_ideas")}
    return out


TOOLS = [
    {"name": "review_video", "description": "Upload a video file from this computer to CineVision and return the Director's review: "
     "is the shot good, department-by-department findings (camera, framing, lighting, colour, sound, editing, titles) with what to do "
     "and why, ways to edit it, and alternative cinematography concepts. Footage stays in CineVision; only text comes back.",
     "inputSchema": {"type": "object", "required": ["path"], "properties": {
         "path": {"type": "string", "description": "Absolute path of the video file"},
         "title": {"type": "string", "description": "Project name (default: file name)"},
         "language": {"type": "string", "enum": LANGS, "description": "Language of the review (default: account setting)"},
         "brief": {"type": "string", "description": "Optional: what the scene should make the audience feel"}}}},
    {"name": "get_review", "description": "The Director's review of an existing project, in a chosen language.",
     "inputSchema": {"type": "object", "required": ["project_id"], "properties": {
         "project_id": {"type": "string"}, "language": {"type": "string", "enum": LANGS}}}},
    {"name": "list_projects", "description": "List the account's projects and movie assemblies.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "list_concepts", "description": "The alternative cinematography concepts of a project (camera, lens, lighting, blocking per shot).",
     "inputSchema": {"type": "object", "required": ["project_id"], "properties": {"project_id": {"type": "string"}}}},
    {"name": "refine_concept", "description": "Change a concept in plain language, e.g. 'make the lighting more dramatic', 'shot 2 tighter'. Creates a new revision.",
     "inputSchema": {"type": "object", "required": ["concept_id", "request"], "properties": {
         "concept_id": {"type": "string"}, "request": {"type": "string"}}}},
    {"name": "assemble_movie", "description": "Upload many clips plus an optional story/screenplay; returns the suggested story order with reasons, "
     "trims and transitions, and the clips that don't fit (and why).",
     "inputSchema": {"type": "object", "required": ["paths"], "properties": {
         "paths": {"type": "array", "items": {"type": "string"}, "description": "Absolute paths of the clips"},
         "title": {"type": "string"}, "story": {"type": "string", "description": "Scene description or screenplay"},
         "language": {"type": "string", "enum": LANGS}}}},
]


def run_tool(cv: CineVision, name: str, a: dict):
    lang = a.get("language")
    q = f"?lang={lang}" if lang in LANGS else ""
    if name == "list_projects":
        return [{"id": p["id"], "title": p["title"], "kind": p.get("kind"), "clips": p.get("clip_count")}
                for p in cv.call("GET", "/api/projects")["projects"]]
    if name == "review_video":
        path = _video(a["path"])
        if lang in LANGS:
            cv.call("PUT", "/api/me/preferences", {"language": lang})
        brief = {"brief": a["brief"]} if a.get("brief") else None
        pid = cv.call("POST", "/api/projects", {"title": a.get("title") or path.stem, **({"brief": brief} if brief else {})})["id"]
        cv.upload(pid, path)
        cv.wait(pid)
        return {"project_id": pid, **_review_text(cv.call("GET", f"/api/projects/{pid}/review{q}"))}
    if name == "get_review":
        r = cv.call("GET", f"/api/projects/{a['project_id']}/review{q}")
        if not r.get("ready"):
            return {"ready": False, "message": "The review is still being prepared."}
        return {"project_id": a["project_id"], **_review_text(r)}
    if name == "list_concepts":
        cs = cv.call("GET", f"/api/projects/{a['project_id']}/concepts")["concepts"]
        return [{"concept_id": c["id"], "name": c["data"]["name"], "objective": c["data"]["objective"],
                 "emotion": c["data"]["intended_emotion"],
                 "shots": [{"size": s["size"], "angle": s["angle"], "lens_mm": s["lens"]["focal_mm"], "movement": s["movement"]["type"],
                            "lighting": s["lighting"]["setup"], "blocking": s["blocking"]} for s in c["data"]["shots"]]} for c in cs]
    if name == "refine_concept":
        r = cv.call("POST", f"/api/concepts/{a['concept_id']}/refine", {"command": a["request"]})
        return {"new_concept_id": r.get("concept", {}).get("id"), "applied": r.get("applied"), "not_understood": r.get("unrecognized")}
    if name == "assemble_movie":
        paths = [_video(p) for p in a["paths"]]
        if not paths:
            raise ToolError("Give at least one clip.")
        if lang in LANGS:
            cv.call("PUT", "/api/me/preferences", {"language": lang})
        aid = cv.call("POST", "/api/assemblies", {"title": a.get("title") or "Movie", "script": a.get("story")})["id"]
        clip_ids = []
        for p in paths:
            cid = cv.call("POST", f"/api/assemblies/{aid}/clips", {"filename": p.name})["clip_id"]
            cv.upload(cid, p)
            clip_ids.append(cid)
        for cid in clip_ids:
            cv.wait(cid)
        for _ in range(60):
            res = cv.call("GET", f"/api/assemblies/{aid}{q}")
            if res.get("result") and not any(j["status"] in ("queued", "processing") for j in res["jobs"]):
                break
            time.sleep(2)
        r = res.get("result") or {}
        return {"assembly_id": aid, "runtime_s": r.get("runtime_s"),
                "order": [{"position": s["position"], "clip": s["name"], "use_from_s": s["in_s"], "use_to_s": s["out_s"],
                           "transition_in": s["transition_label"], "why": [x["text"] for x in s["reasons"]],
                           "warnings": [x["text"] for x in s["flags"]]} for s in r.get("sequence", [])],
                "not_used": [{"clip": x["name"], "why": [f["text"] for f in x["flags"]]} for x in r.get("rejected", [])],
                "notes": [n["text"] for n in r.get("notes", [])],
                "ai_editor": res.get("ai") if res.get("ai") and res["ai"].get("status") == "used" else None}
    raise ToolError(f"Unknown tool {name}")


def handle(cv: CineVision, msg: dict) -> dict | None:
    mid, method = msg.get("id"), msg.get("method")
    if mid is None:                       # notification (e.g. notifications/initialized)
        return None
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": msg.get("params", {}).get("protocolVersion") or PROTOCOL,
            "capabilities": {"tools": {}}, "serverInfo": {"name": "cinevision", "version": "1.0"},
            "instructions": "CineVision reviews videos like a film director and orders clips like an editor. "
                            "Give absolute file paths. Results are text; footage never leaves CineVision."}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        p = msg.get("params", {})
        try:
            out = run_tool(cv, p.get("name", ""), p.get("arguments") or {})
            return {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False, indent=1)}]}}
        except ToolError as e:
            return {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": str(e)}], "isError": True}}
        except Exception as e:  # noqa: BLE001
            return {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": f"Unexpected error: {type(e).__name__}"}], "isError": True}}
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Unknown method {method}"}}


def main():
    cv = CineVision()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            print(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}), flush=True)
            continue
        out = handle(cv, msg)
        if out is not None:
            print(json.dumps(out, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
