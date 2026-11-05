"""Movie assembly: many clips + an optional scene description → story order, misfits, cut suggestions."""
from __future__ import annotations

from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from .. import db, jobs, retention
from ..director import render as RD
from ..director.i18n import LANGUAGES
from ..keys import keyring
from ..pipeline import stages
from ..web import ApiError, current, not_found, ok, parse
from .projects import CreativeControls
from .videos import MEDIA_TOKEN_TTL, _token_for

MAX_CLIPS = 300


class AssemblyCreate(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    script: str | None = Field(default=None, max_length=60000)


class AssemblyPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    script: str | None = Field(default=None, max_length=60000)


class ClipCreate(BaseModel):
    filename: str = Field(min_length=1, max_length=255)


class AssembleBody(BaseModel):
    lang: str | None = Field(default=None, max_length=8)


def _owned(ctx, aid: str) -> dict:
    row = db.owned_one("projects", aid, ctx.uid)
    if row is None or row["status"] != "active" or not db.one("SELECT 1 FROM assemblies WHERE project_id=?", (aid,)):
        raise not_found()
    return dict(row)


async def create(request: Request):
    ctx = current(request)
    body = await parse(request, AssemblyCreate)
    k = keyring()
    aid, now = db.new_id(), db.now_iso()
    with db.tx() as c:
        c.execute("INSERT INTO projects(id,owner_id,title_enc,brief_enc,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                  (aid, ctx.uid, k.enc(body.title.strip(), f"projects:title:{aid}"),
                   k.enc_json(CreativeControls().model_dump(), f"projects:brief:{aid}"), now, now))
        c.execute("INSERT INTO assemblies(project_id,owner_id,script_enc,created_at) VALUES (?,?,?,?)",
                  (aid, ctx.uid, k.enc(body.script, f"assemblies:script:{aid}") if body.script else None, now))
    return ok({"id": aid}, 201)


async def get(request: Request):
    ctx = current(request)
    aid = request.path_params["aid"]
    proj = _owned(ctx, aid)
    k = keyring()
    a = db.one("SELECT * FROM assemblies WHERE project_id=?", (aid,))
    lang = request.query_params.get("lang")
    lang = lang if lang in LANGUAGES else stages.user_language(ctx.uid)
    clips = []
    for r in db.all_("SELECT c.clip_project_id AS pid, c.created_at FROM assembly_clips c JOIN projects p ON p.id=c.clip_project_id"
                     " WHERE c.assembly_id=? AND p.status='active' ORDER BY c.created_at, c.rowid", (aid,)):
        v = db.one("SELECT * FROM uploaded_videos WHERE project_id=?", (r["pid"],))
        meta = db.one("SELECT duration_s, width, height, fps FROM video_metadata WHERE video_id=?", (v["id"],)) if v else None
        an = db.one("SELECT id FROM scene_analyses WHERE project_id=?", (r["pid"],))
        job = db.one("SELECT status, error, stage, progress FROM render_jobs WHERE project_id=? ORDER BY created_at DESC LIMIT 1", (r["pid"],))
        fr = db.one("SELECT blob_id FROM extracted_frames WHERE project_id=? ORDER BY is_keyframe DESC, idx LIMIT 1", (r["pid"],))
        clips.append({"clip_id": r["pid"],
                      "name": k.dec(v["filename_enc"], f"uploaded_videos:filename:{v['id']}") if v else None,
                      "status": "analysed" if an else (v["status"] if v else "waiting_for_upload"),
                      "job": dict(job) if job else None, "meta": dict(meta) if meta else None,
                      "thumb": f"/api/media/{fr['blob_id']}?t={_token_for(ctx, fr['blob_id'])}" if fr else None})
    res = db.one("SELECT * FROM assembly_results WHERE assembly_id=?", (aid,))
    result = ai = None
    if res:
        result = RD.render_assembly(k.dec_json(res["data_enc"], f"assembly_results:data:{aid}"), lang)
        result["created_at"] = res["created_at"]
        ai_all = k.dec_json(res["ai_enc"], f"assembly_results:ai:{aid}") if res["ai_enc"] else {}
        ai = ai_all.get(lang)
    jobs_ = [jobs.public(dict(j)) for j in db.all_("SELECT * FROM render_jobs WHERE project_id=? ORDER BY created_at DESC LIMIT 5", (aid,))]
    from ..adapters import localai
    return ok({"id": aid, "title": k.dec(proj["title_enc"], f"projects:title:{aid}"),
               "script": k.dec(a["script_enc"], f"assemblies:script:{aid}") if a["script_enc"] else "",
               "clips": clips, "result": result, "ai": ai, "ai_available": localai.available(ctx.uid),
               "jobs": jobs_, "expires_in": MEDIA_TOKEN_TTL, "max_clips": MAX_CLIPS})


async def patch(request: Request):
    ctx = current(request)
    aid = request.path_params["aid"]
    _owned(ctx, aid)
    body = await parse(request, AssemblyPatch)
    k = keyring()
    if body.title is not None:
        db.run("UPDATE projects SET title_enc=?, updated_at=? WHERE id=?", (k.enc(body.title.strip(), f"projects:title:{aid}"), db.now_iso(), aid))
    if body.script is not None:
        db.run("UPDATE assemblies SET script_enc=? WHERE project_id=?",
               (k.enc(body.script, f"assemblies:script:{aid}") if body.script else None, aid))
    return ok({"id": aid})


async def add_clip(request: Request):
    ctx = current(request)
    aid = request.path_params["aid"]
    _owned(ctx, aid)
    body = await parse(request, ClipCreate)
    n = db.one("SELECT COUNT(*) n FROM assembly_clips WHERE assembly_id=?", (aid,))["n"]
    if n >= MAX_CLIPS:
        raise ApiError(409, "too_many_clips", f"An assembly can hold up to {MAX_CLIPS} clips.")
    k = keyring()
    pid, now = db.new_id(), db.now_iso()
    with db.tx() as c:
        c.execute("INSERT INTO projects(id,owner_id,title_enc,brief_enc,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                  (pid, ctx.uid, k.enc(body.filename, f"projects:title:{pid}"),
                   k.enc_json(CreativeControls().model_dump(), f"projects:brief:{pid}"), now, now))
        c.execute("INSERT INTO assembly_clips(clip_project_id,assembly_id,owner_id,created_at) VALUES (?,?,?,?)",
                  (pid, aid, ctx.uid, now))
    return ok({"clip_id": pid, "upload_url": f"/api/projects/{pid}/video"}, 201)


async def remove_clip(request: Request):
    ctx = current(request)
    aid, pid = request.path_params["aid"], request.path_params["cid"]
    _owned(ctx, aid)
    if not db.one("SELECT 1 FROM assembly_clips WHERE clip_project_id=? AND assembly_id=?", (pid, aid)):
        raise not_found()
    retention.delete_project(pid, ctx.uid, reason="assembly_clip_removed", ip=ctx.ip)
    return Response(status_code=204)


async def run(request: Request):
    ctx = current(request)
    aid = request.path_params["aid"]
    _owned(ctx, aid)
    body = await parse(request, AssembleBody)
    if not db.one("SELECT 1 FROM assembly_clips c JOIN scene_analyses a ON a.project_id=c.clip_project_id WHERE c.assembly_id=?", (aid,)):
        raise ApiError(409, "nothing_analysed", "Upload clips and wait until at least one is analysed.")
    lang = body.lang if body.lang in LANGUAGES else stages.user_language(ctx.uid)
    job = jobs.enqueue(owner_id=ctx.uid, project_id=aid, kind="assemble", idem_key=f"assemble:{aid}:{db.new_id()}",
                       params={"lang": lang})
    return ok({"job": jobs.public(job)}, 202)


async def delete(request: Request):
    ctx = current(request)
    aid = request.path_params["aid"]
    _owned(ctx, aid)
    for r in db.all_("SELECT clip_project_id FROM assembly_clips WHERE assembly_id=?", (aid,)):
        retention.delete_project(r["clip_project_id"], ctx.uid, reason="assembly_deleted", ip=ctx.ip)
    retention.delete_project(aid, ctx.uid, ip=ctx.ip)
    return Response(status_code=204)


routes = [
    Route("/api/assemblies", create, methods=["POST"]),
    Route("/api/assemblies/{aid}", get, methods=["GET"]),
    Route("/api/assemblies/{aid}", patch, methods=["PATCH"]),
    Route("/api/assemblies/{aid}", delete, methods=["DELETE"]),
    Route("/api/assemblies/{aid}/clips", add_clip, methods=["POST"]),
    Route("/api/assemblies/{aid}/clips/{cid}", remove_clip, methods=["DELETE"]),
    Route("/api/assemblies/{aid}/assemble", run, methods=["POST"]),
]
