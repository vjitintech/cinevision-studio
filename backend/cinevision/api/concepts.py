"""Analysis and cinematography concept endpoints."""
from __future__ import annotations

from starlette.requests import Request
from starlette.routing import Route

from .. import db, jobs
from ..keys import keyring
from ..web import ApiError, current, ok, owned_project


def load_analysis(ctx, pid: str) -> dict | None:
    row = db.one("SELECT * FROM scene_analyses WHERE project_id=? AND owner_id=?", (pid, ctx.uid))
    if row is None:
        return None
    data = keyring().dec_json(row["data_enc"], f"scene_analyses:data:{row['id']}")
    return {"id": row["id"], "created_at": row["created_at"], "data": data}


async def get_analysis(request: Request):
    ctx = current(request)
    pid = request.path_params["pid"]
    owned_project(ctx, pid)
    a = load_analysis(ctx, pid)
    if a is None:
        raise ApiError(404, "analysis_not_ready", "No analysis yet. It appears here once processing completes.")
    return ok(a)


async def rerun_analysis(request: Request):
    ctx = current(request)
    pid = request.path_params["pid"]
    owned_project(ctx, pid)
    v = db.one("SELECT * FROM uploaded_videos WHERE project_id=? AND owner_id=?", (pid, ctx.uid))
    if not v or v["status"] != "ready":
        raise ApiError(409, "video_not_ready", "Upload a clip and wait for preparation to finish first.")
    job = jobs.enqueue(owner_id=ctx.uid, project_id=pid, kind="analyze", idem_key=f"analyze:{v['id']}",
                       params={"video_id": v["id"]})
    return ok({"job": jobs.public(job)}, 202)


routes = [
    Route("/api/projects/{pid}/analysis", get_analysis, methods=["GET"]),
    Route("/api/projects/{pid}/analysis/rerun", rerun_analysis, methods=["POST"]),
]
