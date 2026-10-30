"""'Is my footage already good?' verdict, and the optional local vision model."""
import copy
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
import pytest
from conftest import wait_jobs

from cinevision import config, db
from cinevision.adapters import registry, vision
from cinevision.concepts import assessment as A
from cinevision.concepts import builder


def _analysis(size="medium", angle="eye level", move="static (locked-off)", key="mid-key", cast="neutral",
              clip_hi=0.0, mean=0.45, roll=None):
    t = lambda v: {"value": v, "confidence": 0.6, "basis": "estimated"}  # noqa: E731
    shot = {"index": 0, "start_s": 0, "end_s": 4, "representative_t": 2, "shot_size": t(size), "camera_angle": t(angle),
            "movement": {"movement": t(move), "handheld": t(False), "metrics": {"jitter": 0}},
            "composition": {"horizon_roll_deg": t(roll)}, "subjects": {"positions": t([])},
            "scene_geometry": t({"subjects_m": [{"x": 0, "z": 3}]})}
    return {"clip": {"duration_s": t(4)}, "shots": [shot],
            "color": {"tonal_key": t(key), "color_temperature": t({"cast": cast}), "mean_luminance": t(mean),
                      "contrast": t({"range_p5_p95": 0.6}), "clipping": t({"highlights": clip_hi, "shadows": 0.0}),
                      "palette": t([{"hex": "#806040"}, {"hex": "#203040"}])},
            "environment": {"setting": t("indoor"), "time_of_day": t("uncertain"), "practical_lights": t([])},
            "subjects": {"count": t(1)}, "narrative": {"pacing": t({"motion_energy": 0.02})}}


def _concepts(analysis, **ctl):
    controls = {"concept_count": 4, "preservation": "high", "movement_intensity": 0, "lighting_complexity": 1,
                "treatment": "realistic", "budget": "low", "sensor": "super35", **ctl}
    return [{"id": str(i), "data": c} for i, c in enumerate(builder.generate(analysis, controls))], controls


def test_good_footage_is_called_good_and_others_still_listed():
    an = _analysis()
    cs, ctl = _concepts(an)
    r = A.assess(an, cs, ctl)
    assert r["verdict"] == "good", r["matches"]
    assert r["headline"].startswith("Your footage is good")
    assert r["best_match"]["score"] >= A.GOOD_THRESHOLD and r["best_match"]["already_matches"]
    assert len(r["matches"]) == len(cs) >= 2          # alternatives are still offered


def test_technical_problems_downgrade_verdict():
    an = _analysis(clip_hi=0.15)
    cs, ctl = _concepts(an)
    r = A.assess(an, cs, ctl)
    assert r["verdict"] == "good_with_fixes"
    assert any("blown-out" in i["issue"] for i in r["technical_issues"])
    an2 = _analysis(roll=4.0)
    assert any("horizon" in i["issue"] for i in A.technical_checks(an2, ctl))


def test_different_footage_gets_alternatives():
    an = _analysis(size="extreme wide", angle="high angle", move="pan right, handheld", key="high-key", cast="cool")
    cs, ctl = _concepts(an, preservation="low", movement_intensity=2, genre="thriller")
    r = A.assess(an, cs, ctl)
    assert r["verdict"] == "alternatives"
    assert "different approach" in r["headline"]


def test_assessment_endpoint(user, clips):
    pid = user.post("/api/projects", {"title": "assess"}).json["id"]
    user.upload(pid, clips["static"])
    wait_jobs(user, pid)
    user.post(f"/api/projects/{pid}/concepts/generate", {"controls": {"concept_count": 3, "movement_intensity": 0}})
    wait_jobs(user, pid)
    r = user.get(f"/api/projects/{pid}/assessment")
    assert r.status == 200 and r.json["verdict"] in ("good", "good_with_fixes", "alternatives")
    assert len(r.json["matches"]) == 3 and all(0 <= m["score"] <= 100 for m in r.json["matches"])


# ---------------------------------------------------------------- vision adapter
class FakeVision(BaseHTTPRequestHandler):
    seen = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeVision.seen.append(body)
        reply = {"setting": "interior kitchen", "time_of_day": "night", "subjects": ["a person at a table, waiting"],
                 "action": "Someone waits alone.", "mood": ["tense", "lonely"], "genre_hints": ["thriller", "drama"],
                 "lighting": "single warm practical, hard", "strengths": ["strong practical motivation"],
                 "improvements": ["add a cool rim"], "extra": "ignored"}
        out = json.dumps({"message": {"content": json.dumps(reply)}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


@pytest.fixture
def fake_vision(tmp_path):
    srv = HTTPServer(("127.0.0.1", 0), FakeVision)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    s = config.Settings(data_dir=tmp_path, master_key=os.urandom(32), ollama_url=f"http://127.0.0.1:{srv.server_port}",
                        ollama_vision_model="qwen2.5vl:7b")
    for d in (s.data_dir, s.blob_dir, s.tmp_dir):
        d.mkdir(parents=True, exist_ok=True)
    config.reset_settings(s)
    db.close_thread_connection()
    db.init_db()
    yield
    srv.shutdown()
    db.close_thread_connection()
    config.reset_settings(None)


def test_local_vision_model(fake_vision):
    assert registry.adapter_status("ollama-vision", None)["status"] == "available"
    frames = [np.full((720, 1280, 3), i * 20, np.uint8) for i in range(10)]
    out = vision.describe("u", frames)
    assert out["frames_sent"] == 4 and out["notes"]["basis"] == "heuristic"
    assert out["notes"]["value"]["mood"] == ["tense", "lonely"] and "extra" not in out["notes"]["value"]
    sent = FakeVision.seen[-1]["messages"][0]
    assert len(sent["images"]) == 4 and "Do NOT identify" in sent["content"]
    # its hints nudge ranking toward matching archetypes
    an = _analysis()
    base = dict((k, s) for k, s, _ in builder.score_archetypes(an, {}))
    an["ai_notes"] = out
    nudged = dict((k, s) for k, s, _ in builder.score_archetypes(an, {}))
    assert nudged["suspense"] > base["suspense"] and nudged["stylized"] == base["stylized"]


def test_vision_not_configured_by_default(tmp_path):
    s = config.Settings(data_dir=tmp_path, master_key=os.urandom(32))
    for d in (s.data_dir, s.blob_dir, s.tmp_dir):
        d.mkdir(parents=True, exist_ok=True)
    config.reset_settings(s)
    db.close_thread_connection()
    db.init_db()
    try:
        assert registry.adapter_status("ollama-vision", None)["status"] == "not_configured"
        assert not vision.available(None)
    finally:
        db.close_thread_connection()
        config.reset_settings(None)
