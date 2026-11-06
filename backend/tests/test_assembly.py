"""Movie assembly: many clips + a scene description → story order, misfits flagged, private and deletable."""
import subprocess
from pathlib import Path

import pytest

from conftest import DRAWTEXT_FONT, PASSWORD, Client, wait_jobs

SAMPLES = Path(__file__).resolve().parents[2] / "samples"
CARD = ("drawtext=text='{t}':fontcolor=white:fontsize=64:x=(w-tw)/2:y=280,"
        "drawtext=text='{s}':fontcolor=white:fontsize=36:x=(w-tw)/2:y=400,"
        "drawtext=text='Music by Another Person':fontcolor=white:fontsize=36:x=(w-tw)/2:y=460").replace("drawtext=", "drawtext=" + DRAWTEXT_FONT)


def _ff(*args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    d = tmp_path_factory.mktemp("asm")
    src = SAMPLES / "sample-two-shots.mp4"
    out = {}
    out["titles"] = d / "A001_titles.mp4"
    _ff("-f", "lavfi", "-i", "color=black:s=1280x720:d=3:r=24", "-vf", CARD.format(t="LAUNCH DAY", s="A film by Someone"),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out["titles"]))
    out["astronaut"] = d / "A003_astronaut.mp4"
    _ff("-i", str(src), "-t", "2.9", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out["astronaut"]))
    out["astronaut_take2"] = d / "A004_astronaut_take2.mp4"
    _ff("-ss", "0.1", "-i", str(src), "-t", "2.8", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out["astronaut_take2"]))
    out["rocket_day"] = d / "A002_rocket.mp4"
    _ff("-ss", "3.1", "-i", str(src), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out["rocket_day"]))
    out["rocket_night"] = d / "A005_rocket_night.mp4"
    _ff("-ss", "3.1", "-i", str(src), "-vf", "eq=brightness=-0.35:saturation=0.4,colorbalance=bs=0.3",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out["rocket_night"]))
    out["square"] = d / "B001_phone.mp4"
    _ff("-i", str(src), "-t", "2.5", "-vf", "crop=720:720", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out["square"]))
    out["credits"] = d / "Z999_credits.mp4"
    _ff("-f", "lavfi", "-i", "color=black:s=1280x720:d=3:r=24", "-vf", CARD.format(t="THE END", s="Directed by Name Surname"),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out["credits"]))
    return out


SCRIPT = """Opening titles.
EXT. LAUNCH PAD - DAY. The rocket stands on the launch pad in bright sunlight.
INT. CREW ROOM - DAY. The astronaut sits alone, quiet, a close-up on her face.
EXT. LAUNCH PAD - NIGHT. Night falls; the rocket waits in the dark.
End credits."""


def test_assembly_orders_clips_and_flags_misfits(server, clips):
    c = Client(server["port"])
    assert c.post("/api/auth/register", {"email": "editor@example.test", "password": PASSWORD}).status == 201
    aid = c.post("/api/assemblies", {"title": "Launch day", "script": SCRIPT}).json["id"]
    upload_order = ["credits", "rocket_night", "astronaut", "square", "titles", "astronaut_take2", "rocket_day"]
    for key in upload_order:                                    # deliberately shuffled
        cid = c.post(f"/api/assemblies/{aid}/clips", {"filename": clips[key].name}).json["clip_id"]
        assert c.upload(cid, clips[key]).status == 201
    for _ in range(3):
        a = c.get(f"/api/assemblies/{aid}").json
        for cl in a["clips"]:
            wait_jobs(c, cl["clip_id"], timeout=240)
        jobs = wait_jobs(c, aid, timeout=240)
        if any(j["kind"] == "assemble" for j in jobs):
            break
    a = c.get(f"/api/assemblies/{aid}").json
    assert all(cl["status"] == "analysed" for cl in a["clips"]), a["clips"]
    assert all(cl["thumb"] for cl in a["clips"])
    r = a["result"]
    assert r is not None, a["jobs"]
    names = [s["name"] for s in r["sequence"]]
    rejected = {x["name"]: [f["code"] for f in x["flags"]] for x in r["rejected"]}
    print("ORDER", names, "REJECTED", rejected)
    for s in r["sequence"]:
        print("  ", s["name"], s["beat"], s["tags"], [x["code"] for x in s["reasons"]])
    assert names[0] == "A001_titles.mp4" and names[-1] == "Z999_credits.mp4"
    assert "look_aspect" in rejected.get("B001_phone.mp4", [])
    dup = [n for n, codes in rejected.items() if "duplicate" in codes]
    assert len(dup) == 1 and "astronaut" in dup[0]
    middle = names[1:-1]
    assert "A002_rocket.mp4" in middle and "A005_rocket_night.mp4" in middle
    assert middle == ["A002_rocket.mp4", "A003_astronaut.mp4", "A005_rocket_night.mp4"]     # the order the script describes
    assert len(r["beats"]) == 4          # screenplay: one beat per scene heading (+ the lines before the first)
    for s in r["sequence"]:
        assert s["reasons"] and all(x["text"] and "{" not in x["text"] for x in s["reasons"])
        assert s["transition_label"]
    assert r["sequence"][0]["transition"] == "fade_in"
    assert r["runtime_s"] > 5
    # the clips are not on the dashboard; the assembly is
    plist = c.get("/api/projects").json["projects"]
    assert [p["kind"] for p in plist] == ["assembly"] and plist[0]["clip_count"] == 7
    # translated
    hi = c.get(f"/api/assemblies/{aid}?lang=hi").json["result"]
    assert hi["language"] == "hi" and hi["sequence"][0]["reasons"][0]["text"] != r["sequence"][0]["reasons"][0]["text"]
    # privacy: another user can't see or touch it
    other = Client(server["port"])
    other.post("/api/auth/register", {"email": "nosy@example.test", "password": PASSWORD})
    assert other.get(f"/api/assemblies/{aid}").status == 404
    assert other.post(f"/api/assemblies/{aid}/clips", {"filename": "x.mp4"}).status == 404
    # deleting the assembly removes every clip project and its encrypted media
    clip_ids = [cl["clip_id"] for cl in a["clips"]]
    assert c.delete(f"/api/assemblies/{aid}").status == 204
    assert c.get(f"/api/assemblies/{aid}").status == 404
    assert all(c.get(f"/api/projects/{cid}").status == 404 for cid in clip_ids)


def test_assembly_without_script_groups_by_look(user, clips):
    aid = user.post("/api/assemblies", {"title": "No script"}).json["id"]
    for key in ["credits", "rocket_day", "titles", "astronaut"]:
        cid = user.post(f"/api/assemblies/{aid}/clips", {"filename": clips[key].name}).json["clip_id"]
        assert user.upload(cid, clips[key]).status == 201
    for cl in user.get(f"/api/assemblies/{aid}").json["clips"]:
        wait_jobs(user, cl["clip_id"], timeout=240)
    wait_jobs(user, aid, timeout=240)
    r = user.get(f"/api/assemblies/{aid}").json["result"]
    names = [s["name"] for s in r["sequence"]]
    assert names[0] == "A001_titles.mp4" and names[-1] == "Z999_credits.mp4" and len(names) == 4
    assert any(n["code"] == "no_script" for n in r["notes"])


def test_ai_editor_answer_is_validated(monkeypatch):
    """The local AI's order is checked: unknown numbers and repeats are dropped, missing clips are reported."""
    from cinevision.adapters import localai
    from cinevision.assembly import ai as AAI

    def clip(i, name):
        a = {"clip": {"duration_s": {"value": 3.0}}, "shots": [], "color": {}, "environment": {}, "narrative": {}, "subjects": {}}
        return {"id": f"c{i}", "name": name, "analysis": a, "meta": {}, "issues": []}
    clips = [clip(1, "a.mp4"), clip(2, "b.mp4"), clip(3, "c.mp4")]
    rules = {"sequence": [{"clip_id": "c1"}, {"clip_id": "c2"}, {"clip_id": "c3"}]}
    monkeypatch.setattr(localai, "chat_json", lambda *a, **k: (
        {"order": [3, 3, 9, 1], "unused": [{"clip": 2, "reason": "duplicate"}, {"clip": 7, "reason": "x"}],
         "notes": ["J-cut into 1"]}, "fake"))
    out = AAI.order("u", clips, rules, "story", "en")
    assert [s["clip_id"] for s in out["sequence"]] == ["c3", "c1"]
    assert [u["clip_id"] for u in out["unused"]] == ["c2"] and out["missing"] == []
    monkeypatch.setattr(localai, "chat_json", lambda *a, **k: ({"order": []}, "fake"))
    with pytest.raises(localai.LocalAIError):
        AAI.order("u", clips, rules, "", "en")
