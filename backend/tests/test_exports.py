"""Phase 6: exports are explicit downloads with safe content, and private to the owner."""
import csv
import io
import json
import uuid

import pytest
from conftest import Client, PASSWORD, wait_jobs

from cinevision import exports


@pytest.fixture(scope="module")
def shotlist(server, clips):
    c = Client(server["port"])
    c.post("/api/auth/register", {"email": f"e-{uuid.uuid4().hex[:6]}@example.test", "password": PASSWORD})
    pid = c.post("/api/projects", {"title": "Export test"}).json["id"]
    c.upload(pid, clips["cut"])
    wait_jobs(c, pid)
    c.post(f"/api/projects/{pid}/concepts/generate", {"controls": {"concept_count": 2}})
    wait_jobs(c, pid)
    cid = c.get(f"/api/projects/{pid}/concepts").json["concepts"][0]["id"]
    # a note that tries spreadsheet formula injection
    c.post(f"/api/concepts/{cid}/refine", {"patches": [{"shot": 0, "field": "notes", "value": "=HYPERLINK(\"http://evil\")"}]})
    cid = c.get(f"/api/projects/{pid}/concepts").json["concepts"][0]["id"]
    sid = c.post(f"/api/concepts/{cid}/shotlist").json["id"]
    return c, pid, sid


def test_csv_export_is_safe(shotlist):
    c, _, sid = shotlist
    r = c.post(f"/api/shotlists/{sid}/export", {"format": "csv"})
    assert r.status == 200 and "attachment" in r.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(r.body.decode("utf-8-sig"))))
    assert rows[0][0] == "Shot" and len(rows) == 3
    notes = [row[-1] for row in rows[1:]]
    assert any(n.startswith("'=HYPERLINK") for n in notes)          # formula neutralised
    assert not any(n.startswith("=") for row in rows[1:] for n in row)


def test_pdf_and_json_exports(shotlist):
    c, pid, sid = shotlist
    pdf = c.post(f"/api/shotlists/{sid}/export", {"format": "pdf"})
    assert pdf.status == 200 and pdf.body[:5] == b"%PDF-" and pdf.headers["content-type"] == "application/pdf"
    js = json.loads(c.post(f"/api/shotlists/{sid}/export", {"format": "json"}).body)
    assert js["format"] == "cinevision.shotlist/1" and len(js["shot_list"]["rows"]) == 2
    proj = json.loads(c.post(f"/api/projects/{pid}/export").body)
    assert proj["format"] == "cinevision.project/1" and proj["media_included"] is False
    assert proj["analysis"]["shots"] and proj["concepts"]
    blob = json.dumps(proj)
    assert "/api/media/" not in blob and "wrapped_dek" not in blob
    assert c.post(f"/api/shotlists/{sid}/export", {"format": "exe"}).status == 422
    events = [e["event"] for e in c.get("/api/audit").json["events"]]
    assert "export.shotlist" in events and "export.project" in events


def test_exports_are_private(shotlist, server):
    _, pid, sid = shotlist
    o = Client(server["port"])
    o.post("/api/auth/register", {"email": f"o-{uuid.uuid4().hex[:6]}@example.test", "password": PASSWORD})
    assert o.post(f"/api/shotlists/{sid}/export", {"format": "csv"}).status == 404
    assert o.post(f"/api/projects/{pid}/export").status == 404


def test_safe_filename():
    assert exports.safe_filename("../../etc/passwd", "csv") == "etc-passwd.csv"
    assert exports.safe_filename("Sc. 14 — Kitchen", "pdf") == "Sc.-14-Kitchen.pdf"
