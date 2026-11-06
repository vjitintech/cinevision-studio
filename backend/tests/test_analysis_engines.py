"""Analyser engines: faces/people, interior/exterior + day/night, on-screen text reading, speech to text, and the
adaptive detail budget for long films. Optional engines are faked where they are not installed."""
import base64
import json
import os
import subprocess
import sys
import threading
import types
import wave
from http.server import BaseHTTPRequestHandler, HTTPServer

import cv2
import numpy as np
import pytest

os.environ.setdefault("CINEVISION_MASTER_KEY", base64.b64encode(os.urandom(32)).decode())

import synthetic_scenes as SS  # noqa: E402
from cinevision.analysis import engine, environment, speech, subjects, titles  # noqa: E402
from cinevision.director import review as RV  # noqa: E402
from cinevision.director.render import render  # noqa: E402


# ---------------------------------------------------------------- environment

@pytest.mark.parametrize("scene,setting,tod", [
    ("night_exterior", "outdoor", "night"),
    ("lit_room", "indoor", None),
    ("overcast_day", "outdoor", "day"),
    ("window_interior", "indoor", "day"),
])
def test_environment_on_synthetic_scenes(scene, setting, tod):
    img = SS.ALL[scene]()
    lum = float(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).mean()) / 255
    r = environment.classify([img], lum, "neutral")
    assert r["setting"]["value"] == setting, (scene, r["cues"])
    assert r["setting"]["confidence"] >= 0.55
    if tod:
        assert r["time_of_day"]["value"] == tod, (scene, r["cues"])
    assert r["setting"]["basis"] == "heuristic"


def test_dark_sky_is_not_read_as_interior():
    img = SS.night_exterior()
    r = environment.classify([img], 0.06, "warm")
    assert r["setting"]["value"] != "indoor"
    assert "night sky" in r["cues"]["value"]["seen"]


def test_conflicting_cues_give_uncertain():
    r = environment.classify([SS.night_exterior(), SS.lit_room_with_lamp()], 0.25, "neutral")
    assert r["setting"]["value"] == "uncertain" and r["setting"]["confidence"] <= 0.3
    assert r["cues"]["value"]["conflicting"] is True


def test_black_title_card_is_not_a_starry_night():
    img = np.zeros((360, 640, 3), np.uint8)
    cv2.putText(img, "A FILM BY SOMEONE", (90, 150), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (255, 255, 255), 3)
    r = environment.classify([img], 0.03, "neutral")
    assert r["setting"]["value"] == "uncertain"


# ---------------------------------------------------------------- faces / people

def test_bundled_face_model_is_default_and_env_overrides(monkeypatch):
    monkeypatch.delenv("CINEVISION_FACE_MODEL", raising=False)
    subjects._reset_face_model()
    assert subjects.face_model_path() == str(subjects.BUNDLED_FACE_MODEL)
    assert subjects.face_model_available() and subjects.detector_name().startswith("yunet")
    monkeypatch.setenv("CINEVISION_FACE_MODEL", "/nonexistent/model.onnx")
    subjects._reset_face_model()
    assert not subjects.face_model_available()
    s = subjects.summarise([{"people": [], "faces": []}])
    assert s["face_detection"]["value"] == "not available" and s["detector"]["value"] == "hog-people"
    monkeypatch.delenv("CINEVISION_FACE_MODEL")
    subjects._reset_face_model()


@pytest.mark.parametrize("h,abbr", [(0.6, "ECU"), (0.4, "CU"), (0.3, "MCU"), (0.15, "MS"), (0.05, "WS"), (0.02, "EWS")])
def test_face_height_gives_shot_size(h, abbr):
    det = {"people": [], "faces": [{"x": 0.4, "y": 0.1, "w": h * 0.6, "h": h, "score": 0.9}]}
    out = subjects.shot_size([det, det], 0.3)
    assert out["abbr"] == abbr and out["source"] == "face" and out["basis"] == "estimated"


def test_close_up_found_from_the_face(tmp_path):
    """The sample clip's portrait: HOG sees no body, the face detector finds the person and sizes the shot."""
    src = os.path.join(os.path.dirname(__file__), "..", "..", "samples", "sample-two-shots.mp4")
    out = tmp_path / "f.png"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "1", "-i", src, "-frames:v", "1", str(out)], check=True)
    d = subjects.detect(cv2.imread(str(out)))
    assert d["faces"] and any(p.get("source") in ("face", "face+body") for p in d["people"])
    assert subjects.shot_size([d], 0.3)["abbr"] in ("MCU", "CU")
    # the rocket on the launch pad is not a person
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "4", "-i", src, "-frames:v", "1", str(out)], check=True)
    assert subjects.detect(cv2.imread(str(out)))["people"] == []


def test_pole_is_rejected_and_person_shape_kept():
    g = np.full((256, 128), 30, np.uint8)
    cv2.rectangle(g, (58, 0), (70, 255), 220, -1)                 # a pole: one thin bright column
    assert subjects._pole_like(g, (0, 0, 128, 256))
    p = np.full((256, 128), 30, np.uint8)
    cv2.circle(p, (64, 40), 20, 200, -1)                           # head
    cv2.rectangle(p, (22, 62), (106, 170), 180, -1)                # shoulders + torso
    cv2.rectangle(p, (36, 170), (58, 255), 160, -1)                # legs
    cv2.rectangle(p, (70, 170), (92, 255), 160, -1)
    assert not subjects._pole_like(p, (0, 0, 128, 256))


# ---------------------------------------------------------------- on-screen text

def _card(text_lines, size=(720, 1280), fg=255, bg=0, y0=300):
    img = np.full((*size, 3), bg, np.uint8)
    for i, t in enumerate(text_lines):
        cv2.putText(img, t, (260, y0 + i * 90), cv2.FONT_HERSHEY_SIMPLEX, 2.0, (fg, fg, fg), 5)
    return img


def _samples(img, secs, fps=8):
    small = cv2.resize(img, (320, 180), interpolation=cv2.INTER_AREA)
    return [(i / fps, small) for i in range(int(secs * fps))]


@pytest.mark.skipif(not titles.ocr_engine()["available"], reason="tesseract not installed")
def test_titles_are_read_and_too_short_cards_flagged():
    big = _card(["THE END", "Directed by Jane Doe", "Music by John Smith"])
    r = titles.detect(_samples(big, 1.5), 1.5, frame_at=lambda t: big, lang="en")
    seg = r["value"][0]
    assert "END" in seg["text"] and seg["words"] >= 8 and seg["needed_s"] > seg["on_screen_s"]
    assert seg["title_safe"] is True and seg["contrast_ratio"] > 10
    assert r["reading"]["available"] and r["reading"]["languages"].startswith("eng")
    rv = RV.build({"on_screen_text": r, "shots": []}, None)
    codes = {f["code"] for d in rv["departments"] for f in d["findings"]}
    assert "titles.too_short_to_read" in codes and "titles.read" in codes


def test_text_reading_missing_degrades(monkeypatch):
    monkeypatch.setattr(titles, "_tess", {"available": False, "path": None, "version": None, "languages": []})
    big = _card(["OPENING TITLE", "a film by someone", "another line here"])
    r = titles.detect(_samples(big, 3), 3, frame_at=lambda t: big)
    assert r["value"] and "text" not in r["value"][0]
    assert r["reading"]["available"] is False and titles.NOT_AVAILABLE in r["note"]
    rv = render(RV.build({"on_screen_text": r, "shots": []}, None), "en")
    f = [f for d in rv["departments"] for f in d["findings"] if f["code"] == "titles.ocr_missing"]
    assert f and "not available" in f[0]["observation"]


def test_unsafe_and_low_contrast_text_flagged():
    img = np.full((720, 1280, 3), 110, np.uint8)
    for i, t in enumerate(["EDGE TEXT ONE", "EDGE TEXT TWO", "EDGE TEXT THREE"]):
        cv2.putText(img, t, (4, 40 + i * 70), cv2.FONT_HERSHEY_SIMPLEX, 1.8, (150, 150, 150), 5)
    r = titles.detect(_samples(img, 3), 30, frame_at=lambda t: img)
    seg = r["value"][0]
    assert seg["title_safe"] is False and seg["contrast_ratio"] < 3
    rv = RV.build({"on_screen_text": r, "shots": []}, None)
    codes = {f["code"] for d in rv["departments"] for f in d["findings"]}
    assert {"titles.unsafe", "titles.low_contrast"} <= codes


# ---------------------------------------------------------------- speech

def _speechlike_wav(path, secs=12.0, bed_db=None, sr=16000):
    """Syllable-like bursts (4 Hz) with pauses; optionally a loud steady 'music' bed under it."""
    rng = np.random.default_rng(0)
    t = np.arange(int(secs * sr)) / sr
    env = (np.sin(2 * np.pi * 4 * t) > 0.2).astype(np.float32)
    x = 0.3 * rng.standard_normal(len(t)).astype(np.float32) * env
    if bed_db is not None:
        x += (10 ** (bed_db / 20)) * np.sin(2 * np.pi * 220 * t).astype(np.float32)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())
    return path


def _raw(n=3, secs=12.0, lp=-0.3):
    seg = secs / n
    return {"engine": "test", "language": "en", "language_probability": 0.97, "partial": False,
            "segments": [{"start_s": i * seg, "end_s": i * seg + seg - 0.2,
                          "text": "this is a line of dialogue spoken here", "avg_logprob": lp, "no_speech_prob": 0.05}
                         for i in range(n)]}


def test_dialogue_buried_under_a_loud_bed(tmp_path):
    clean = speech.measure(_raw(), speech.envelope(str(_speechlike_wav(tmp_path / "a.wav"))), 12)
    assert clean["buried"] == [] and clean["words_per_minute"] and clean["language"] == "en"
    loud = speech.measure(_raw(), speech.envelope(str(_speechlike_wav(tmp_path / "b.wav", bed_db=-12))), 12)
    assert len(loud["buried"]) == 3


def test_dead_air_fast_and_unclear_lines(tmp_path):
    env = np.full(50 * 40, -70.0, np.float32)
    raw = {"engine": "t", "language": "en", "segments": [
        {"start_s": 0, "end_s": 3, "text": " ".join(["word"] * 14), "avg_logprob": -0.2, "no_speech_prob": 0.0},
        {"start_s": 15, "end_s": 18, "text": "mumble mumble", "avg_logprob": -1.4, "no_speech_prob": 0.3}]}
    d = speech.measure(raw, env, 40)
    assert len(d["dead_air"]) == 1 and d["dead_air"][0]["at"] == "0:03"
    assert len(d["fast"]) == 1 and d["fast"][0]["wpm"] == 280
    assert len(d["unclear"]) == 1
    rv = render(RV.build({"sound": {"present": {"value": True}, "speech_to_text": {"value": "t"},
                                    "dialogue": {"value": d, "confidence": 0.6, "basis": "estimated"}}, "shots": []}, None), "en")
    codes = {f["code"] for dd in rv["departments"] for f in dd["findings"]}
    assert {"snd.dialogue", "snd.dead_air", "snd.fast_delivery", "snd.mumbled"} <= codes
    for dd in rv["departments"]:
        for f in dd["findings"]:
            assert "{" not in (f["observation"] or ""), f


@pytest.fixture
def sound_clip(tmp_path):
    p = tmp_path / "talk.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=gray:s=320x180:d=6:r=24",
                    "-f", "lavfi", "-i", "sine=frequency=300:d=6", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", str(p)], check=True)
    return p


def test_no_engine_says_how_to_enable(monkeypatch, sound_clip):
    monkeypatch.setattr(speech, "_faster_whisper", lambda: None)
    monkeypatch.setattr(speech, "_local_server", lambda: None)
    r = speech.analyse(str(sound_clip), 6)
    assert r["speech_to_text"]["value"] == "not available" and "faster-whisper" in r["speech_to_text"]["note"]
    assert r["dialogue"]["value"] is None
    assert not [f for f in os.listdir(sound_clip.parent) if f.endswith(".wav")]


def test_faster_whisper_stub(monkeypatch, sound_clip):
    seen = {}

    class Model:
        def __init__(self, name, device, compute_type):
            seen["init"] = (name, device)

        def transcribe(self, wav, **kw):
            seen["wav_exists"] = os.path.isfile(wav)
            seg = types.SimpleNamespace(start=0.5, end=4.0, text=" Hello there, how are you today my friend?",
                                        avg_logprob=-0.2, no_speech_prob=0.01)
            return iter([seg]), types.SimpleNamespace(language="en", language_probability=0.99)
    mod = types.ModuleType("faster_whisper")
    mod.WhisperModel = Model
    monkeypatch.setitem(sys.modules, "faster_whisper", mod)
    monkeypatch.setattr(speech, "_models", {})
    r = speech.analyse(str(sound_clip), 6)
    d = r["dialogue"]["value"]
    assert r["speech_to_text"]["value"].startswith("faster-whisper") and seen["init"][1] == "auto" and seen["wav_exists"]
    assert d["language"] == "en" and d["segments"][0]["text"].startswith("Hello") and d["words"] == 8
    assert not [f for f in os.listdir(sound_clip.parent) if f.endswith(".wav")]      # temp audio removed


class FakeSTT(BaseHTTPRequestHandler):
    seen: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        FakeSTT.seen.append((self.path, self.headers["Content-Type"], body[:4000]))
        out = json.dumps({"language": "en", "text": "Hello there.", "segments": [
            {"start": 1.0, "end": 3.0, "text": " Hello there.", "avg_logprob": -0.1, "no_speech_prob": 0.02}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


def test_local_server_transcription(monkeypatch, sound_clip):
    srv = HTTPServer(("127.0.0.1", 0), FakeSTT)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        monkeypatch.setattr(speech, "_faster_whisper", lambda: None)
        monkeypatch.setattr(speech, "_local_server",
                            lambda: {"enabled": True, "url": f"http://127.0.0.1:{srv.server_address[1]}/v1"})
        r = speech.analyse(str(sound_clip), 6)
        path, ctype, body = FakeSTT.seen[-1]
        assert path == "/v1/audio/transcriptions" and ctype.startswith("multipart/form-data")
        assert b"verbose_json" in body and b"RIFF" in body
        d = r["dialogue"]["value"]
        assert r["speech_to_text"]["value"] == "local AI server" and d["segments"][0]["start_s"] == 1.0
    finally:
        srv.shutdown()


def test_only_loopback_servers_get_audio():
    assert speech._strict_loopback("http://127.0.0.1:8800/v1") and speech._strict_loopback("http://localhost:1234/v1")
    assert not speech._strict_loopback("http://192.168.1.20:8800/v1")
    assert not speech._strict_loopback("http://ollama:11434/v1")


# ---------------------------------------------------------------- long films

def test_detail_budget_short_unchanged_long_grows():
    assert engine.detail_target(50, 60, 900) == 50
    assert engine.detail_target(200, 120, 900) == 80                  # short clip: unchanged cap
    two_hours = engine.detail_target(1500, 7200, 900)
    assert two_hours > 600                                            # far more than the old 80
    assert engine.detail_target(1500, 7200, 120) < two_hours          # a smaller time budget gives fewer


def test_detail_plan_spreads_and_includes_longest():
    shots = [{"index": i, "start_s": float(i), "end_s": i + (30.0 if i == 77 else 1.0)} for i in range(500)]
    plan = engine.detail_plan(shots, 100)
    assert len(plan) == 100 == len(set(plan))
    assert 0 in plan and 499 in plan and 77 in plan[:4]              # the longest shot is processed early
    assert max(plan) - min(plan) == 499
