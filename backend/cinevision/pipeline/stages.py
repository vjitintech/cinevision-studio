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
    with storage.private_tempdir("ingest-") as tmp:
        src = storage.decrypt_to_file(orig, tmp / "source.bin")
        ctx.progress(0.15, "Creating browser preview (H.264)")
        proxy = tmp / "proxy.mp4"
        _ffmpeg(["-i", f"file:{src}", "-map", "0:v:0", "-map", "0:a:0?", "-vf", "scale='min(1280,iw)':-2:flags=bicubic,format=yuv420p",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
                 "-t", str(settings().max_duration_s), "-map_metadata", "-1", f"file:{proxy}"], settings().ffmpeg_timeout_s)
        ctx.progress(0.6, "Extracting keyframes")
        dur = float(meta["duration_s"])
        fps = min(2.0, MAX_KEYFRAMES / max(dur, 0.5))
        frames_dir = tmp / "frames"
        frames_dir.mkdir()
        _ffmpeg(["-i", f"file:{proxy}", "-vf", f"fps={fps:.4f},scale=640:-2", "-q:v", "4", "-frames:v", str(MAX_KEYFRAMES),
                 f"file:{frames_dir}/f_%04d.jpg"], 300)
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
            img = cv2.imread(str(f))
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
    return aid


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
    return batch
