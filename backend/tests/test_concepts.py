"""Phase 3: concept generation, distinctness, refinements and revisions."""
import itertools

import pytest
from conftest import wait_jobs

REQUIRED_SHOT_FIELDS = ["size", "angle", "camera_height_m", "viewing_direction", "lens", "dof", "movement", "blocking",
                        "lighting", "transition_out", "camera_path", "purpose", "duration_s"]


@pytest.fixture(scope="module")
def project(server, clips):
    from conftest import Client, PASSWORD
    import uuid
    c = Client(server["port"])
    c.post("/api/auth/register", {"email": f"c-{uuid.uuid4().hex[:6]}@example.test", "password": PASSWORD})
    pid = c.post("/api/projects", {"title": "concepts"}).json["id"]
    c.upload(pid, clips["cut"])
    wait_jobs(c, pid)
    return c, pid


def _generate(c, pid, **controls):
    r = c.post(f"/api/projects/{pid}/concepts/generate", {"controls": controls} if controls else {})
    assert r.status == 202, r.body
    jobs = wait_jobs(c, pid)
    assert all(j["status"] == "completed" for j in jobs), jobs
    return c.get(f"/api/projects/{pid}/concepts").json["concepts"]


def test_generates_four_distinct_complete_concepts(project):
    c, pid = project
    concepts = _generate(c, pid, genre="thriller", desired_emotion="dread", concept_count=4)
    assert len(concepts) == 4
    names = [x["data"]["name"] for x in concepts]
    assert len(set(names)) == 4
    for con in concepts:
        d = con["data"]
        for key in ("objective", "intended_emotion", "rationale", "color", "editing", "production", "continuity_risks",
                    "differs_from_original", "scene"):
            assert d[key], key
        assert len(d["shots"]) == 2                                      # follows the 2-shot source clip
        for s in d["shots"]:
            for f in REQUIRED_SHOT_FIELDS:
                assert f in s, f
            roles = {x["role"] for x in s["lighting"]["sources"]}
            assert "key" in roles
            assert s["lens"]["focal_mm"] > 0 and s["lens"]["category"]
            assert len(s["camera_path"]["keyframes"]) >= 2
        assert con["engine"]["id"] == "rules-cinematography" and "settings" in con["engine"]
        assert "brief" not in con["engine"]["settings"]                 # free text not recorded in engine metadata
    # pairwise genuinely different set-ups, not renamed copies
    def signature(d):
        s = d["shots"][0]
        return (s["lens"]["ff_equiv_mm"] // 10, s["lighting"]["ratio"], s["movement"]["type"], s["size"], s["angle"])
    for a, b in itertools.combinations(concepts, 2):
        diff = sum(x != y for x, y in zip(signature(a["data"]), signature(b["data"])))
        assert diff >= 3, (a["data"]["name"], b["data"]["name"])
    # brief keywords steer ranking
    assert concepts[0]["data"]["archetype"] == "suspense"


def test_generation_is_idempotent_for_same_controls(project):
    c, pid = project
    c.post(f"/api/projects/{pid}/concepts/generate", {"controls": {"genre": "comedy", "concept_count": 3}})
    c.post(f"/api/projects/{pid}/concepts/generate", {"controls": {"genre": "comedy", "concept_count": 3}})
    jobs = wait_jobs(c, pid)
    assert len([j for j in jobs if j["kind"] == "concepts"]) == 2      # thriller batch + one comedy batch
    assert len(c.get(f"/api/projects/{pid}/concepts").json["concepts"]) == 3


def test_refinement_commands_create_revisions(project):
    c, pid = project
    con = c.get(f"/api/projects/{pid}/concepts").json["concepts"][0]
    before = con["data"]
    cmd = ("Make the lighting more dramatic. Move the camera lower. Create a slower dolly-in. "
           "Give the background more separation. Create three alternative endings. "
           "Make it achievable with a small production budget. Also teleport the actor to Mars.")
    r = c.post(f"/api/concepts/{con['id']}/refine", {"command": cmd})
    assert r.status == 201, r.body
    after = r.json["concept"]["data"]
    assert r.json["concept"]["revision"] == 2 and r.json["concept"]["parent_id"] == con["id"]
    assert r.json["unrecognized"] == ["Also teleport the actor to Mars"]
    s0, s1 = before["shots"][0], after["shots"][0]
    assert s1["lighting"]["ratio"] > s0["lighting"]["ratio"]
    assert s1["camera_height_m"] < s0["camera_height_m"]
    assert "push-in" in s1["movement"]["type"]
    assert any(x["role"] in ("rim", "backlight") for x in s1["lighting"]["sources"])
    assert len(after["alternative_endings"]) == 3
    assert after["budget"] == "micro"
    # original revision untouched and no longer current
    old = c.get(f"/api/concepts/{con['id']}").json
    assert old["data"] == before and not old["is_current"]
    hist = c.get(f"/api/concepts/{con['id']}/history").json["revisions"]
    assert [h["revision"] for h in hist] == [1, 2]
    # revert creates revision 3 equal to revision 1
    rv = c.post(f"/api/concepts/{con['id']}/revert")
    assert rv.status == 201 and rv.json["concept"]["revision"] == 3
    assert rv.json["concept"]["data"]["shots"][0]["lighting"]["ratio"] == s0["lighting"]["ratio"]


def test_keep_blocking_change_angle_and_shot_scope(project):
    c, pid = project
    con = c.get(f"/api/projects/{pid}/concepts").json["concepts"][1]
    d = con["data"]
    r = c.post(f"/api/concepts/{con['id']}/refine", {"command": "Keep the actor's blocking but change the camera angle"})
    n = r.json["concept"]["data"]
    assert n["shots"][0]["azimuth_deg"] != d["shots"][0]["azimuth_deg"]
    assert n["shots"][0]["blocking"] == d["shots"][0]["blocking"]
    r = c.post(f"/api/concepts/{r.json['concept']['id']}/refine", {"command": "shot 2 tighter"})
    m = r.json["concept"]["data"]
    assert m["shots"][0]["size"] == n["shots"][0]["size"] and m["shots"][1]["size"] != n["shots"][1]["size"]


def test_structured_patch_and_validation(project):
    c, pid = project
    con = c.get(f"/api/projects/{pid}/concepts").json["concepts"][2]
    r = c.post(f"/api/concepts/{con['id']}/refine", {"patches": [{"shot": 0, "field": "lens.ff_equiv_mm", "value": 85}]})
    s = r.json["concept"]["data"]["shots"][0]
    assert s["lens"]["ff_equiv_mm"] == 85 and s["lens"]["category"] == "short telephoto"
    bad = c.post(f"/api/concepts/{r.json['concept']['id']}/refine", {"patches": [{"shot": 0, "field": "__class__", "value": 1}]})
    assert bad.status == 422
    bad = c.post(f"/api/concepts/{r.json['concept']['id']}/refine", {"patches": [{"shot": 0, "field": "size", "value": "huge"}]})
    assert bad.status == 422


def test_planner_state_and_shotlist(project):
    c, pid = project
    con = c.get(f"/api/projects/{pid}/concepts").json["concepts"][0]
    cid = con["id"]
    cam = c.get(f"/api/concepts/{cid}/camera/0").json
    assert cam["derived"] and cam["camera_path"]["keyframes"]
    cam["camera_height_m"] = 0.4
    assert c.put(f"/api/concepts/{cid}/camera/0", cam).json["derived"] is False
    assert c.get(f"/api/concepts/{cid}/camera/0").json["camera_height_m"] == 0.4
    light = c.get(f"/api/concepts/{cid}/lighting/0").json
    light["sources"][0]["fixture"] = "Window, moved camera-left"
    c.put(f"/api/concepts/{cid}/lighting/0", light)
    sb = c.get(f"/api/concepts/{cid}/storyboard").json
    assert len(sb["panels"]) == 2
    sb["panels"][0]["note"] = "edited"
    sb["animatic"] = {"playback_rate": 0.5}
    assert c.put(f"/api/concepts/{cid}/storyboard", sb).status == 200
    assert c.get(f"/api/concepts/{cid}/storyboard").json["animatic"]["playback_rate"] == 0.5
    sc = c.get(f"/api/concepts/{cid}/scene3d").json
    assert sc["scene"]["subjects"]
    sl = c.post(f"/api/concepts/{cid}/shotlist").json["data"]
    assert len(sl["rows"]) == 2
    row = sl["rows"][0]
    for f in ("shot", "duration_s", "purpose", "size", "angle", "lens", "movement", "focus_dof", "blocking", "lighting",
              "equipment", "continuity", "transition"):
        assert row[f] not in (None, ""), f
    assert "Window, moved camera-left" in " ".join(row["lighting_detail"])
    assert c.get(f"/api/concepts/{cid}/camera/7").status == 404


def test_ai_preview_not_faked(project):
    c, pid = project
    cid = c.get(f"/api/projects/{pid}/concepts").json["concepts"][0]["id"]
    r = c.post(f"/api/concepts/{cid}/ai-preview")
    assert r.status == 409 and r.json["error"]["code"] == "not_configured"
    adapters = c.get("/api/adapters").json["adapters"]
    assert {a["id"]: a["status"] for a in adapters}["video-gen"] == "not_configured"


def test_concepts_isolated_between_users(project, server):
    from conftest import Client, PASSWORD
    import uuid
    c, pid = project
    cid = c.get(f"/api/projects/{pid}/concepts").json["concepts"][0]["id"]
    o = Client(server["port"])
    o.post("/api/auth/register", {"email": f"x-{uuid.uuid4().hex[:6]}@example.test", "password": PASSWORD})
    for path in (f"/api/concepts/{cid}", f"/api/concepts/{cid}/history", f"/api/concepts/{cid}/camera/0",
                 f"/api/concepts/{cid}/storyboard", f"/api/projects/{pid}/analysis", f"/api/projects/{pid}/concepts"):
        assert o.get(path).status == 404, path
    assert o.post(f"/api/concepts/{cid}/refine", {"command": "darker"}).status == 404
    assert o.put(f"/api/concepts/{cid}/camera/0", {"x": 1}).status == 404
    assert o.post(f"/api/concepts/{cid}/shotlist").status == 404
