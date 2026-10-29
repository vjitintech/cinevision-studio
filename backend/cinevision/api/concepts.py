"""Analysis, concepts, planners (camera / lighting / storyboard / 3D) and shot lists."""
from __future__ import annotations

import hashlib
import json

from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.routing import Route

from .. import audit, db, jobs
from ..adapters import llm, registry
from ..concepts import refine as R
from ..concepts import shotlist as SL
from ..keys import keyring
from ..web import ApiError, current, not_found, ok, owned_project, parse
from .projects import CreativeControls

MAX_STATE_BYTES = 128 * 1024


# ---------------------------------------------------------------- analysis
def load_analysis(ctx, pid: str) -> dict | None:
    row = db.one("SELECT * FROM scene_analyses WHERE project_id=? AND owner_id=?", (pid, ctx.uid))
    if row is None:
        return None
    data = keyring().dec_json(row["data_enc"], f"scene_analyses:data:{row['id']}")
    return {"id": row["id"], "created_at": row["created_at"], "engine": json.loads(row["engine"]), "data": data}


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


# ---------------------------------------------------------------- concepts
def _concept_row(ctx, cid: str) -> dict:
    row = db.owned_one("cinematography_concepts", cid, ctx.uid)
    if row is None:
        raise not_found()
    owned_project(ctx, row["project_id"])
    return dict(row)


def _concept_out(row: dict) -> dict:
    data = keyring().dec_json(row["data_enc"], f"cinematography_concepts:data:{row['id']}")
    note = keyring().dec(row["change_note_enc"], f"cinematography_concepts:note:{row['id']}") if row["change_note_enc"] else None
    return {"id": row["id"], "lineage_id": row["lineage_id"], "parent_id": row["parent_id"], "revision": row["revision"],
            "is_current": bool(row["is_current"]), "is_selected": bool(row["is_selected"]), "batch_id": row["batch_id"],
            "created_at": row["created_at"], "engine": json.loads(row["engine"]), "change_note": note, "data": data}


class GenerateBody(BaseModel):
    controls: CreativeControls | None = None


async def generate(request: Request):
    ctx = current(request)
    pid = request.path_params["pid"]
    proj = owned_project(ctx, pid)
    body = await parse(request, GenerateBody)
    a = db.one("SELECT id FROM scene_analyses WHERE project_id=? AND owner_id=?", (pid, ctx.uid))
    if a is None:
        raise ApiError(409, "analysis_not_ready", "Concepts need a finished scene analysis first.")
    k = keyring()
    if body.controls is not None:
        controls = body.controls.model_dump()
        db.run("UPDATE projects SET brief_enc=?, updated_at=? WHERE id=?",
               (k.enc_json(controls, f"projects:brief:{pid}"), db.now_iso(), pid))
    else:
        controls = k.dec_json(proj["brief_enc"], f"projects:brief:{pid}") or CreativeControls().model_dump()
    h = hashlib.sha256(json.dumps({"c": controls, "a": a["id"]}, sort_keys=True).encode()).hexdigest()[:24]
    job = jobs.enqueue(owner_id=ctx.uid, project_id=pid, kind="concepts", idem_key=f"concepts:{pid}:{h}",
                       params={"controls": controls, "controls_hash": h})
    return ok({"job": jobs.public(job)}, 202)


async def list_concepts(request: Request):
    ctx = current(request)
    pid = request.path_params["pid"]
    owned_project(ctx, pid)
    rows = [dict(r) for r in db.all_("SELECT * FROM cinematography_concepts WHERE project_id=? AND owner_id=? AND is_current=1"
                                     " ORDER BY created_at DESC", (pid, ctx.uid))]
    if request.query_params.get("all") != "1" and rows:
        latest_batch = max(rows, key=lambda r: r["created_at"])["batch_id"]
        rows = [r for r in rows if r["batch_id"] == latest_batch or r["is_selected"]]
    rows.sort(key=lambda r: (json.loads(r["engine"]).get("score") is None, -(json.loads(r["engine"]).get("score") or 0)))
    return ok({"concepts": [_concept_out(r) for r in rows]})


async def get_concept(request: Request):
    ctx = current(request)
    return ok(_concept_out(_concept_row(ctx, request.path_params["cid"])))


async def history(request: Request):
    ctx = current(request)
    row = _concept_row(ctx, request.path_params["cid"])
    rows = db.all_("SELECT * FROM cinematography_concepts WHERE lineage_id=? AND owner_id=? ORDER BY revision",
                   (row["lineage_id"], ctx.uid))
    return ok({"revisions": [_concept_out(dict(r)) for r in rows]})


class RefineBody(BaseModel):
    command: str | None = Field(default=None, max_length=1000)
    patches: list[dict] | None = Field(default=None, max_length=40)


async def refine_concept(request: Request):
    ctx = current(request)
    row = _concept_row(ctx, request.path_params["cid"])
    if not row["is_current"]:
        raise ApiError(409, "not_current", "Refine the latest revision of this concept.")
    body = await parse(request, RefineBody)
    if not body.command and not body.patches:
        raise ApiError(422, "empty_refinement", "Describe a change or send patches.")
    k = keyring()
    concept = k.dec_json(row["data_enc"], f"cinematography_concepts:data:{row['id']}")
    a = load_analysis(ctx, row["project_id"])
    analysis = a["data"] if a else None
    applied, unknown, used_model = [], [], None
    new = concept
    try:
        if body.patches:
            new, applied = R.apply_patch(new, body.patches, analysis)
        if body.command:
            new, ap, unknown = R.apply_command(new, body.command, analysis)
            applied += ap
    except R.PatchError as e:
        raise ApiError(422, "invalid_patch", str(e))
    if unknown and llm.pick(ctx.uid):
        op_map = {fn.__name__: fn for _, fn in R.RULES}
        try:
            ops, used_model = llm.interpret(ctx.uid, "; ".join(unknown), list(op_map), len(new["shots"]))
        except Exception:
            ops = []
        if ops:
            import copy
            new = copy.deepcopy(new)
            for o in ops:
                applied.append(op_map[o["op"]](new, o["shot"], o["n"]) + f" (interpreted by {used_model})")
            R.refresh(new, analysis)
            unknown = []
    if not applied:
        return ok({"concept": _concept_out(row), "applied": [], "unrecognized": unknown,
                   "help": "Try: 'make the lighting more dramatic', 'move the camera lower', 'slower dolly-in', "
                           "'more background separation', 'keep the blocking but change the angle', "
                           "'three alternative endings', 'small budget', 'shot 2 tighter', 'warmer', 'add haze'."})
    nid = db.new_id()
    with db.tx() as c:
        c.execute("UPDATE cinematography_concepts SET is_current=0 WHERE lineage_id=? AND owner_id=?", (row["lineage_id"], ctx.uid))
        c.execute("INSERT INTO cinematography_concepts(id,owner_id,project_id,analysis_id,batch_id,lineage_id,parent_id,revision,"
                  "is_current,is_selected,data_enc,engine,change_note_enc,created_at) VALUES (?,?,?,?,?,?,?,?,1,?,?,?,?,?)",
                  (nid, ctx.uid, row["project_id"], row["analysis_id"], row["batch_id"], row["lineage_id"], row["id"],
                   row["revision"] + 1, row["is_selected"], k.enc_json(new, f"cinematography_concepts:data:{nid}"),
                   json.dumps({**json.loads(row["engine"]), "refined_with": used_model or "rule-parser"}),
                   k.enc(" ".join(applied)[:2000], f"cinematography_concepts:note:{nid}"), db.now_iso()))
        # carry planner state forward to the new revision
        for table in ("camera_configurations", "lighting_configurations"):
            for r in c.execute(f"SELECT * FROM {table} WHERE concept_id=?", (row["id"],)).fetchall():
                new_id = db.new_id()
                payload = k.dec_json(r["data_enc"], f"{table}:data:{r['id']}")
                c.execute(f"INSERT INTO {table}(id,owner_id,project_id,concept_id,shot_index,data_enc,updated_at) VALUES (?,?,?,?,?,?,?)",
                          (new_id, ctx.uid, row["project_id"], nid, r["shot_index"], k.enc_json(payload, f"{table}:data:{new_id}"),
                           db.now_iso()))
    out = _concept_out(dict(db.one("SELECT * FROM cinematography_concepts WHERE id=?", (nid,))))
    return ok({"concept": out, "applied": applied, "unrecognized": unknown}, 201)


async def revert(request: Request):
    """Make an older revision current again (as a new revision, preserving history)."""
    ctx = current(request)
    row = _concept_row(ctx, request.path_params["cid"])
    head = db.one("SELECT * FROM cinematography_concepts WHERE lineage_id=? AND owner_id=? AND is_current=1",
                  (row["lineage_id"], ctx.uid))
    if head["id"] == row["id"]:
        return ok({"concept": _concept_out(row)})
    k = keyring()
    data = k.dec_json(row["data_enc"], f"cinematography_concepts:data:{row['id']}")
    nid = db.new_id()
    with db.tx() as c:
        c.execute("UPDATE cinematography_concepts SET is_current=0 WHERE lineage_id=?", (row["lineage_id"],))
        c.execute("INSERT INTO cinematography_concepts(id,owner_id,project_id,analysis_id,batch_id,lineage_id,parent_id,revision,"
                  "is_current,is_selected,data_enc,engine,change_note_enc,created_at) VALUES (?,?,?,?,?,?,?,?,1,?,?,?,?,?)",
                  (nid, ctx.uid, row["project_id"], row["analysis_id"], row["batch_id"], row["lineage_id"], head["id"],
                   head["revision"] + 1, head["is_selected"], k.enc_json(data, f"cinematography_concepts:data:{nid}"),
                   row["engine"], k.enc(f"Reverted to revision {row['revision']}", f"cinematography_concepts:note:{nid}"),
                   db.now_iso()))
    return ok({"concept": _concept_out(dict(db.one("SELECT * FROM cinematography_concepts WHERE id=?", (nid,))))}, 201)


async def select(request: Request):
    ctx = current(request)
    row = _concept_row(ctx, request.path_params["cid"])
    with db.tx() as c:
        c.execute("UPDATE cinematography_concepts SET is_selected=0 WHERE project_id=? AND owner_id=?", (row["project_id"], ctx.uid))
        c.execute("UPDATE cinematography_concepts SET is_selected=1 WHERE lineage_id=? AND owner_id=?", (row["lineage_id"], ctx.uid))
    return ok()


# ---------------------------------------------------------------- planner state (camera, lighting, storyboard, 3D)
def _shot(concept: dict, idx: int) -> dict:
    for s in concept["shots"]:
        if s["index"] == idx:
            return s
    raise not_found()


def _default_camera(concept, s):
    return {"size": s["size"], "angle": s["angle"], "composition": s["composition"], "camera_height_m": s["camera_height_m"],
            "azimuth_deg": s["azimuth_deg"], "distance_m": s["distance_m"], "dutch_roll_deg": s.get("dutch_roll_deg", 0),
            "lens_ff_mm": s["lens"]["ff_equiv_mm"], "movement": s["movement"]["type"], "speed_m_s": s["movement"]["speed_m_s"],
            "focus": s["dof"]["focus"], "camera_path": s["camera_path"], "derived": True}


def _default_lighting(concept, s):
    return {**s["lighting"], "derived": True}


def _validate_state(data) -> dict:
    if not isinstance(data, dict):
        raise ApiError(422, "invalid_state", "Expected an object.")
    if len(json.dumps(data)) > MAX_STATE_BYTES:
        raise ApiError(413, "too_large", "State too large.")
    data.pop("derived", None)
    return data


def _planner(table: str, default_fn):
    async def handler(request: Request):
        ctx = current(request)
        row = _concept_row(ctx, request.path_params["cid"])
        try:
            idx = int(request.path_params["shot"])
        except ValueError:
            raise not_found()
        k = keyring()
        concept = k.dec_json(row["data_enc"], f"cinematography_concepts:data:{row['id']}")
        s = _shot(concept, idx)
        existing = db.one(f"SELECT * FROM {table} WHERE concept_id=? AND shot_index=? AND owner_id=?", (row["id"], idx, ctx.uid))
        if request.method == "GET":
            if existing:
                return ok({**k.dec_json(existing["data_enc"], f"{table}:data:{existing['id']}"), "derived": False})
            return ok(default_fn(concept, s))
        if request.method == "DELETE":
            db.run(f"DELETE FROM {table} WHERE concept_id=? AND shot_index=? AND owner_id=?", (row["id"], idx, ctx.uid))
            return ok(default_fn(concept, s))
        body = await request.body()
        try:
            data = _validate_state(json.loads(body or b"null"))
        except json.JSONDecodeError:
            raise ApiError(422, "bad_json", "Invalid JSON.")
        rid = existing["id"] if existing else db.new_id()
        enc = k.enc_json(data, f"{table}:data:{rid}")
        if existing:
            db.run(f"UPDATE {table} SET data_enc=?, updated_at=? WHERE id=?", (enc, db.now_iso(), rid))
        else:
            db.run(f"INSERT INTO {table}(id,owner_id,project_id,concept_id,shot_index,data_enc,updated_at) VALUES (?,?,?,?,?,?,?)",
                   (rid, ctx.uid, row["project_id"], row["id"], idx, enc, db.now_iso()))
        return ok({**data, "derived": False})
    return handler


def _default_storyboard(concept: dict) -> dict:
    panels = []
    for s in concept["shots"]:
        panels.append({"shot": s["index"], "duration_s": s["duration_s"], "title": f"{s['size'].title()} · {s['angle']}",
                       "note": f"{s['purpose'].capitalize()}. {s['movement']['type'].capitalize()}.",
                       "transition": s["transition_out"], "transition_s": 0.6 if "dissolve" in s["transition_out"] else 0.0})
    return {"panels": panels, "derived": True}


async def storyboard(request: Request):
    ctx = current(request)
    row = _concept_row(ctx, request.path_params["cid"])
    k = keyring()
    concept = k.dec_json(row["data_enc"], f"cinematography_concepts:data:{row['id']}")
    sb = db.one("SELECT * FROM storyboards WHERE concept_id=? AND owner_id=?", (row["id"], ctx.uid))
    if request.method == "GET":
        if sb:
            data = k.dec_json(sb["data_enc"], f"storyboards:data:{sb['id']}")
            an = db.one("SELECT * FROM animatics WHERE storyboard_id=?", (sb["id"],))
            anim = k.dec_json(an["data_enc"], f"animatics:data:{an['id']}") if an else {}
            return ok({**data, "animatic": anim, "derived": False})
        return ok({**_default_storyboard(concept), "animatic": {"playback_rate": 1.0, "show_paths": True}})
    data = await request.body()
    try:
        data = _validate_state(json.loads(data or b"null"))
    except json.JSONDecodeError:
        raise ApiError(422, "bad_json", "Invalid JSON.")
    anim = data.pop("animatic", {}) or {}
    if not isinstance(data.get("panels"), list):
        raise ApiError(422, "invalid_state", "panels must be a list.")
    with db.tx() as c:
        sid = sb["id"] if sb else db.new_id()
        if sb:
            c.execute("UPDATE storyboards SET data_enc=?, updated_at=? WHERE id=?", (k.enc_json(data, f"storyboards:data:{sid}"), db.now_iso(), sid))
        else:
            c.execute("INSERT INTO storyboards(id,owner_id,project_id,concept_id,data_enc,updated_at) VALUES (?,?,?,?,?,?)",
                      (sid, ctx.uid, row["project_id"], row["id"], k.enc_json(data, f"storyboards:data:{sid}"), db.now_iso()))
        an = c.execute("SELECT id FROM animatics WHERE storyboard_id=?", (sid,)).fetchone()
        aid = an["id"] if an else db.new_id()
        if an:
            c.execute("UPDATE animatics SET data_enc=?, updated_at=? WHERE id=?", (k.enc_json(anim, f"animatics:data:{aid}"), db.now_iso(), aid))
        else:
            c.execute("INSERT INTO animatics(id,owner_id,project_id,storyboard_id,data_enc,updated_at) VALUES (?,?,?,?,?,?)",
                      (aid, ctx.uid, row["project_id"], sid, k.enc_json(anim, f"animatics:data:{aid}"), db.now_iso()))
    return ok({**data, "animatic": anim, "derived": False})


async def scene3d(request: Request):
    ctx = current(request)
    row = _concept_row(ctx, request.path_params["cid"])
    k = keyring()
    concept = k.dec_json(row["data_enc"], f"cinematography_concepts:data:{row['id']}")
    sc = db.one("SELECT * FROM three_d_scenes WHERE concept_id=? AND owner_id=?", (row["id"], ctx.uid))
    if request.method == "GET":
        if sc:
            return ok({**k.dec_json(sc["data_enc"], f"three_d_scenes:data:{sc['id']}"), "derived": False})
        return ok({"scene": concept["scene"], "overrides": {}, "derived": True})
    try:
        data = _validate_state(json.loads(await request.body() or b"null"))
    except json.JSONDecodeError:
        raise ApiError(422, "bad_json", "Invalid JSON.")
    sid = sc["id"] if sc else db.new_id()
    enc = k.enc_json(data, f"three_d_scenes:data:{sid}")
    if sc:
        db.run("UPDATE three_d_scenes SET data_enc=?, updated_at=? WHERE id=?", (enc, db.now_iso(), sid))
    else:
        db.run("INSERT INTO three_d_scenes(id,owner_id,project_id,concept_id,data_enc,updated_at) VALUES (?,?,?,?,?,?)",
               (sid, ctx.uid, row["project_id"], row["id"], enc, db.now_iso()))
    return ok({**data, "derived": False})


# ---------------------------------------------------------------- shot lists
async def create_shotlist(request: Request):
    ctx = current(request)
    row = _concept_row(ctx, request.path_params["cid"])
    k = keyring()
    concept = k.dec_json(row["data_enc"], f"cinematography_concepts:data:{row['id']}")
    overrides = {}
    for r in db.all_("SELECT * FROM lighting_configurations WHERE concept_id=? AND owner_id=?", (row["id"], ctx.uid)):
        d = k.dec_json(r["data_enc"], f"lighting_configurations:data:{r['id']}")
        if isinstance(d.get("sources"), list) and d.get("setup"):
            overrides[r["shot_index"]] = d
    data = SL.build(concept, overrides)
    data["concept_revision"] = row["revision"]
    sid = db.new_id()
    db.run("INSERT INTO shot_lists(id,owner_id,project_id,concept_id,data_enc,created_at) VALUES (?,?,?,?,?,?)",
           (sid, ctx.uid, row["project_id"], row["id"], k.enc_json(data, f"shot_lists:data:{sid}"), db.now_iso()))
    return ok({"id": sid, "created_at": db.now_iso(), "data": data}, 201)


async def list_shotlists(request: Request):
    ctx = current(request)
    row = _concept_row(ctx, request.path_params["cid"])
    k = keyring()
    rows = db.all_("SELECT s.* FROM shot_lists s JOIN cinematography_concepts c ON c.id=s.concept_id"
                   " WHERE c.lineage_id=? AND s.owner_id=? ORDER BY s.created_at DESC", (row["lineage_id"], ctx.uid))
    return ok({"shotlists": [{"id": r["id"], "created_at": r["created_at"],
                              "data": k.dec_json(r["data_enc"], f"shot_lists:data:{r['id']}")} for r in rows]})


# ---------------------------------------------------------------- Mode D (AI preview) gate
async def ai_preview(request: Request):
    ctx = current(request)
    _concept_row(ctx, request.path_params["cid"])
    statuses = [registry.adapter_status(a, ctx.uid) for a in ("image-gen", "video-gen")]
    if all(s["status"] == "not_configured" for s in statuses):
        audit.record("ai_preview.request", user_id=ctx.uid, outcome="denied", details={"reason": "not_configured"})
        raise ApiError(409, "not_configured", "AI concept previews need an image or video generation adapter. None is "
                       "installed in this deployment, so nothing was generated and no data left the server.")
    if not any(s["status"] == "available" for s in statuses):
        raise ApiError(403, "consent_required", "Grant consent for the generation provider in Security & Privacy first.")
    raise ApiError(501, "adapter_missing", "A provider endpoint is configured but this build has no adapter for it.")


routes = [
    Route("/api/projects/{pid}/analysis", get_analysis, methods=["GET"]),
    Route("/api/projects/{pid}/analysis/rerun", rerun_analysis, methods=["POST"]),
    Route("/api/projects/{pid}/concepts/generate", generate, methods=["POST"]),
    Route("/api/projects/{pid}/concepts", list_concepts, methods=["GET"]),
    Route("/api/concepts/{cid}", get_concept, methods=["GET"]),
    Route("/api/concepts/{cid}/history", history, methods=["GET"]),
    Route("/api/concepts/{cid}/refine", refine_concept, methods=["POST"]),
    Route("/api/concepts/{cid}/revert", revert, methods=["POST"]),
    Route("/api/concepts/{cid}/select", select, methods=["POST"]),
    Route("/api/concepts/{cid}/camera/{shot}", _planner("camera_configurations", _default_camera), methods=["GET", "PUT", "DELETE"]),
    Route("/api/concepts/{cid}/lighting/{shot}", _planner("lighting_configurations", _default_lighting), methods=["GET", "PUT", "DELETE"]),
    Route("/api/concepts/{cid}/storyboard", storyboard, methods=["GET", "PUT"]),
    Route("/api/concepts/{cid}/scene3d", scene3d, methods=["GET", "PUT"]),
    Route("/api/concepts/{cid}/shotlist", create_shotlist, methods=["POST"]),
    Route("/api/concepts/{cid}/shotlists", list_shotlists, methods=["GET"]),
    Route("/api/concepts/{cid}/ai-preview", ai_preview, methods=["POST"]),
]
