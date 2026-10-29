import uuid

from conftest import Client, PASSWORD


def _project(c, title="Scene 12"):
    r = c.post("/api/projects", {"title": title})
    assert r.status == 201, r.body
    return r.json["id"]


def test_upload_valid_clip_is_encrypted_at_rest(user, clips, server):
    pid = _project(user)
    r = user.upload(pid, clips["static"])
    assert r.status == 201, r.body
    meta = r.json["metadata"]
    assert meta["video_codec"] == "h264" and meta["width"] == 640 and 2.5 < meta["duration_s"] < 3.5
    assert r.json["scan_status"] == "not_configured"
    # no plaintext video on disk: every stored file is in the encrypted container format
    raw_clip = clips["static"].read_bytes()
    for f in (server["data"] / "blobs").rglob("*.cve"):
        b = f.read_bytes()
        assert b[:4] == b"CVE1"
        assert raw_clip[1000:1064] not in b
    # project title not stored in clear
    db_bytes = (server["data"] / "cinevision.db").read_bytes() + (
        (server["data"] / "cinevision.db-wal").read_bytes() if (server["data"] / "cinevision.db-wal").exists() else b"")
    assert b"Scene 12" not in db_bytes
    # second upload to the same project is refused
    assert user.upload(pid, clips["static"]).status == 409


def test_upload_rejections(user, clips, tmp_path):
    pid = _project(user)
    fake = tmp_path / "evil.mp4"
    fake.write_bytes(b"<script>alert(1)</script>" * 100)
    r = user.upload(pid, fake)
    assert r.status == 422 and r.json["error"]["code"] == "unsupported_type"
    r = user.upload(pid, clips["static"], filename="clip.exe")
    assert r.status == 422 and r.json["error"]["code"] == "unsupported_type"
    r = user.upload(pid, clips["long"])
    assert r.status == 422 and r.json["error"]["code"] == "bad_duration"
    r = user.upload(pid, clips["tiny"])
    assert r.status == 422 and r.json["error"]["code"] == "bad_resolution"
    big = tmp_path / "big.mp4"
    big.write_bytes(clips["static"].read_bytes() + b"\0" * (41 * 1024 * 1024))
    r = user.upload(pid, big)
    assert r.status == 413
    # path traversal in filename is harmless (only stored, encrypted, for display)
    r = user.upload(pid, clips["static"], filename="../../../../etc/passwd.mp4")
    assert r.status == 201


def test_cross_user_access_is_404(user, server, clips):
    pid = _project(user, "Private")
    user.upload(pid, clips["static"])
    blob = user.get(f"/api/projects/{pid}").json["video"]["original_blob_id"]
    good = user.post(f"/api/projects/{pid}/media-token", {"blob_id": blob})
    assert good.status == 200
    url = good.json["url"]

    other = Client(server["port"])
    other.post("/api/auth/register", {"email": f"o-{uuid.uuid4().hex[:6]}@example.test", "password": PASSWORD})
    assert other.get(f"/api/projects/{pid}").status == 404
    assert other.patch(f"/api/projects/{pid}", {"title": "pwned"}).status == 404
    assert other.delete(f"/api/projects/{pid}").status == 404
    assert other.post(f"/api/projects/{pid}/media-token", {"blob_id": blob}).status == 404
    assert other.get(url).status == 404  # stolen token is bound to the owner's session
    assert other.get(f"/api/projects/{pid}/frames").status == 404
    assert other.upload(pid, clips["static"]).status == 404
    assert all(p["id"] != pid for p in other.get("/api/projects").json["projects"])
    # unauthenticated
    anon = Client(server["port"])
    assert anon.get(url).status == 401
    assert anon.get(f"/api/projects/{pid}").status == 401
    # malformed ids
    assert user.get("/api/projects/../../etc").status == 404
    assert user.get("/api/projects/not-a-uuid").status == 404


def test_media_streaming_with_ranges(user, clips):
    pid = _project(user)
    user.upload(pid, clips["static"])
    blob = user.get(f"/api/projects/{pid}").json["video"]["original_blob_id"]
    url = user.post(f"/api/projects/{pid}/media-token", {"blob_id": blob}).json["url"]
    original = clips["static"].read_bytes()
    full = user.get(url)
    assert full.status == 200 and full.body == original
    part = user.get(url, headers={"Range": "bytes=100-199"})
    assert part.status == 206 and part.body == original[100:200]
    assert part.headers["content-range"] == f"bytes 100-199/{len(original)}"
    tail = user.get(url, headers={"Range": "bytes=-50"})
    assert tail.body == original[-50:]
    assert user.get(url, headers={"Range": f"bytes={len(original) + 5}-"}).status == 416
    assert user.get(url.split("?")[0]).status == 404  # token required
    assert user.get(url[:-3] + "AAA").status == 404


def test_project_deletion_removes_everything(user, clips, server):
    pid = _project(user)
    user.upload(pid, clips["static"])
    files_before = set((server["data"] / "blobs").rglob("*.cve"))
    assert files_before
    blob = user.get(f"/api/projects/{pid}").json["video"]["original_blob_id"]
    assert user.delete(f"/api/projects/{pid}").status == 204
    assert user.get(f"/api/projects/{pid}").status == 404
    assert not list((server["data"] / "blobs").rglob(f"{blob}.cve"))
    events = user.get("/api/audit").json["events"]
    assert any(e["event"] == "project.delete" for e in events)
