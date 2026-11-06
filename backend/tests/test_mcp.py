"""The MCP server speaks JSON-RPC over stdio and drives a real CineVision: review a video, list, refine."""
import json
import os
import subprocess
import sys
from pathlib import Path

from conftest import ORIGIN, PASSWORD, Client

BACKEND = Path(__file__).resolve().parents[1]
SAMPLE = BACKEND.parent / "samples" / "sample-push-in.mp4"


class MCP:
    def __init__(self, port, email):
        env = {**os.environ, "CINEVISION_URL": f"http://127.0.0.1:{port}", "CINEVISION_ORIGIN": ORIGIN,
               "CINEVISION_EMAIL": email, "CINEVISION_PASSWORD": PASSWORD}
        self.p = subprocess.Popen([sys.executable, str(BACKEND / "cinevision" / "mcp_server.py")], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, text=True, encoding="utf-8", env=env)
        self.n = 0

    def send(self, method, params=None, notify=False):
        msg = {"jsonrpc": "2.0", "method": method, **({"params": params} if params is not None else {})}
        if not notify:
            self.n += 1
            msg["id"] = self.n
        self.p.stdin.write(json.dumps(msg) + "\n")
        self.p.stdin.flush()
        return None if notify else json.loads(self.p.stdout.readline())

    def tool(self, name, **args):
        r = self.send("tools/call", {"name": name, "arguments": args})["result"]
        return r, (json.loads(r["content"][0]["text"]) if not r.get("isError") else r["content"][0]["text"])

    def close(self):
        self.p.stdin.close()
        self.p.wait(10)


def test_mcp_review_list_refine(server):
    c = Client(server["port"])
    assert c.post("/api/auth/register", {"email": "agent@example.test", "password": PASSWORD}).status == 201
    m = MCP(server["port"], "agent@example.test")
    try:
        init = m.send("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test"}})
        assert init["result"]["serverInfo"]["name"] == "cinevision"
        m.send("notifications/initialized", notify=True)
        names = {t["name"] for t in m.send("tools/list")["result"]["tools"]}
        assert {"review_video", "get_review", "list_projects", "list_concepts", "refine_concept", "assemble_movie"} <= names
        r, rv = m.tool("review_video", path=str(SAMPLE), language="es")
        assert not r.get("isError"), rv
        assert rv["language"] == "es" and rv["verdict"] and len(rv["departments"]) == 8 and rv["ways_to_edit"]
        assert any(f["observation"] for d in rv["departments"] for f in d["findings"])
        _, projects = m.tool("list_projects")
        assert projects[0]["id"] == rv["project_id"]
        _, concepts = m.tool("list_concepts", project_id=rv["project_id"])
        assert len(concepts) >= 3 and concepts[0]["shots"]
        _, ref = m.tool("refine_concept", concept_id=concepts[0]["concept_id"], request="make the lighting more dramatic")
        assert ref["applied"] and ref["new_concept_id"]
        # only video files, only paths that exist
        r, msg = m.tool("review_video", path=str(BACKEND / "requirements.txt"))
        assert r["isError"] and "video" in msg
        r, msg = m.tool("review_video", path="/nope/missing.mp4")
        assert r["isError"] and "No file" in msg
        assert "error" in m.send("does/not/exist")
    finally:
        m.close()


def test_mcp_reorder_movie_and_export_timeline(server, tmp_path):
    """assemble_movie → reorder_movie (own order + continuity notes) → export_timeline (EDL text)."""
    import shutil
    c = Client(server["port"])
    assert c.post("/api/auth/register", {"email": "cutter@example.test", "password": PASSWORD}).status == 201
    a, b = tmp_path / "A001_pushin.mp4", tmp_path / "A002_twoshots.mp4"
    shutil.copy(SAMPLE, a)
    shutil.copy(BACKEND.parent / "samples" / "sample-two-shots.mp4", b)
    m = MCP(server["port"], "cutter@example.test")
    try:
        m.send("initialize", {"protocolVersion": "2025-06-18"})
        names = {t["name"] for t in m.send("tools/list")["result"]["tools"]}
        assert {"export_timeline", "reorder_movie"} <= names
        r, mv = m.tool("assemble_movie", paths=[str(a), str(b)], title="Two clips")
        assert not r.get("isError"), mv
        aid = mv["assembly_id"]
        r, edl = m.tool("export_timeline", assembly_id=aid)
        assert not r.get("isError"), edl
        assert edl["edl"].startswith("TITLE: Two clips") and "* SOURCE FILE: A001_pushin.mp4" in edl["edl"]
        r, msg = m.tool("export_timeline", assembly_id=aid, order="mine")
        assert r["isError"] and "own order" in msg
        r, mine = m.tool("reorder_movie", assembly_id=aid, order=["A002_twoshots.mp4", "A001_pushin.mp4", "A002_twoshots.mp4"])
        assert not r.get("isError"), mine
        assert [o["clip"] for o in mine["order"]] == ["A002_twoshots.mp4", "A001_pushin.mp4", "A002_twoshots.mp4"]
        assert any("twice" in n["problem"] and n["fix"] for n in mine["notes"])
        r, edl = m.tool("export_timeline", assembly_id=aid, order="mine")
        body = edl["edl"]
        assert body.index("A002_twoshots.mp4") < body.index("A001_pushin.mp4") and body.count("* SOURCE FILE:") == 3
        r, msg = m.tool("reorder_movie", assembly_id=aid, order=["nope.mp4"])
        assert r["isError"] and "nope.mp4" in msg
    finally:
        m.close()


def test_mcp_wrong_password_is_a_clear_error(server):
    m = MCP(server["port"], "nobody@example.test")
    try:
        m.send("initialize", {"protocolVersion": "2025-06-18"})
        r, msg = m.tool("list_projects")
        assert r["isError"] and msg
    finally:
        m.close()
