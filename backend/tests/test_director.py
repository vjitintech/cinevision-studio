"""Director's review: automatic after upload, covers sound and titles, optional local AI layer, languages."""
import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from conftest import PASSWORD, Client, start_server, wait_jobs


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    """Own server: the first account registered here is the owner who may change AI settings."""
    proc, info = start_server(tmp_path_factory.mktemp("ddata"))
    yield info
    proc.terminate()
    proc.wait(10)


@pytest.fixture(scope="module")
def film(tmp_path_factory):
    """4 s title card → 4 s textured shot → 3 s credits, with a sine tone as sound."""
    d = tmp_path_factory.mktemp("film")
    out = d / "film.mp4"
    card = ("drawtext=text='A FILM BY SOMEONE':fontcolor=white:fontsize=64:x=(w-tw)/2:y=300,"
            "drawtext=text='Directed by Name Surname':fontcolor=white:fontsize=36:x=(w-tw)/2:y=420,"
            "drawtext=text='Music by Another Person':fontcolor=white:fontsize=36:x=(w-tw)/2:y=480")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", "color=black:s=1280x720:d=4:r=24",
                    "-f", "lavfi", "-i", "testsrc2=s=1280x720:d=4:r=24",
                    "-f", "lavfi", "-i", "color=black:s=1280x720:d=3:r=24",
                    "-f", "lavfi", "-i", "sine=frequency=330:d=11",
                    "-filter_complex", f"[0:v]{card}[a];[2:v]{card}[c];[a][1:v][c]concat=n=3:v=1[v]",
                    "-map", "[v]", "-map", "3:a", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                    str(out)], check=True)
    return out


class FakeAI(BaseHTTPRequestHandler):
    seen: list = []

    def log_message(self, *a):
        pass

    def _send(self, obj):
        b = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        self._send({"data": [{"id": "fake-director"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeAI.seen.append(body)
        notes = {"overall": "Bonne prise.", "verdict": "improve", "story": "Une ouverture calme.",
                 "departments": {"sound": {"works": ["ton propre"], "improve": ["ajouter de l'ambiance"]}},
                 "alternative_ways": ["Plan-séquence au steadicam"], "edit_ideas": ["Montage au rythme"],
                 "learn": [{"term": "J-cut", "meaning": "Le son précède l'image."}]}
        self._send({"choices": [{"message": {"content": "<think>hmm</think>" + json.dumps(notes)}}]})


@pytest.fixture(scope="module")
def fake_ai():
    srv = HTTPServer(("127.0.0.1", 0), FakeAI)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/v1"
    srv.shutdown()


def _upload(user, clip, title="film"):
    pid = user.post("/api/projects", {"title": title}).json["id"]
    assert user.upload(pid, clip).status == 201
    jobs = wait_jobs(user, pid, timeout=240)
    assert all(j["status"] == "completed" for j in jobs), jobs
    return pid


def test_review_is_automatic_and_covers_sound_and_titles(server, film):
    c = Client(server["port"])
    assert c.post("/api/auth/register", {"email": "owner-dir@example.test", "password": PASSWORD}).status == 201
    pid = _upload(c, film)
    a = c.get(f"/api/projects/{pid}/analysis").json["data"]
    assert a["sound"]["present"]["value"] is True
    assert a["sound"]["loudness"]["value"]["integrated_lufs"] is not None
    kinds = [s["kind"] for s in a["on_screen_text"]["value"]]
    assert "opening titles" in kinds and "end credits" in kinds, kinds
    r = c.get(f"/api/projects/{pid}/review").json
    assert r["verdict"] in ("good", "good_with_fixes", "alternatives") and r["verdict_label"]
    deps = {d["id"]: d for d in r["departments"]}
    assert set(deps) == {"story", "camera", "composition", "lighting", "colour", "sound", "editing", "titles"}
    codes = {f["code"] for d in r["departments"] for f in d["findings"]}
    assert "titles.opening" in codes and "titles.credits" in codes
    assert any(code.startswith("snd.") for code in codes)
    for d in r["departments"]:
        for f in d["findings"]:
            assert f["observation"] and "{" not in f["observation"], f
    assert len(r["edit_options"]) >= 3 and all(o["steps"] for o in r["edit_options"])
    assert r["ai"] is None and r["ai_available"] is False


def test_language_preference_and_unknown_language_falls_back(user, film):
    pid = _upload(user, film, "lang")
    assert user.put("/api/me/preferences", {"language": "xx"}).status == 422
    assert user.put("/api/me/preferences", {"language": "te"}).json["language"] == "te"
    langs = user.get("/api/me/preferences").json["languages"]
    assert {"en", "hi", "te", "ta", "kn", "ml", "bn", "mr", "es", "fr", "de", "pt", "ar", "ja", "ko", "zh"} <= {x["code"] for x in langs}
    r = user.get(f"/api/projects/{pid}/review?lang=ar").json
    assert r["language"] == "ar" and r["rtl"] is True
    assert user.get(f"/api/projects/{pid}/review?lang=zz").json["language"] == "te"   # user's preference


def test_local_ai_settings_owner_only_and_notes_in_language(server, film, fake_ai):
    owner = Client(server["port"])
    assert owner.post("/api/auth/login", {"email": "owner-dir@example.test", "password": PASSWORD}).status == 200
    st = owner.get("/api/ai/local").json
    assert st["config"]["enabled"] is False and "colibri" in st["presets"]
    other = Client(server["port"])
    assert other.post("/api/auth/register", {"email": "second@example.test", "password": PASSWORD}).status == 201   # open sign-up
    assert other.put("/api/ai/local", {"url": fake_ai, "enabled": True}).status == 403
    r = owner.put("/api/ai/local", {"url": fake_ai, "enabled": True, "provider": "custom", "model": ""})
    assert r.status == 200 and r.json["reachable"] and r.json["models"] == ["fake-director"]
    try:
        owner.put("/api/me/preferences", {"language": "fr"})
        pid = _upload(owner, film, "ai")
        rv = owner.get(f"/api/projects/{pid}/review").json
        assert rv["language"] == "fr" and rv["ai"]["status"] == "used", rv["ai"]
        assert rv["ai"]["model"] == "fake-director" and rv["ai"]["overall"] == "Bonne prise."
        assert rv["ai"]["learn"][0]["term"] == "J-cut"
        sent = FakeAI.seen[-1]
        assert "French" in sent["messages"][0]["content"]
        parts = sent["messages"][1]["content"]
        assert any(p.get("type") == "image_url" and p["image_url"]["url"].startswith("data:image/jpeg;base64,") for p in parts)
        # asking again in another language adds notes for that language too
        job = owner.post(f"/api/projects/{pid}/review", {"lang": "hi"})
        assert job.status == 202
        wait_jobs(owner, pid)
        rv2 = owner.get(f"/api/projects/{pid}/review?lang=hi").json
        assert rv2["ai"]["status"] == "used" and set(rv2["ai_languages"]) >= {"fr", "hi"}
    finally:
        owner.put("/api/ai/local", {"url": fake_ai, "enabled": False})


def test_review_private_and_deleted_with_project(server, film):
    owner = Client(server["port"])
    owner.post("/api/auth/login", {"email": "owner-dir@example.test", "password": PASSWORD})
    pid = _upload(owner, film, "private")
    intruder = Client(server["port"])
    intruder.post("/api/auth/login", {"email": "second@example.test", "password": PASSWORD})
    assert intruder.get(f"/api/projects/{pid}/review").status == 404
    assert owner.delete(f"/api/projects/{pid}").status == 204
    assert owner.get(f"/api/projects/{pid}/review").status == 404


def test_learn_directing_uses_your_reviews(user, film):
    empty = user.get("/api/learn").json
    assert len(empty["chapters"]) == 8 and empty["focus"] == [] and empty["reviews"] == 0
    _upload(user, film, "learn")
    d = user.get("/api/learn?lang=ta").json
    assert d["language"] == "ta" and d["reviews"] == 1
    for ch in d["chapters"]:
        assert ch["exercise"] and ch["lessons"]
        for l in ch["lessons"]:
            assert l["why"] and "{" not in l["why"] and "{" not in (l["symptom"] or "")
    assert d["focus"], "the film has things to improve (e.g. title card timing, sound), so there is something to practise"
