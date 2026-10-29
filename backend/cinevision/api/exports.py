"""Explicit, user-initiated downloads. Nothing here sends data anywhere except back to the requesting browser."""
from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

from .. import audit, db, exports
from ..keys import keyring
from ..web import current, not_found, owned_project, parse


class ExportBody(BaseModel):
    format: Literal["csv", "json", "pdf"]


def _attachment(data: bytes, ctype: str, filename: str) -> Response:
    return Response(data, media_type=ctype, headers={
        "Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff"})


async def export_shotlist(request: Request):
    ctx = current(request)
    row = db.owned_one("shot_lists", request.path_params["sid"], ctx.uid)
    if row is None:
        raise not_found()
    proj = owned_project(ctx, row["project_id"])
    body = await parse(request, ExportBody)
    k = keyring()
    data = k.dec_json(row["data_enc"], f"shot_lists:data:{row['id']}")
    title = k.dec(proj["title_enc"], f"projects:title:{proj['id']}")
    name = exports.safe_filename(f"{title}-{data['concept_name']}-shot-list", body.format)
    if body.format == "csv":
        resp = _attachment(exports.shotlist_csv(data), "text/csv; charset=utf-8", name)
    elif body.format == "json":
        resp = _attachment(exports.shotlist_json(data, {"project": title}), "application/json", name)
    else:
        pdf = await run_in_threadpool(exports.shotlist_pdf, data, title)
        resp = _attachment(pdf, "application/pdf", name)
    audit.record("export.shotlist", user_id=ctx.uid, target_type="shot_list", target_id=row["id"], ip=ctx.ip,
                 details={"format": body.format})
    return resp


async def export_project(request: Request):
    """Structured project file: brief, analysis, concepts (current revisions), plans, shot lists. No media."""
    ctx = current(request)
    pid = request.path_params["pid"]
    proj = owned_project(ctx, pid)
    k = keyring()
    a = db.one("SELECT * FROM scene_analyses WHERE project_id=? AND owner_id=?", (pid, ctx.uid))
    concepts = []
    for r in db.all_("SELECT * FROM cinematography_concepts WHERE project_id=? AND owner_id=? AND is_current=1", (pid, ctx.uid)):
        item = {"id": r["id"], "revision": r["revision"], "selected": bool(r["is_selected"]), "engine": json.loads(r["engine"]),
                "concept": k.dec_json(r["data_enc"], f"cinematography_concepts:data:{r['id']}")}
        for table, key in (("camera_configurations", "camera_plans"), ("lighting_configurations", "lighting_plans")):
            item[key] = {str(c["shot_index"]): k.dec_json(c["data_enc"], f"{table}:data:{c['id']}")
                         for c in db.all_(f"SELECT * FROM {table} WHERE concept_id=?", (r["id"],))}
        sc = db.one("SELECT * FROM three_d_scenes WHERE concept_id=?", (r["id"],))
        item["scene3d"] = k.dec_json(sc["data_enc"], f"three_d_scenes:data:{sc['id']}") if sc else None
        concepts.append(item)
    out = {
        "format": "cinevision.project/1",
        "exported_at": db.now_iso(),
        "title": k.dec(proj["title_enc"], f"projects:title:{pid}"),
        "brief": k.dec_json(proj["brief_enc"], f"projects:brief:{pid}"),
        "analysis": k.dec_json(a["data_enc"], f"scene_analyses:data:{a['id']}") if a else None,
        "concepts": concepts,
        "shot_lists": [k.dec_json(s["data_enc"], f"shot_lists:data:{s['id']}") for s in
                       db.all_("SELECT * FROM shot_lists WHERE project_id=? AND owner_id=? ORDER BY created_at", (pid, ctx.uid))],
        "media_included": False,
    }
    audit.record("export.project", user_id=ctx.uid, target_type="project", target_id=pid, ip=ctx.ip, details={"format": "json"})
    return _attachment(json.dumps(out, indent=2, ensure_ascii=False).encode(), "application/json",
                       exports.safe_filename(out["title"] + ".cinevision", "json"))


routes = [
    Route("/api/shotlists/{sid}/export", export_shotlist, methods=["POST"]),
    Route("/api/projects/{pid}/export", export_project, methods=["POST"]),
]
