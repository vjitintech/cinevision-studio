"""Analysis must finish for any decodable clip: a failing step degrades to a zero-confidence placeholder,
a failing decoder falls back to the next one, and concepts can still be generated from the result."""
import base64
import os

import cv2
import pytest

os.environ.setdefault("CINEVISION_MASTER_KEY", base64.b64encode(os.urandom(32)).decode())

from conftest import make_clip, wait_jobs  # noqa: E402
from cinevision.analysis import color, composition, engine, environment, motion, narrative, subjects  # noqa: E402
from cinevision.concepts import assessment, builder  # noqa: E402


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    p = make_clip(tmp_path_factory.mktemp("rob") / "cut.mp4", pattern="cut", duration=4)
    cap = cv2.VideoCapture(str(p))
    kfs, i = [], 0
    while True:
        ok, f = cap.read()
        if not ok:
            break
        if i % 12 == 0:
            kfs.append((i / 24, f))
        i += 1
    meta = {"duration_s": i / 24, "display_aspect": kfs[0][1].shape[1] / kfs[0][1].shape[0], "width": kfs[0][1].shape[1],
            "height": kfs[0][1].shape[0], "fps": 24, "video_codec": "h264"}
    return str(p), kfs, meta


def _boom(*a, **k):
    raise ValueError("injected failure")


def _check(result):
    assert result["shots"], result
    for sh in result["shots"]:
        assert {"shot_size", "movement", "composition", "subjects", "camera_angle"} <= sh.keys()
    concepts = builder.generate(result, {"concept_count": 4})
    assert len(concepts) == 4
    assessment.assess(result, [{"id": str(i), "data": c} for i, c in enumerate(concepts)], {})
    return result


def test_healthy_run_uses_ffmpeg_and_reports_no_failures(clip):
    r = _check(engine.run(*clip, lambda *_: None))
    assert r["engine"]["decoder"] == "ffmpeg" and r["engine"]["failed_steps"] == []
    assert r["continuity"]["value"]["shot_count"] == 2


@pytest.mark.parametrize("target", [
    (motion, "detect_shots"), (motion, "pair_motion"), (motion, "classify"), (color, "tone_and_color"),
    (color, "palette"), (composition, "analyse_frame"), (subjects, "detect"), (subjects, "summarise"),
    (subjects, "shot_size"), (environment, "classify"), (narrative, "interpret"),
])
def test_any_failing_step_degrades_instead_of_crashing(clip, monkeypatch, target):
    mod, name = target
    real = getattr(mod, name)
    calls = {"n": 0}

    def fail_first(*a, **k):        # fail on real input, allow the neutral fallback call to succeed
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("injected failure")
        return real(*a, **k)
    monkeypatch.setattr(mod, name, fail_first)
    r = _check(engine.run(*clip, lambda *_: None))
    if name not in ("pair_motion", "detect"):   # per-item failures are skipped silently
        assert r["engine"]["failed_steps"], name
        assert any("could not be measured" in x for x in r["limitations"])


def test_placeholders_have_zero_confidence(clip, monkeypatch):
    real = color.tone_and_color
    state = {"first": True}

    def once(frames):
        if state.pop("first", False):
            raise ValueError
        return real(frames)
    monkeypatch.setattr(color, "tone_and_color", once)
    r = engine.run(*clip, lambda *_: None)
    assert r["color"]["mean_luminance"]["confidence"] == 0.0
    assert "Not measured" in r["color"]["mean_luminance"]["note"]


def test_falls_back_to_opencv_then_keyframes(clip, monkeypatch):
    monkeypatch.setattr(engine, "_sample_ffmpeg", _boom)
    assert _check(engine.run(*clip, lambda *_: None))["engine"]["decoder"] == "opencv"
    monkeypatch.setattr(engine, "_sample_opencv", _boom)
    r = _check(engine.run(*clip, lambda *_: None))
    assert r["engine"]["decoder"] == "keyframes" and any("keyframes" in x for x in r["limitations"])


def test_odd_width_clip_is_accepted_end_to_end(user, tmp_path):
    p = make_clip(tmp_path / "odd.avi", pattern="pan", duration=2, size="1081x607", codec="mpeg4")
    pid = user.post("/api/projects", {"title": "odd"}).json["id"]
    assert user.upload(pid, p).status == 201
    jobs = wait_jobs(user, pid)
    assert all(j["status"] == "completed" for j in jobs), jobs
