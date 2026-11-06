"""Movie assembly: many clips + an optional scene description → story order, misfits, cut suggestions."""
from __future__ import annotations

from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from .. import audit, db, exports, jobs, retention
from ..assembly import engine as AE
from ..assembly import timeline as TL
from ..director import render as RD
from ..director.i18n import LANGUAGES
from ..keys import keyring
from ..pipeline import stages
from ..web import ApiError, current, not_found, ok, parse
from .exports import _attachment
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


class OrderBody(BaseModel):
    clips: list[str] = Field(max_length=2 * MAX_CLIPS)        # playing order; a clip may appear twice on purpose
    excluded: list[str] = Field(default_factory=list, max_length=MAX_CLIPS)
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
    mine = _stored_order(aid)
    mine = _render_order(aid, mine, lang, res["created_at"] if res else None) if mine else None
    return ok({"id": aid, "title": k.dec(proj["title_enc"], f"projects:title:{aid}"), "mine": mine,
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


def _clip_ids(aid: str) -> list[str]:
    return [r["clip_project_id"] for r in db.all_(
        "SELECT c.clip_project_id FROM assembly_clips c JOIN projects p ON p.id=c.clip_project_id"
        " WHERE c.assembly_id=? AND p.status='active' ORDER BY c.created_at, c.rowid", (aid,))]


def _load_clips(aid: str) -> list[dict]:
    """The analysed clips of an assembly, in the shape the engine reads (same as the assemble job)."""
    from ..concepts import assessment as AS
    k = keyring()
    clips = []
    for pid in _clip_ids(aid):
        an = db.one("SELECT * FROM scene_analyses WHERE project_id=?", (pid,))
        v = db.one("SELECT * FROM uploaded_videos WHERE project_id=?", (pid,))
        if an is None or v is None:
            continue
        analysis = k.dec_json(an["data_enc"], f"scene_analyses:data:{an['id']}")
        meta = dict(db.one("SELECT * FROM video_metadata WHERE video_id=?", (v["id"],)) or {})
        clips.append({"id": pid, "name": k.dec(v["filename_enc"], f"uploaded_videos:filename:{v['id']}"), "analysis": analysis,
                      "meta": meta, "issues": AS.technical_checks(analysis, {})})
    return clips


def _media_facts(aid: str) -> dict[str, dict]:
    """Original file name, frame rate, size, length and audio of every clip — what an editor's timeline needs."""
    k = keyring()
    out = {}
    for pid in _clip_ids(aid):
        v = db.one("SELECT id, filename_enc FROM uploaded_videos WHERE project_id=?", (pid,))
        if v is None:
            continue
        m = db.one("SELECT fps, width, height, duration_s, audio_codec FROM video_metadata WHERE video_id=?", (v["id"],))
        if m is None:
            continue
        out[pid] = {"name": k.dec(v["filename_enc"], f"uploaded_videos:filename:{v['id']}"), "fps": m["fps"], "width": m["width"],
                    "height": m["height"], "duration_s": m["duration_s"], "audio": bool(m["audio_codec"])}
    return out


def _stored_order(aid: str) -> dict | None:
    row = db.one("SELECT * FROM assembly_orders WHERE assembly_id=?", (aid,))
    if row is None:
        return None
    data = keyring().dec_json(row["data_enc"], f"assembly_orders:data:{aid}")
    data["updated_at"] = row["updated_at"]
    return data


def _render_order(aid: str, data: dict, lang: str, result_created: str | None) -> dict:
    current = set(_clip_ids(aid))
    out = RD.render_assembly_order(data, lang)
    out["clips"] = [c for c in data["order"] if c in current]
    out["excluded"] = [c for c in data["excluded"] if c in current]
    out["unplaced"] = [c for c in _clip_ids(aid) if c not in set(out["clips"]) | set(out["excluded"])]
    out["updated_at"] = data.get("updated_at")
    out["stale"] = bool(result_created and data.get("based_on") and result_created > data["based_on"])
    return out


def _check_and_store(ctx, aid: str, order: list[str], excluded: list[str]) -> dict:
    a = db.one("SELECT script_enc FROM assemblies WHERE project_id=?", (aid,))
    k = keyring()
    script = k.dec(a["script_enc"], f"assemblies:script:{aid}") if a and a["script_enc"] else ""
    res = db.one("SELECT created_at FROM assembly_results WHERE assembly_id=?", (aid,))
    data = AE.check_order(_load_clips(aid), script, order, excluded)
    data["based_on"] = res["created_at"] if res else None
    now = db.now_iso()
    with db.tx() as c:
        c.execute("DELETE FROM assembly_orders WHERE assembly_id=?", (aid,))
        c.execute("INSERT INTO assembly_orders(assembly_id,owner_id,data_enc,updated_at) VALUES (?,?,?,?)",
                  (aid, ctx.uid, k.enc_json(data, f"assembly_orders:data:{aid}"), now))
    data["updated_at"] = now
    return data


async def put_order(request: Request):
    """Save the owner's own order (and which clips are left out) and check it for continuity problems."""
    ctx = current(request)
    aid = request.path_params["aid"]
    _owned(ctx, aid)
    body = await parse(request, OrderBody)
    if not db.one("SELECT 1 FROM assembly_results WHERE assembly_id=?", (aid,)):
        raise ApiError(409, "not_ordered", "Order the movie once first; then you can rearrange it.")
    ids = set(_clip_ids(aid))
    if any(c not in ids for c in body.clips) or any(c not in ids for c in body.excluded):
        raise ApiError(400, "unknown_clip", "One of the clips is not part of this movie.")
    if set(body.clips) & set(body.excluded):
        raise ApiError(400, "clip_twice", "A clip can't be both used and not used.")
    lang = body.lang if body.lang in LANGUAGES else stages.user_language(ctx.uid)
    data = await run_in_threadpool(_check_and_store, ctx, aid, body.clips, body.excluded)
    res = db.one("SELECT created_at FROM assembly_results WHERE assembly_id=?", (aid,))
    audit.record("assembly.order", user_id=ctx.uid, target_type="project", target_id=aid, ip=ctx.ip,
                 details={"count": len(body.clips)})
    return ok({"mine": _render_order(aid, data, lang, res["created_at"] if res else None)})


async def delete_order(request: Request):
    """Back to the suggested cut."""
    ctx = current(request)
    aid = request.path_params["aid"]
    _owned(ctx, aid)
    db.run("DELETE FROM assembly_orders WHERE assembly_id=?", (aid,))
    return Response(status_code=204)


async def export_timeline(request: Request):
    """Download the suggested cut or the owner's own order as CMX3600 EDL or FCPXML 1.9 (names and numbers only)."""
    ctx = current(request)
    aid = request.path_params["aid"]
    proj = _owned(ctx, aid)
    fmt = request.query_params.get("format", "edl")
    which = request.query_params.get("order", "suggested")
    if fmt not in ("edl", "fcpxml") or which not in ("suggested", "mine"):
        raise ApiError(400, "bad_request", "format must be edl or fcpxml; order must be suggested or mine.")
    k = keyring()
    if which == "mine":
        data = _stored_order(aid)
        if data is None:
            raise ApiError(409, "no_order", "Save your own order first (Check my order).")
    else:
        res = db.one("SELECT data_enc FROM assembly_results WHERE assembly_id=?", (aid,))
        if res is None:
            raise ApiError(409, "not_ordered", "Order the movie first.")
        data = k.dec_json(res["data_enc"], f"assembly_results:data:{aid}")
    facts = _media_facts(aid)
    entries = [{**facts[s["clip_id"]], "in_s": s["in_s"], "out_s": s["out_s"], "transition": s["transition"],
                "transition_out": s.get("transition_out")} for s in data["sequence"] if s["clip_id"] in facts]
    if not entries:
        raise ApiError(409, "empty_cut", "There are no clips in this cut to export.")
    title = k.dec(proj["title_enc"], f"projects:title:{aid}")
    tl = TL.build(entries, title)
    name = exports.safe_filename(f"{title}-{'my-order' if which == 'mine' else 'suggested-cut'}", fmt)
    if fmt == "edl":
        resp = _attachment(TL.to_edl(tl).encode("utf-8"), "text/plain; charset=utf-8", name)
    else:
        resp = _attachment(TL.to_fcpxml(tl).encode("utf-8"), "application/xml; charset=utf-8", name)
    audit.record("export.timeline", user_id=ctx.uid, target_type="project", target_id=aid, ip=ctx.ip,
                 details={"format": fmt, "kind": which, "count": len(entries)})
    return resp


routes = [
    Route("/api/assemblies", create, methods=["POST"]),
    Route("/api/assemblies/{aid}", get, methods=["GET"]),
    Route("/api/assemblies/{aid}", patch, methods=["PATCH"]),
    Route("/api/assemblies/{aid}", delete, methods=["DELETE"]),
    Route("/api/assemblies/{aid}/clips", add_clip, methods=["POST"]),
    Route("/api/assemblies/{aid}/clips/{cid}", remove_clip, methods=["DELETE"]),
    Route("/api/assemblies/{aid}/assemble", run, methods=["POST"]),
    Route("/api/assemblies/{aid}/order", put_order, methods=["PUT"]),
    Route("/api/assemblies/{aid}/order", delete_order, methods=["DELETE"]),
    Route("/api/assemblies/{aid}/export", export_timeline, methods=["GET"]),
]
