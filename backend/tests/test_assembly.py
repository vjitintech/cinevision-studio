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
    # timeline export of the suggested cut: EDL and FCPXML downloads, private and not cached
    by_name = {cl["name"]: cl["clip_id"] for cl in a["clips"]}
    e = c.get(f"/api/assemblies/{aid}/export?format=edl&order=suggested")
    assert e.status == 200, e.body
    assert e.headers["Content-Disposition"].startswith("attachment;") and e.headers["Cache-Control"] == "no-store"
    edl = e.body.decode()
    assert edl.startswith("TITLE: Launch day") and edl.count("* SOURCE FILE:") == len(names)
    assert f"* SOURCE FILE: {names[0]}" in edl and "BL " in edl                # fade in from black
    x = c.get(f"/api/assemblies/{aid}/export?format=fcpxml&order=suggested")
    import xml.etree.ElementTree as ET
    root = ET.fromstring(x.body.decode().split("\n", 2)[2])
    assert [n.get("name") for n in root.findall(".//spine/asset-clip")] == names
    assert c.get(f"/api/assemblies/{aid}/export?format=edl&order=mine").status == 409       # no own order yet
    assert c.get(f"/api/assemblies/{aid}/export?format=avid&order=suggested").status == 400
    # the director's own order: credits moved up, the square phone clip used → plain-language notes with fixes
    mine_ids = [by_name[n] for n in ["A001_titles.mp4", "Z999_credits.mp4", "A003_astronaut.mp4", "B001_phone.mp4", "A002_rocket.mp4"]]
    rest = [cid for cid in by_name.values() if cid not in mine_ids]
    m = c.put(f"/api/assemblies/{aid}/order", {"clips": mine_ids, "excluded": rest, "lang": "en"})
    assert m.status == 200, m.body
    mine = m.json["mine"]
    codes = {n["code"] for n in mine["notes"]}
    assert {"chk_credits_not_last", "chk_misfit_used", "chk_aspect"} <= codes, codes
    assert all(n["text"] and n["fix_text"] and "{" not in n["text"] + n["fix_text"] for n in mine["notes"])
    assert mine["clips"] == mine_ids and sorted(mine["excluded"]) == sorted(rest) and not mine["unplaced"]
    assert c.get(f"/api/assemblies/{aid}").json["mine"]["clips"] == mine_ids                 # stored (encrypted)
    assert c.get(f"/api/assemblies/{aid}?lang=hi").json["mine"]["language"] == "hi"
    e = c.get(f"/api/assemblies/{aid}/export?format=edl&order=mine")
    assert e.status == 200 and e.body.decode().index("Z999_credits.mp4") < e.body.decode().index("A002_rocket.mp4")
    assert c.put(f"/api/assemblies/{aid}/order", {"clips": [mine_ids[0]], "excluded": [mine_ids[0]]}).status == 400
    assert c.put(f"/api/assemblies/{aid}/order", {"clips": ["00000000-0000-4000-8000-000000000000"]}).status == 400
    # privacy: another user can't see or touch it
    other = Client(server["port"])
    other.post("/api/auth/register", {"email": "nosy@example.test", "password": PASSWORD})
    assert other.get(f"/api/assemblies/{aid}").status == 404
    assert other.get(f"/api/assemblies/{aid}/export?format=edl&order=suggested").status == 404
    assert other.put(f"/api/assemblies/{aid}/order", {"clips": []}).status == 404
    assert other.post(f"/api/assemblies/{aid}/clips", {"filename": "x.mp4"}).status == 404
    assert c.delete(f"/api/assemblies/{aid}/order").status == 204
    assert c.get(f"/api/assemblies/{aid}").json["mine"] is None
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


# ---------------------------------------------------------------------------------------------------------------
# Fast engine tests on measured-analysis stand-ins (no video needed)

def _clip(cid, name, *, setting, conf, time, lum, people, size, energy, pal, dur=4.0, text=False, aspect=1.78, fps=24):
    a = {"clip": {"duration_s": {"value": dur}, "aspect": {"value": aspect}},
         "environment": {"setting": {"value": setting, "confidence": conf}, "time_of_day": {"value": time}},
         "color": {"mean_luminance": {"value": lum}, "saturation": {"value": {"mean": 0.3}},
                   "color_temperature": {"value": {"cast": "neutral"}}, "palette": {"value": [{"hex": h} for h in pal]}},
         "narrative": {"mood": {"value": []}, "pacing": {"value": {"motion_energy": energy}}},
         "subjects": {"max_visible": {"value": people}},
         "shots": [{"shot_size": {"value": size}, "composition": {"subject_centroid": {"value": {"x": 0.5}}}}],
         "on_screen_text": {"value": [{"on_screen_s": dur, "max_lines": 3}] if text else []}}
    return {"id": cid, "name": name, "analysis": a, "meta": {"fps": fps, "height": 720}, "issues": []}


def _story_clips():
    # file names deliberately in the opposite order of the story, so camera order can't produce the right answer
    return [
        _clip("titles", "A0_titles.mp4", setting="indoor", conf=0.3, time="night", lum=0.05, people=0, size="wide", energy=0.0,
              pal=["#000000", "#ffffff", "#101010"], dur=3, text=True),
        _clip("night", "C1_rocket.mp4", setting="outdoor", conf=0.8, time="night", lum=0.12, people=0, size="wide", energy=0.03,
              pal=["#101828", "#202838", "#05070a"]),
        _clip("room", "C2_room.mp4", setting="indoor", conf=0.7, time="day", lum=0.5, people=1, size="close-up", energy=0.02,
              pal=["#a08060", "#806040", "#c0a080"]),
        _clip("day", "C3_rocket.mp4", setting="outdoor", conf=0.8, time="day", lum=0.62, people=0, size="wide", energy=0.03,
              pal=["#80b0e0", "#d0d0c0", "#406080"]),
        _clip("credits", "Z9_credits.mp4", setting="indoor", conf=0.3, time="night", lum=0.05, people=0, size="wide", energy=0.0,
              pal=["#000000", "#fefefe", "#111111"], dur=5, text=True),
    ]


STORY = {
    "en": "EXT. LAUNCH PAD - DAY. The rocket stands in bright sunlight.\n"
          "INT. CREW ROOM - DAY. The astronaut sits alone, quiet, a close-up on her face.\n"
          "EXT. LAUNCH PAD - NIGHT. Night falls; the rocket waits in the dark.",
    "te": "బయట, పగలు. రాకెట్ ఎండలో నిలబడి ఉంది.\n"
          "లోపల, పగలు. వ్యోమగామి ఒంటరిగా నిశ్శబ్దంగా కూర్చుంది, ముఖం క్లోజప్.\n"
          "బయట, రాత్రి. చీకటిలో రాకెట్ వేచి ఉంది.",
    "hi": "बाहर, दिन। रॉकेट धूप में खड़ा है।\n"
          "अंदर, दिन। अंतरिक्ष यात्री अकेली, चुपचाप बैठी है, चेहरे का क्लोज़-अप।\n"
          "बाहर, रात। अंधेरे में रॉकेट इंतज़ार कर रहा है।",
    "ja": "屋外、昼。ロケットが日差しの中に立っている。\n屋内、昼。宇宙飛行士がひとりで静かに座っている、顔のアップ。\n屋外、夜。暗闇の中でロケットが待っている。",
    "zh": "室外，白天。火箭在阳光下矗立。\n室内，白天。宇航员独自安静地坐着，脸部特写。\n室外，夜晚。火箭在黑暗中等待。",
    "es": "EXTERIOR. DÍA. El cohete está al sol.\nINTERIOR. DÍA. La astronauta sentada, sola, en silencio; primer plano de su cara.\nEXTERIOR. NOCHE. El cohete espera en la oscuridad.",
}


def test_non_english_story_orders_like_english():
    """Telugu, Hindi, Japanese, Chinese and Spanish descriptions give the same cut as the English one — no AI."""
    from cinevision.assembly import engine as AE
    clips = _story_clips()
    want = ["titles", "day", "room", "night", "credits"]
    en = AE.assemble(clips, STORY["en"])
    assert [s["clip_id"] for s in en["sequence"]] == want
    assert [s["clip_id"] for s in AE.assemble(clips, None)["sequence"]] != want        # the story is what decides
    for lang in ("te", "hi", "ja", "zh", "es"):
        r = AE.assemble(clips, STORY[lang])
        assert [s["clip_id"] for s in r["sequence"]] == want, (lang, [b["cues"] for b in r["beats"]])
        assert [s["beat"] for s in r["sequence"]] == [s["beat"] for s in en["sequence"]], lang
        assert not any(n["code"] == "script_no_cues" for n in r["notes"]), lang


def test_lexicon_scripts_and_inflections():
    from cinevision.assembly import lexicon
    assert lexicon.match("రాత్రిపూట వీధిలో") >= {"night", "outdoor"}          # Telugu endings
    assert lexicon.match("夜明けの海") == {"dawn", "outdoor"}                    # 夜明け is dawn, not night
    assert lexicon.match("Nachts im Wald") == {"night", "outdoor"}
    assert lexicon.match("في الليل بالشارع") >= {"night", "outdoor"}             # Arabic article/prefix stripped
    assert lexicon.match("밤에 거리에서 혼자") >= {"night", "outdoor", "alone"}
    assert "night" not in lexicon.match("आता घरात")                               # short words need a whole-word match
    assert lexicon.match("") == set()


def test_check_my_order_explains_continuity_problems():
    from cinevision.assembly import engine as AE
    from cinevision.director import render as RD
    clips = _story_clips()
    clips.append(_clip("square", "B1_phone.mp4", setting="outdoor", conf=0.8, time="day", lum=0.6, people=0, size="wide",
                       energy=0.03, pal=["#70a0d0", "#c0c0b0", "#305070"], aspect=1.0))
    good = AE.check_order(clips, STORY["en"], ["titles", "day", "room", "night", "credits"], ["square"])
    assert [n["code"] for n in good["notes"]] == ["chk_ok"]
    bad = AE.check_order(clips, STORY["en"], ["day", "titles", "night", "room", "square", "room", "credits"], [])
    codes = {n["code"] for n in bad["notes"]}
    assert {"chk_titles_not_first", "chk_beat_order", "chk_repeat", "chk_aspect", "chk_misfit_used"} <= codes, codes
    assert all(n["fix"] and n["fix"]["code"].startswith("fix_") for n in bad["notes"])
    assert bad["notes"] == sorted(bad["notes"], key=lambda n: min(n["positions"]) if n["positions"] else 10 ** 6)
    out = RD.render_assembly_order(bad, "en")
    for n in out["notes"]:
        assert n["text"] and "{" not in n["text"] and n["fix_text"] and "{" not in n["fix_text"], n
    assert any(n.get("because_text") for n in out["notes"] if n["code"] == "chk_misfit_used")
    assert len(out["sequence"]) == 7 and out["sequence"][0]["transition"] == "fade_in"
    # credits not last, and day↔night jump inside one part of the story
    clips2 = _story_clips() + [_clip("day2", "C4_rocket_close.mp4", setting="outdoor", conf=0.8, time="day", lum=0.6, people=0,
                                     size="medium", energy=0.03, pal=["#7aa8d8", "#c8c8b8", "#3a5a7a"], dur=6)]
    r = AE.check_order(clips2, "EXT. LAUNCH PAD. The rocket stands on the launch pad, wide.", ["titles", "day", "credits", "night", "day2"], [])
    codes = [n["code"] for n in r["notes"]]
    assert "chk_credits_not_last" in codes and "chk_time_jump" in codes, codes
    # no description: a night shot between two day shots of the same place
    r = AE.check_order(clips2, None, ["day", "night", "day2"], [])
    assert "chk_sandwich" in [n["code"] for n in r["notes"]]


def test_timeline_export_edl_and_fcpxml_are_frame_accurate():
    import re
    import xml.etree.ElementTree as ET
    from fractions import Fraction
    from cinevision.assembly import timeline as TL
    entries = [
        {"name": "A001_titles.mp4", "fps": 23.976, "width": 1280, "height": 720, "duration_s": 3.0, "in_s": 0.25, "out_s": 2.75,
         "transition": "fade_in", "audio": False},
        {"name": "A002_rocket.mp4", "fps": 23.976, "width": 1280, "height": 720, "duration_s": 2.9, "in_s": 0.25, "out_s": 2.65,
         "transition": "dissolve", "audio": True},
        {"name": "A003 astronaut.mov", "fps": 23.976, "width": 1280, "height": 720, "duration_s": 5.0, "in_s": 0.25, "out_s": 4.75,
         "transition": "cut", "audio": True},
        {"name": "రాకెట్_రాత్రి.mp4", "fps": 25, "width": 1920, "height": 1080, "duration_s": 2.0, "in_s": 0.0, "out_s": 2.0,
         "transition": "match_cut", "audio": True, "transition_out": "fade_out"},
    ]
    tl = TL.build(entries, "Launch day")
    assert tl["rate"] == Fraction(24000, 1001) and tl["timebase"] == 24
    assert tl["frames"] == sum(c["length"] for c in tl["clips"])
    edl = TL.to_edl(tl)
    assert edl.startswith("TITLE: Launch day\nFCM: NON-DROP FRAME\n")
    rx = re.compile(r"^(\d{3})  (\S{1,8}) +(V|AA/V) +(C|D) +(\d{3})? *"
                    r"(\d\d:\d\d:\d\d:\d\d) (\d\d:\d\d:\d\d:\d\d) (\d\d:\d\d:\d\d:\d\d) (\d\d:\d\d:\d\d:\d\d)$")
    tb = 24
    fr = lambda t: ((int(t[0:2]) * 60 + int(t[3:5])) * 60 + int(t[6:8])) * tb + int(t[9:11])  # noqa: E731
    events = [rx.match(line).groups() for line in edl.splitlines() if line[:3].isdigit()]
    assert all(events)
    rec_end = fr("01:00:00:00")
    for ev, reel, chan, kind, dur, si, so, ri, ro in events:
        assert fr(so) - fr(si) == fr(ro) - fr(ri), (ev, si, so, ri, ro)       # source and record lengths agree
        assert fr(ri) == rec_end, (ev, ri)                                    # events butt together, no gaps
        rec_end = fr(ro)
        if kind == "D":
            assert dur and 1 <= int(dur) <= fr(ro) - fr(ri), (ev, dur)       # transition fits inside its event
        else:
            assert dur is None
    assert rec_end - fr("01:00:00:00") == tl["frames"]
    kinds = [(e[1], e[3]) for e in events]
    assert kinds[0] == ("BL", "C") and kinds[1] == ("A001_TIT", "D")           # fade in from black
    assert ("A001_TIT", "C") in kinds and ("A002_ROC", "D") in kinds           # dissolve: outgoing C + incoming D
    assert kinds[-1] == ("BL", "D")                                            # fade out to black
    assert "* FROM CLIP NAME: A003 astronaut.mov" in edl and "* SOURCE FILE: రాకెట్_రాత్రి.mp4" in edl
    xml = TL.to_fcpxml(tl)
    root = ET.fromstring(xml.split("\n", 2)[2])                                 # skip the XML declaration and DOCTYPE
    assert root.tag == "fcpxml" and root.get("version") == "1.9"
    fd = Fraction(1001, 24000)
    clips = root.findall(".//spine/asset-clip")
    assert len(clips) == 4 and len(root.findall("./resources/asset")) == 4
    sec = lambda v: Fraction(v[:-1])  # noqa: E731
    for c, want in zip(clips, tl["clips"]):
        for attr in ("offset", "start", "duration"):
            assert (sec(c.get(attr)) / fd).denominator == 1, (attr, c.get(attr))   # on a frame boundary
        assert sec(c.get("duration")) / fd == want["length"] and sec(c.get("offset")) / fd == want["rec_in"]
    seq = root.find(".//sequence")
    assert sec(seq.get("duration")) / fd == tl["frames"]
    assert len(root.findall(".//spine/transition")) == 3                       # fade in, dissolve, fade out
    for a in root.findall("./resources/asset"):
        assert a.find("media-rep").get("kind") == "original-media"


def test_assembly_takes_more_than_20_clips_an_hour(tmp_path_factory):
    """Real rate limits (multiplier 1): single projects keep 20 uploads an hour, but an assembly's clips don't
    share that budget, otherwise clip 21 of a 300-clip movie is refused (and the page then deletes it)."""
    from conftest import start_server, stop_server
    d = tmp_path_factory.mktemp("asm_rate")
    clip = d / "c.mp4"
    _ff("-f", "lavfi", "-i", "color=gray:s=320x180:d=1:r=24", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip))
    proc, info = start_server(d, {"CINEVISION_RATE_LIMIT_MULTIPLIER": "1", "CINEVISION_EMBEDDED_WORKER": "false"})
    try:
        c = Client(info["port"])
        assert c.post("/api/auth/register", {"email": "big@example.test", "password": PASSWORD}).status == 201
        aid = c.post("/api/assemblies", {"title": "Feature"}).json["id"]
        for i in range(25):
            cid = c.post(f"/api/assemblies/{aid}/clips", {"filename": f"S{i:03d}.mp4"}).json["clip_id"]
            r = c.upload(cid, clip, f"S{i:03d}.mp4")
            assert r.status == 201, (i, r.status, r.json)
        statuses = []
        for i in range(21):                                  # single projects: still 20 an hour
            pid = c.post("/api/projects", {"title": f"P{i}"}).json["id"]
            statuses.append(c.upload(pid, clip).status)
        assert statuses[:20] == [201] * 20 and statuses[20] == 429, statuses
    finally:
        stop_server(proc)
