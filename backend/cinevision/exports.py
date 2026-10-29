"""Shot-list and project exports (CSV, JSON, PDF). Generated in memory and returned to the requesting browser only."""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timezone

COLUMNS = [("shot", "Shot"), ("duration_s", "Duration (s)"), ("purpose", "Narrative purpose"), ("size", "Shot size"),
           ("angle", "Angle"), ("camera_position", "Camera position"), ("lens", "Lens"), ("movement", "Movement"),
           ("focus_dof", "Focus / depth of field"), ("blocking", "Blocking"), ("lighting", "Lighting"),
           ("lighting_detail", "Lighting detail"), ("equipment", "Equipment"), ("continuity", "Continuity"),
           ("transition", "Transition"), ("notes", "Notes")]


def _cell(v) -> str:
    if isinstance(v, list):
        v = "; ".join(str(x) for x in v)
    s = "" if v is None else str(v)
    # neutralise spreadsheet formula injection
    if s[:1] in ("=", "+", "-", "@", "\t", "\r"):
        s = "'" + s
    return s


def safe_filename(name: str, ext: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.")[:60] or "shot-list"
    return f"{base}.{ext}"


def shotlist_csv(data: dict) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
    w.writerow([label for _, label in COLUMNS])
    for r in data["rows"]:
        w.writerow([_cell(r.get(k)) for k, _ in COLUMNS])
    return ("﻿" + buf.getvalue()).encode("utf-8")  # BOM so spreadsheet apps detect UTF-8


def shotlist_json(data: dict, meta: dict) -> bytes:
    return json.dumps({"format": "cinevision.shotlist/1", "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                       **meta, "shot_list": data}, indent=2, ensure_ascii=False).encode()


def shotlist_pdf(data: dict, project_title: str) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=12 * mm, rightMargin=12 * mm, topMargin=12 * mm,
                            bottomMargin=12 * mm, title=f"{project_title} — shot list", author="CineVision Studio")
    ss = getSampleStyleSheet()
    cell = ParagraphStyle("cell", parent=ss["BodyText"], fontSize=7.2, leading=8.6)
    head = ParagraphStyle("head", parent=cell, fontName="Helvetica-Bold", textColor=colors.white)
    esc = lambda s: (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))  # noqa: E731
    story = [Paragraph(esc(f"{project_title} — {data['concept_name']}"), ss["Title"]),
             Paragraph(esc(f"Shot list · {data['aspect_ratio']} · {data['frame_rate']} fps · {data['total_duration_s']} s · "
                           f"revision {data.get('concept_revision', 1)}"), ss["Normal"]),
             Paragraph(esc(f"Grade: {data['grade']}"), ss["Normal"]), Spacer(1, 6)]
    cols = [("shot", 8), ("purpose", 26), ("size", 22), ("camera_position", 36), ("lens", 30), ("movement", 32),
            ("focus_dof", 34), ("blocking", 36), ("lighting", 30), ("lighting_detail", 44), ("transition", 18)]
    titles = {k: lbl for k, lbl in COLUMNS}
    rows = [[Paragraph(esc(titles[k]), head) for k, _ in cols]]
    for r in data["rows"]:
        line = []
        for k, _ in cols:
            v = r.get(k)
            if k == "size":
                v = f"{r['size']}<br/>{esc(r['angle'])}"
                line.append(Paragraph(v, cell))
                continue
            if k == "shot":
                v = f"{r['shot']}<br/>{r['duration_s']}s"
                line.append(Paragraph(v, cell))
                continue
            line.append(Paragraph(esc(_cell(v)).replace("; ", "<br/>") if k == "lighting_detail" else esc(_cell(v)), cell))
        rows.append(line)
    t = Table(rows, colWidths=[w * mm for _, w in cols], repeatRows=1)
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2b313a")),
                           ("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3efe7")]),
                           ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#b9b2a5"))]))
    story.append(t)
    if data.get("alternative_endings"):
        story += [Spacer(1, 8), Paragraph("Alternative endings", ss["Heading3"])]
        for e in data["alternative_endings"]:
            story.append(Paragraph(esc(f"• {e['name']} ({e['size']}, {e['movement']}): {e['note']}"), ss["Normal"]))
    p = data.get("production", {})
    if p:
        story += [Spacer(1, 8), Paragraph("Production", ss["Heading3"]),
                  Paragraph(esc(f"Complexity {p.get('complexity')}/5 ({p.get('complexity_label')}) · crew {p.get('crew_hint')} · "
                                f"support: {', '.join(p.get('camera_support', []))}"), ss["Normal"]),
                  Paragraph(esc("Lighting: " + ", ".join(p.get("lighting_equipment", []))), ss["Normal"])]
    story += [Spacer(1, 8), Paragraph("Generated by CineVision Studio from a proxy previsualisation. Positions, distances and "
                                      "lighting are planning estimates — verify on set.", ss["Italic"])]
    doc.build(story)
    return buf.getvalue()
