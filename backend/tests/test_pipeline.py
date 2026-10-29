"""Phase 2: ingest + analysis through the real worker, checked against clips with known ground truth."""
from conftest import wait_jobs


def _ingest(user, clip, title="clip"):
    pid = user.post("/api/projects", {"title": title}).json["id"]
    assert user.upload(pid, clip).status == 201
    jobs = wait_jobs(user, pid)
    assert {j["kind"] for j in jobs} == {"ingest", "analyze"}, jobs
    assert all(j["status"] == "completed" for j in jobs), jobs
    return pid


def test_ingest_produces_proxy_and_keyframes(user, clips):
    pid = _ingest(user, clips["pan"])
    p = user.get(f"/api/projects/{pid}").json
    assert p["video"]["status"] == "ready" and p["video"]["proxy_blob_id"]
    frames = user.get(f"/api/projects/{pid}/frames").json["frames"]
    assert 6 <= len(frames) <= 10
    img = user.get(frames[0]["url"])
    assert img.status == 200 and img.body[:3] == b"\xff\xd8\xff"   # JPEG decrypted on the fly
    tok = user.post(f"/api/projects/{pid}/media-token", {"blob_id": p["video"]["proxy_blob_id"]}).json["url"]
    proxy = user.get(tok, headers={"Range": "bytes=0-11"})
    assert proxy.status == 206 and proxy.body[4:8] == b"ftyp"


def test_analysis_detects_pan(user, clips):
    pid = _ingest(user, clips["pan"])
    a = user.get(f"/api/projects/{pid}/analysis").json["data"]
    assert len(a["shots"]) == 1
    mv = a["shots"][0]["movement"]
    assert mv["movement"]["value"].startswith("pan right"), mv
    assert mv["movement"]["basis"] == "estimated"
    assert abs(mv["metrics"]["pan_rate"] - 0.125) < 0.02          # truth: 160 px/s over a 1280 px window
    assert a["clip"]["duration_s"]["basis"] == "observed"
    assert len(a["color"]["palette"]["value"]) == 5
    assert any("cannot be recovered" in x for x in a["limitations"])


def test_analysis_static_zoom_and_cut(user, clips):
    pid = _ingest(user, clips["static"])
    a = user.get(f"/api/projects/{pid}/analysis").json["data"]
    assert a["shots"][0]["movement"]["movement"]["value"].startswith("static")

    pid = _ingest(user, clips["zoom"])
    a = user.get(f"/api/projects/{pid}/analysis").json["data"]
    assert a["shots"][0]["movement"]["movement"]["value"].startswith("zoom in")

    pid = _ingest(user, clips["cut"])
    a = user.get(f"/api/projects/{pid}/analysis").json["data"]
    assert len(a["shots"]) == 2
    assert abs(a["shots"][1]["start_s"] - 3.0) < 0.3
    frames = user.get(f"/api/projects/{pid}/frames").json["frames"]
    assert {f["shot_index"] for f in frames} == {0, 1}
    assert sum(f["is_keyframe"] for f in frames) == 2


def test_rerun_is_idempotent(user, clips):
    pid = _ingest(user, clips["static"])
    first = user.get(f"/api/projects/{pid}/analysis").json["id"]
    r1 = user.post(f"/api/projects/{pid}/analysis/rerun")
    r2 = user.post(f"/api/projects/{pid}/analysis/rerun")
    assert r1.status == r2.status == 202
    jobs = wait_jobs(user, pid)
    assert len([j for j in jobs if j["kind"] == "analyze"]) == 1      # same idempotency key → one job row
    second = user.get(f"/api/projects/{pid}/analysis").json["id"]
    assert first != second                                              # replaced, not duplicated


def test_analysis_not_fabricated_before_ready(user):
    pid = user.post("/api/projects", {"title": "empty"}).json["id"]
    r = user.get(f"/api/projects/{pid}/analysis")
    assert r.status == 404 and r.json["error"]["code"] == "analysis_not_ready"
