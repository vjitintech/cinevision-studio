"""Job handlers. Each handler is idempotent: outputs for the same input are replaced inside one transaction."""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

from .. import db, jobs, storage
from ..config import settings
from ..keys import keyring
from ..media import sandbox
from ..worker import UserFacingError, handler

MAX_KEYFRAMES = 120


def _video(video_id: str, owner_id: str):
    v = db.one("SELECT * FROM uploaded_videos WHERE id=? AND owner_id=?", (video_id, owner_id))
    if v is None:
        raise UserFacingError("The clip no longer exists.")
    proj = db.one("SELECT status FROM projects WHERE id=?", (v["project_id"],))
    if not proj or proj["status"] != "active":
        raise jobs.Cancelled()
    return v


def _ffmpeg(args: list[str], timeout: int) -> None:
    s = settings()
    res = sandbox.run([s.ffmpeg_bin, "-nostdin", "-hide_banner", "-loglevel", "error", "-protocol_whitelist", "file",
                       *args], sandbox.Limits(cpu_s=timeout * 2, timeout_s=timeout))
    if res.returncode != 0:
        raise UserFacingError("Video processing failed while decoding the clip.", retryable=False)


@handler("ingest")
def ingest(ctx: jobs.JobContext, params: dict) -> str:
    owner = ctx.job["owner_id"]
    v = _video(params["video_id"], owner)
    meta = dict(db.one("SELECT * FROM video_metadata WHERE video_id=?", (v["id"],)))
    orig = db.one("SELECT * FROM blobs WHERE id=?", (v["original_blob_id"],))
    db.run("UPDATE uploaded_videos SET status='ingesting' WHERE id=?", (v["id"],))
    ctx.progress(0.05, "Decrypting into private workspace")
    dur = float(meta["duration_s"])
    tlimit = max(settings().ffmpeg_timeout_s, int(dur * 3))      # long films need proportionally longer
    with storage.private_tempdir("ingest-") as tmp:
        src = storage.decrypt_to_file(orig, tmp / "source.bin")
        ctx.progress(0.15, "Creating browser preview (H.264)")
        proxy = tmp / "proxy.mp4"
        # even dimensions are required by H.264 4:2:0, so odd sizes (e.g. 1081 px) are rounded down
        vf = "scale='trunc(min(1280,iw)/2)*2':-2:flags=bicubic,format=yuv420p"
        common = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-movflags", "+faststart",
                  "-t", str(settings().max_duration_s), "-map_metadata", "-1"]
        try:
            _ffmpeg(["-i", f"file:{src}", "-map", "0:v:0", "-map", "0:a:0?", "-vf", vf, *common,
                     "-c:a", "aac", "-b:a", "128k", f"file:{proxy}"], tlimit)
        except UserFacingError:
            # unusual audio (or a damaged audio track) shouldn't block the picture: retry without sound,
            # and tolerate decode errors in damaged files
            proxy.unlink(missing_ok=True)
            _ffmpeg(["-err_detect", "ignore_err", "-i", f"file:{src}", "-map", "0:v:0", "-an", "-vf", vf, *common,
                     f"file:{proxy}"], tlimit)
        ctx.progress(0.6, "Extracting keyframes")
        fps = min(2.0, MAX_KEYFRAMES / max(dur, 0.5))
        frames_dir = tmp / "frames"
        frames_dir.mkdir()
        _ffmpeg(["-i", f"file:{proxy}", "-vf", f"fps={fps:.4f},scale=640:-2", "-q:v", "4", "-frames:v", str(MAX_KEYFRAMES),
                 f"file:{frames_dir}/f_%04d.jpg"], max(300, int(dur)))
        files = sorted(frames_dir.glob("f_*.jpg"))
        if not files:
            raise UserFacingError("No frames could be extracted from the clip.")
        ctx.progress(0.75, "Encrypting derived media")
        old_blobs = [r["id"] for r in db.all_(
            "SELECT b.id FROM blobs b WHERE b.project_id=? AND (b.kind='frame' OR b.id=?)",
            (v["project_id"], v["proxy_blob_id"] or ""))]
        proxy_id = storage.put_file(proxy, owner_id=owner, project_id=v["project_id"], kind="proxy", content_type="video/mp4")
        frame_rows = []
        for i, f in enumerate(files):
            # np.fromfile + imdecode works with any path (cv2.imread fails on non-ASCII Windows paths)
            img = cv2.imdecode(np.fromfile(str(f), np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                continue
            bid = storage.put_file(f, owner_id=owner, project_id=v["project_id"], kind="frame", content_type="image/jpeg")
            frame_rows.append((db.new_id(), owner, v["project_id"], v["id"], bid, i, round(min(dur, i / fps), 3),
                               img.shape[1], img.shape[0]))
            if i % 10 == 0:
                ctx.progress(0.75 + 0.2 * i / len(files), "Encrypting derived media")
        ctx.check_cancel()
        with db.tx() as c:
            c.execute("DELETE FROM extracted_frames WHERE video_id=?", (v["id"],))
            c.executemany("INSERT INTO extracted_frames(id,owner_id,project_id,video_id,blob_id,idx,t_s,width,height)"
                          " VALUES (?,?,?,?,?,?,?,?,?)", frame_rows)
            c.execute("UPDATE uploaded_videos SET proxy_blob_id=?, status='ready' WHERE id=?", (proxy_id, v["id"]))
            c.executemany("DELETE FROM blobs WHERE id=?", [(b,) for b in old_blobs])
        for b in old_blobs:
            storage.destroy_blob_file(b)
    jobs.enqueue(owner_id=owner, project_id=v["project_id"], kind="analyze", idem_key=f"analyze:{v['id']}",
                 params={"video_id": v["id"]})
    return proxy_id


@handler("analyze")
def analyze(ctx: jobs.JobContext, params: dict) -> str:
    from ..analysis import engine

    owner = ctx.job["owner_id"]
    v = _video(params["video_id"], owner)
    if not v["proxy_blob_id"]:
        raise UserFacingError("The clip has not been prepared yet.", retryable=True)
    meta = dict(db.one("SELECT * FROM video_metadata WHERE video_id=?", (v["id"],)))
    frames = db.all_("SELECT f.*, b.wrapped_dek, b.plaintext_size FROM extracted_frames f JOIN blobs b ON b.id=f.blob_id"
                     " WHERE f.video_id=? ORDER BY f.idx", (v["id"],))
    with storage.private_tempdir("analyze-") as tmp:
        proxy = storage.decrypt_to_file(db.one("SELECT * FROM blobs WHERE id=?", (v["proxy_blob_id"],)), tmp / "proxy.mp4")
        keyframes = []
        for f in frames:
            data = storage.read_bytes(db.one("SELECT * FROM blobs WHERE id=?", (f["blob_id"],)))
            img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            if img is not None:
                keyframes.append((f["t_s"], img))
        result = engine.run(str(proxy), keyframes, meta, lambda p, stage: ctx.progress(p * 0.9, stage))
        from ..adapters import vision
        if vision.available(owner) and keyframes:
            ctx.progress(0.92, "Asking the local vision model to describe the scene")
            try:
                reps = [img for t, img in keyframes if any(abs(t - sh["representative_t"]) < 0.6 for sh in result["shots"])] \
                    or [img for _, img in keyframes]
                result["ai_notes"] = vision.describe(owner, reps)
                result["engine"] = {**result["engine"], "vision_model": result["ai_notes"]["model"]}
            except Exception as e:  # never fabricate: record that it failed and carry on
                result["ai_notes"] = {"status": "failed", "error": type(e).__name__}
    ctx.check_cancel()
    shots = result["shots"]
    aid = db.new_id()
    k = keyring()
    with db.tx() as c:
        c.execute("DELETE FROM scene_analyses WHERE video_id=?", (v["id"],))
        c.execute("INSERT INTO scene_analyses(id,owner_id,project_id,video_id,data_enc,engine,created_at) VALUES (?,?,?,?,?,?,?)",
                  (aid, owner, v["project_id"], v["id"], k.enc_json(result, f"scene_analyses:data:{aid}"),
                   json.dumps(result["engine"]), db.now_iso()))
        # tag frames with shot index and mark each shot's representative frame as keyframe
        c.execute("UPDATE extracted_frames SET shot_index=NULL, is_keyframe=0 WHERE video_id=?", (v["id"],))
        for sh in shots:
            c.execute("UPDATE extracted_frames SET shot_index=? WHERE video_id=? AND t_s>=? AND t_s<?",
                      (sh["index"], v["id"], sh["start_s"], sh["end_s"] + (1e-3 if sh is shots[-1] else 0)))
            c.execute("UPDATE extracted_frames SET is_keyframe=1 WHERE id=(SELECT id FROM extracted_frames WHERE video_id=?"
                      " ORDER BY ABS(t_s-?) LIMIT 1)", (v["id"], sh["representative_t"]))
        c.execute("UPDATE projects SET updated_at=? WHERE id=?", (db.now_iso(), v["project_id"]))
    link = db.one("SELECT assembly_id FROM assembly_clips WHERE clip_project_id=?", (v["project_id"],))
    if link:
        _maybe_assemble(owner, link["assembly_id"])      # clips of a movie assembly don't get their own concepts
    else:
        _auto_concepts(owner, v["project_id"], aid)
    return aid


def _maybe_assemble(owner: str, assembly_id: str) -> None:
    """When the last clip of an assembly finishes analysis, order the movie automatically."""
    import hashlib
    rows = db.all_("SELECT c.clip_project_id, a.id AS aid FROM assembly_clips c LEFT JOIN scene_analyses a"
                   " ON a.project_id=c.clip_project_id WHERE c.assembly_id=?", (assembly_id,))
    if not rows or any(r["aid"] is None for r in rows):
        return
    h = hashlib.sha256("|".join(sorted(r["aid"] for r in rows)).encode()).hexdigest()[:20]
    jobs.enqueue(owner_id=owner, project_id=assembly_id, kind="assemble", idem_key=f"assemble:{assembly_id}:{h}",
                 params={"lang": user_language(owner)})


def _auto_concepts(owner: str, pid: str, analysis_id: str) -> None:
    """Zero-input flow: as soon as the clip is analysed, generate concepts from the project's brief (defaults if
    the user gave none). The review job follows the concepts job."""
    import hashlib
    proj = db.one("SELECT brief_enc FROM projects WHERE id=?", (pid,))
    from ..api.projects import CreativeControls
    controls = keyring().dec_json(proj["brief_enc"], f"projects:brief:{pid}") if proj else None
    controls = controls or CreativeControls().model_dump()
    h = hashlib.sha256(json.dumps({"c": controls, "a": analysis_id}, sort_keys=True).encode()).hexdigest()[:24]
    jobs.enqueue(owner_id=owner, project_id=pid, kind="concepts", idem_key=f"concepts:{pid}:{h}",
                 params={"controls": controls, "controls_hash": h, "then_review": True})


@handler("concepts")
def concepts(ctx: jobs.JobContext, params: dict) -> str:
    from ..concepts import builder

    owner, pid = ctx.job["owner_id"], ctx.job["project_id"]
    proj = db.one("SELECT status FROM projects WHERE id=? AND owner_id=?", (pid, owner))
    if not proj or proj["status"] != "active":
        raise jobs.Cancelled()
    row = db.one("SELECT * FROM scene_analyses WHERE project_id=? AND owner_id=?", (pid, owner))
    if row is None:
        raise UserFacingError("Analyse the clip before generating concepts.")
    k = keyring()
    analysis = k.dec_json(row["data_enc"], f"scene_analyses:data:{row['id']}")
    ctx.progress(0.2, "Scoring creative approaches")
    concepts_ = builder.generate(analysis, params.get("controls") or {})
    ctx.progress(0.8, "Saving concepts")
    ctx.check_cancel()
    batch = db.new_id()
    ctl = params.get("controls") or {}
    recorded = {k: ctl.get(k) for k in ("movement_intensity", "lighting_complexity", "budget", "preservation", "treatment",
                                        "sensor", "concept_count", "intent", "aspect_ratio", "frame_rate")}
    with db.tx() as c:
        # a retried job replaces its own earlier output instead of adding a second batch
        c.execute("DELETE FROM cinematography_concepts WHERE project_id=? AND batch_id IN "
                  "(SELECT batch_id FROM cinematography_concepts WHERE engine LIKE ?)", (pid, f'%"job": "{ctx.id}"%'))
        for con in concepts_:
            cid = db.new_id()
            eng = json.dumps({**builder.ENGINE, "job": ctx.id, "archetype": con["archetype"], "score": con.get("score"),
                              "controls_hash": params.get("controls_hash"), "settings": recorded})
            c.execute("INSERT INTO cinematography_concepts(id,owner_id,project_id,analysis_id,batch_id,lineage_id,parent_id,"
                      "revision,is_current,is_selected,data_enc,engine,created_at) VALUES (?,?,?,?,?,?,NULL,1,1,0,?,?,?)",
                      (cid, owner, pid, row["id"], batch, cid, k.enc_json(con, f"cinematography_concepts:data:{cid}"),
                       eng, db.now_iso()))
        c.execute("UPDATE projects SET updated_at=? WHERE id=?", (db.now_iso(), pid))
    if params.get("then_review"):
        enqueue_review(owner, pid, row["id"])
    return batch


def enqueue_review(owner: str, pid: str, analysis_id: str, lang: str | None = None, force: bool = False):
    lang = lang or user_language(owner)
    key = f"review:{pid}:{analysis_id}:{lang}" + (f":{db.new_id()}" if force else "")
    return jobs.enqueue(owner_id=owner, project_id=pid, kind="review", idem_key=key, params={"lang": lang})


def user_language(uid: str) -> str:
    r = db.one("SELECT language FROM user_prefs WHERE user_id=?", (uid,))
    return r["language"] if r else "en"


@handler("review")
def review(ctx: jobs.JobContext, params: dict) -> str:
    """Director's review: built-in rules always; local AI notes in the user's language when it is switched on."""
    from ..concepts import assessment as AS
    from ..director import review as RV
    owner, pid = ctx.job["owner_id"], ctx.job["project_id"]
    proj = db.one("SELECT * FROM projects WHERE id=? AND owner_id=?", (pid, owner))
    if not proj or proj["status"] != "active":
        raise jobs.Cancelled()
    row = db.one("SELECT * FROM scene_analyses WHERE project_id=? AND owner_id=?", (pid, owner))
    if row is None:
        raise UserFacingError("Analyse the clip before the review.")
    k = keyring()
    analysis = k.dec_json(row["data_enc"], f"scene_analyses:data:{row['id']}")
    ctx.progress(0.1, "Reviewing camera, light, colour and sound")
    crow = db.all_("SELECT * FROM cinematography_concepts WHERE project_id=? AND owner_id=? AND is_current=1", (pid, owner))
    latest = db.one("SELECT batch_id FROM cinematography_concepts WHERE project_id=? AND owner_id=? AND revision=1"
                    " ORDER BY rowid DESC LIMIT 1", (pid, owner))
    concepts = [{"id": r["id"], "data": k.dec_json(r["data_enc"], f"cinematography_concepts:data:{r['id']}")}
                for r in crow if latest and r["batch_id"] == latest["batch_id"]]
    controls = k.dec_json(proj["brief_enc"], f"projects:brief:{pid}") or {}
    assessment = AS.assess(analysis, concepts, controls) if concepts else None
    data = RV.build(analysis, assessment)
    lang = params.get("lang") or "en"
    existing = db.one("SELECT * FROM director_reviews WHERE project_id=?", (pid,))
    ai_all = {}
    if existing and existing["ai_enc"] and existing["analysis_id"] == row["id"]:
        ai_all = k.dec_json(existing["ai_enc"], f"director_reviews:ai:{existing['id']}") or {}
    from ..adapters import localai
    if localai.available(owner):
        ctx.progress(0.3, "Asking the local AI director for notes (this can take a few minutes)")
        try:
            from ..director import ai as AI
            frames = []
            v = db.one("SELECT id FROM uploaded_videos WHERE project_id=? ORDER BY created_at DESC LIMIT 1", (pid,))
            if v:
                fr = db.all_("SELECT f.blob_id FROM extracted_frames f WHERE f.video_id=? AND f.is_keyframe=1 ORDER BY f.idx",
                             (v["id"],)) or db.all_("SELECT blob_id FROM extracted_frames WHERE video_id=? ORDER BY idx", (v["id"],))
                for f in fr[:: max(1, len(fr) // 6)][:6]:
                    img = cv2.imdecode(np.frombuffer(storage.read_bytes(db.one("SELECT * FROM blobs WHERE id=?",
                                                                                   (f["blob_id"],))), np.uint8), cv2.IMREAD_COLOR)
                    if img is not None:
                        frames.append(img)
            brief = (controls.get("brief") or "") if isinstance(controls, dict) else ""
            ai_all[lang] = AI.notes(owner, analysis, data, frames, lang, brief)
        except Exception as e:  # noqa: BLE001 - AI is an optional layer; never fail the review because of it
            ai_all[lang] = {"status": "failed", "error": str(e)[:200] if isinstance(e, localai.LocalAIError) else type(e).__name__}
    ctx.check_cancel()
    rid = existing["id"] if existing else db.new_id()
    with db.tx() as c:
        c.execute("DELETE FROM director_reviews WHERE project_id=?", (pid,))
        c.execute("INSERT INTO director_reviews(id,owner_id,project_id,analysis_id,data_enc,ai_enc,created_at)"
                  " VALUES (?,?,?,?,?,?,?)", (rid, owner, pid, row["id"], k.enc_json(data, f"director_reviews:data:{rid}"),
                                             k.enc_json(ai_all, f"director_reviews:ai:{rid}") if ai_all else None,
                                             db.now_iso()))
    return rid


@handler("assemble")
def assemble(ctx: jobs.JobContext, params: dict) -> str:
    """Order the clips of a movie assembly and flag misfits (rules always; local AI as an extra opinion)."""
    from ..assembly import engine as AE
    from ..concepts import assessment as AS
    owner, aid = ctx.job["owner_id"], ctx.job["project_id"]
    host = db.one("SELECT status FROM projects WHERE id=? AND owner_id=?", (aid, owner))
    if not host or host["status"] != "active":
        raise jobs.Cancelled()
    k = keyring()
    arow = db.one("SELECT * FROM assemblies WHERE project_id=?", (aid,))
    script = k.dec(arow["script_enc"], f"assemblies:script:{aid}") if arow and arow["script_enc"] else ""
    clips = []
    rows = db.all_("SELECT c.clip_project_id FROM assembly_clips c JOIN projects p ON p.id=c.clip_project_id"
                   " WHERE c.assembly_id=? AND p.status='active'", (aid,))
    ctx.progress(0.1, "Reading the clip analyses")
    for r in rows:
        pid = r["clip_project_id"]
        an = db.one("SELECT * FROM scene_analyses WHERE project_id=?", (pid,))
        v = db.one("SELECT * FROM uploaded_videos WHERE project_id=?", (pid,))
        if an is None or v is None:
            continue
        analysis = k.dec_json(an["data_enc"], f"scene_analyses:data:{an['id']}")
        meta = dict(db.one("SELECT * FROM video_metadata WHERE video_id=?", (v["id"],)) or {})
        name = k.dec(v["filename_enc"], f"uploaded_videos:filename:{v['id']}")
        clips.append({"id": pid, "name": name, "analysis": analysis, "meta": meta,
                      "issues": AS.technical_checks(analysis, {})})
    if not clips:
        raise UserFacingError("Upload and analyse at least one clip first.")
    ctx.progress(0.4, "Matching clips to the story")
    data = AE.assemble(clips, script)
    lang = params.get("lang") or "en"
    ai_all = {}
    from ..adapters import localai
    if localai.available(owner):
        ctx.progress(0.6, "Asking the local AI editor for its order (this can take a few minutes)")
        try:
            from ..assembly import ai as AAI
            ai_all[lang] = AAI.order(owner, clips, data, script, lang)
        except Exception as e:  # noqa: BLE001 - optional layer
            ai_all[lang] = {"status": "failed", "error": str(e)[:200] if isinstance(e, localai.LocalAIError) else type(e).__name__}
    ctx.check_cancel()
    with db.tx() as c:
        c.execute("DELETE FROM assembly_results WHERE assembly_id=?", (aid,))
        c.execute("INSERT INTO assembly_results(assembly_id,owner_id,data_enc,ai_enc,created_at) VALUES (?,?,?,?,?)",
                  (aid, owner, k.enc_json(data, f"assembly_results:data:{aid}"),
                   k.enc_json(ai_all, f"assembly_results:ai:{aid}") if ai_all else None, db.now_iso()))
        c.execute("UPDATE projects SET updated_at=? WHERE id=?", (db.now_iso(), aid))
    return aid
