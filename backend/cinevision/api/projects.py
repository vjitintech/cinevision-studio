from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from .. import audit, db, jobs, retention
from ..keys import keyring
from ..web import ApiError, current, not_found, ok, owned_project, parse


class CreativeControls(BaseModel):
    """Director's creative controls. All optional; the engine uses neutral defaults."""
    genre: str | None = Field(default=None, max_length=60)
    scene_purpose: str | None = Field(default=None, max_length=200)
    desired_emotion: str | None = Field(default=None, max_length=80)
    visual_style: str | None = Field(default=None, max_length=120)
    treatment: Literal["realistic", "balanced", "stylized"] = "balanced"
    movement_intensity: int = Field(default=2, ge=0, le=4)
    lighting_complexity: int = Field(default=2, ge=0, le=4)
    budget: Literal["micro", "low", "medium", "high"] = "low"
    available_equipment: list[str] = Field(default_factory=list, max_length=60)
    aspect_ratio: str = Field(default="16:9", pattern=r"^(16:9|2\.39:1|4:3|1\.85:1|9:16|1:1|\d{1,2}(\.\d{1,2})?:\d{1,2}(\.\d{1,2})?)$")
    frame_rate: float = Field(default=24, ge=1, le=240)
    target_duration_s: float | None = Field(default=None, ge=1, le=600)
    concept_count: int = Field(default=4, ge=1, le=6)
    preservation: Literal["low", "medium", "high"] = "medium"
    intent: Literal["practical_shoot", "animation", "exploration"] = "practical_shoot"
    sensor: Literal["super35", "full_frame", "micro_four_thirds", "phone"] = "super35"
    brief: str | None = Field(default=None, max_length=4000)


class ProjectCreate(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    brief: CreativeControls | None = None


class ProjectPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    brief: CreativeControls | None = None


def _project_public(row: dict) -> dict:
    k = keyring()
    return {
        "id": row["id"],
        "title": k.dec(row["title_enc"], f"projects:title:{row['id']}"),
        "brief": k.dec_json(row["brief_enc"], f"projects:brief:{row['id']}") or CreativeControls().model_dump(),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def project_detail(ctx, pid: str) -> dict:
    row = owned_project(ctx, pid)
    out = _project_public(row)
    k = keyring()
    v = db.one("SELECT * FROM uploaded_videos WHERE project_id=? AND owner_id=?", (pid, ctx.uid))
    if v:
        meta = db.one("SELECT * FROM video_metadata WHERE video_id=?", (v["id"],))
        out["video"] = {
            "id": v["id"], "status": v["status"], "scan_status": v["scan_status"],
            "filename": k.dec(v["filename_enc"], f"uploaded_videos:filename:{v['id']}"),
            "original_blob_id": v["original_blob_id"], "proxy_blob_id": v["proxy_blob_id"],
            "metadata": {kk: meta[kk] for kk in meta.keys() if kk not in ("video_id", "owner_id")} if meta else None,
        }
    else:
        out["video"] = None
    out["jobs"] = [jobs.public(dict(j)) for j in db.all_(
        "SELECT * FROM render_jobs WHERE project_id=? AND owner_id=? ORDER BY created_at DESC LIMIT 20", (pid, ctx.uid))]
    a = db.one("SELECT id, created_at FROM scene_analyses WHERE project_id=? AND owner_id=?", (pid, ctx.uid))
    out["analysis_id"] = a["id"] if a else None
    out["concept_count"] = db.one("SELECT COUNT(*) n FROM cinematography_concepts WHERE project_id=? AND owner_id=? AND is_current=1",
                                  (pid, ctx.uid))["n"]
    return out


async def list_projects(request: Request):
    ctx = current(request)
    rows = db.all_("SELECT * FROM projects WHERE owner_id=? AND status='active' ORDER BY updated_at DESC", (ctx.uid,))
    out = []
    for r in rows:
        p = _project_public(dict(r))
        v = db.one("SELECT status FROM uploaded_videos WHERE project_id=?", (r["id"],))
        p["video_status"] = v["status"] if v else None
        p["concept_count"] = db.one("SELECT COUNT(*) n FROM cinematography_concepts WHERE project_id=? AND is_current=1",
                                    (r["id"],))["n"]
        out.append(p)
    return ok({"projects": out})


async def create_project(request: Request):
    ctx = current(request)
    body = await parse(request, ProjectCreate)
    if db.one("SELECT COUNT(*) n FROM projects WHERE owner_id=?", (ctx.uid,))["n"] >= 500:
        raise ApiError(429, "too_many_projects", "Project limit reached.")
    pid = db.new_id()
    k = keyring()
    now = db.now_iso()
    brief = (body.brief or CreativeControls()).model_dump()
    db.run("INSERT INTO projects(id,owner_id,title_enc,brief_enc,created_at,updated_at) VALUES (?,?,?,?,?,?)",
           (pid, ctx.uid, k.enc(body.title.strip(), f"projects:title:{pid}"), k.enc_json(brief, f"projects:brief:{pid}"), now, now))
    audit.record("project.create", user_id=ctx.uid, target_type="project", target_id=pid, ip=ctx.ip)
    return ok(project_detail(ctx, pid), 201)


async def get_project(request: Request):
    ctx = current(request)
    return ok(project_detail(ctx, request.path_params["pid"]))


async def patch_project(request: Request):
    ctx = current(request)
    pid = request.path_params["pid"]
    owned_project(ctx, pid)
    body = await parse(request, ProjectPatch)
    k = keyring()
    if body.title is not None:
        db.run("UPDATE projects SET title_enc=? WHERE id=? AND owner_id=?",
               (k.enc(body.title.strip(), f"projects:title:{pid}"), pid, ctx.uid))
    if body.brief is not None:
        db.run("UPDATE projects SET brief_enc=? WHERE id=? AND owner_id=?",
               (k.enc_json(body.brief.model_dump(), f"projects:brief:{pid}"), pid, ctx.uid))
    db.run("UPDATE projects SET updated_at=? WHERE id=?", (db.now_iso(), pid))
    return ok(project_detail(ctx, pid))


async def delete_project(request: Request):
    ctx = current(request)
    if not retention.delete_project(request.path_params["pid"], ctx.uid, ip=ctx.ip):
        raise not_found()
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


routes = [
    Route("/api/projects", list_projects, methods=["GET"]),
    Route("/api/projects", create_project, methods=["POST"]),
    Route("/api/projects/{pid}", get_project, methods=["GET"]),
    Route("/api/projects/{pid}", patch_project, methods=["PATCH"]),
    Route("/api/projects/{pid}", delete_project, methods=["DELETE"]),
]
