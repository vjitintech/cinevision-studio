"""The Director's review: an automatic, department-by-department critique of the footage that needs no input from
the user. Built-in rules read the measured analysis; findings are stored as codes + parameters so they can be shown
in any supported language. If a local AI model is configured, its notes are added as a separate, labelled layer.
"""
from __future__ import annotations

from collections import Counter

ORDER = ["story", "camera", "composition", "lighting", "colour", "sound", "editing", "titles"]
RANK = {"good": 0, "unknown": 0, "improve": 1, "problem": 2}


def _v(d, default=None):
    return d.get("value", default) if isinstance(d, dict) else default


def _c(d) -> float:
    return float(d.get("confidence", 0)) if isinstance(d, dict) else 0.0


def _f(dept, code, status, basis="estimated", confidence=0.5, **params):
    return {"department": dept, "code": code, "status": status, "basis": basis, "confidence": round(confidence, 2),
            "params": params}


def _shots_list(idx: list[int]) -> str:
    return ", ".join(str(i + 1) for i in idx[:8]) + ("…" if len(idx) > 8 else "")


def build(analysis: dict, assessment: dict | None) -> dict:
    F: list[dict] = []
    shots = analysis.get("shots", [])
    detailed = [s for s in shots if s.get("detailed", True)]
    narr = analysis.get("narrative", {})
    pacing = _v(narr.get("pacing"), {}) or {}
    moods = _v(narr.get("mood"), []) or []
    calm = not any(w in " ".join(moods) for w in ("urgent", "tense", "immediate"))

    # story
    if moods or pacing:
        F.append(_f("story", "story.reading", "good", "heuristic", 0.35,
                    mood=" / ".join(moods) or "neutral", pacing=pacing.get("label", "unknown")))

    # camera
    moves = [(_v(s["movement"].get("movement"), "") or "", s["index"]) for s in shots if s.get("movement")]
    known = [(m, i) for m, i in moves if m and "undetermined" not in m]
    if len(known) >= 1 and all("static" in m for m, _ in known):
        F.append(_f("camera", "cam.all_static", "improve", confidence=0.6))
    elif known:
        names = Counter(m.split(",")[0].split(" (")[0] for m, _ in known if "static" not in m)
        F.append(_f("camera", "cam.moves", "good", confidence=0.55,
                    moves=", ".join(f"{k} ×{v}" if v > 1 else k for k, v in names.most_common(5))))
    hh = [s["index"] for s in shots if _v(s.get("movement", {}).get("handheld"))]
    if hh:
        F.append(_f("camera", "cam.handheld_calm" if calm else "cam.handheld_ok", "improve" if calm else "good",
                    confidence=0.5, shots=_shots_list(hh)))
    real_sizes = [_v(s["shot_size"]) for s in detailed if s.get("shot_size") and _v(s["shot_size"])
                  and s["shot_size"].get("basis") != "heuristic" and _c(s["shot_size"]) > 0]
    if len(shots) == 1:
        F.append(_f("camera", "cam.single_shot", "improve", "observed", 0.8))
    elif len(set(real_sizes)) == 1 and len(real_sizes) >= 2:
        F.append(_f("camera", "cam.one_size", "improve", confidence=0.5, size=real_sizes[0]))
    elif len(set(real_sizes)) >= 2:
        F.append(_f("camera", "cam.sizes", "good", confidence=0.5, sizes=", ".join(dict.fromkeys(real_sizes))))
    angles = [_v(s.get("camera_angle")) for s in detailed if _c(s.get("camera_angle")) >= 0.3]
    if angles and all(a == "eye level" for a in angles):
        F.append(_f("camera", "cam.eye_level", "improve", confidence=0.4))
    for s in detailed:
        roll = _v(s.get("composition", {}).get("horizon_roll_deg"))
        if roll is not None and 2.0 < abs(roll) < 8:
            F.append(_f("camera", "cam.horizon", "problem", confidence=0.5, shot=s["index"] + 1, deg=round(abs(roll))))

    # composition (from detailed shots)
    for s in detailed[:1] if len(detailed) == 1 else detailed[:12]:
        comp = s.get("composition", {})
        if _c(comp.get("placement")) == 0:
            continue
        pl = _v(comp.get("placement"), "") or ""
        sym = _v(comp.get("symmetry"), 0) or 0
        if "centre" in pl and "off" not in pl and sym < 0.5:
            F.append(_f("composition", "comp.centred", "improve", confidence=0.45))
            break
    else:
        pl = next((_v(s["composition"].get("placement")) for s in detailed
                   if _c(s.get("composition", {}).get("placement")) > 0), None)
        if pl:
            F.append(_f("composition", "comp.thirds", "good", confidence=0.45, placement=pl))
    ns = [(_v(s["composition"].get("negative_space")) or 0) for s in detailed if _c(s.get("composition", {}).get("negative_space")) > 0]
    if ns and sum(ns) / len(ns) > 0.62:
        F.append(_f("composition", "comp.space", "good", confidence=0.45, pct=round(100 * sum(ns) / len(ns))))
    dofs = [_v(s["composition"].get("depth_of_field"), "") or "" for s in detailed if _c(s.get("composition", {}).get("depth_of_field")) > 0]
    if dofs:
        shallow = sum("shallow" in d for d in dofs) >= len(dofs) / 2
        F.append(_f("composition", "comp.shallow" if shallow else "comp.deep", "good" if shallow else "improve", confidence=0.35))

    # lighting
    col = analysis.get("color", {})
    if _c(col.get("mean_luminance")) > 0:
        mean = _v(col.get("mean_luminance"), 0.45)
        clip = _v(col.get("clipping"), {}) or {}
        key = _v(col.get("tonal_key"), "") or ""
        contrast = _v(col.get("contrast"), {}) or {}
        bad = False
        if mean < 0.14:
            F.append(_f("lighting", "light.dark", "problem", "observed", 0.85, pct=round(mean * 100))); bad = True
        elif mean > 0.72:
            F.append(_f("lighting", "light.bright", "problem", "observed", 0.85, pct=round(mean * 100))); bad = True
        if clip.get("highlights", 0) > 0.025:
            F.append(_f("lighting", "light.clipped", "problem", "observed", 0.85, pct=round(clip["highlights"] * 100, 1))); bad = True
        if clip.get("shadows", 0) > 0.15 and "low" not in key:
            F.append(_f("lighting", "light.crushed", "improve", "observed", 0.8, pct=round(clip["shadows"] * 100))); bad = True
        if (contrast.get("range_p5_p95") or 1) < 0.45:
            F.append(_f("lighting", "light.flat", "improve", "observed", 0.7)); bad = True
        if not bad:
            F.append(_f("lighting", "light.good", "good", "observed", 0.8, key=key or "mid-key",
                        contrast=contrast.get("label", "normal")))
    if (_v(analysis.get("environment", {}).get("practical_lights"), []) or []):
        F.append(_f("lighting", "light.practicals", "good", "estimated", 0.4))

    # colour
    if _c(col.get("color_temperature")) > 0:
        cast = (_v(col.get("color_temperature"), {}) or {}).get("cast", "neutral")
        if cast and cast != "neutral":
            F.append(_f("colour", "col.cast", "improve", "observed", 0.6, cast=cast))
        else:
            F.append(_f("colour", "col.neutral", "improve", "observed", 0.6))
    sat = (_v(col.get("saturation"), {}) or {}).get("mean")
    if sat is not None and _c(col.get("saturation")) > 0:
        if sat < 0.12:
            F.append(_f("colour", "col.flat", "improve", "observed", 0.7, pct=round(sat * 100)))
        elif sat > 0.6:
            F.append(_f("colour", "col.vivid", "improve", "observed", 0.7, pct=round(sat * 100)))
    pal = _v(col.get("palette"), []) or []
    if pal:
        F.append(_f("colour", "col.palette", "good", "observed", 0.9,
                    colours=", ".join(p.get("name") or p.get("hex", "") for p in pal[:4] if isinstance(p, dict))))

    # sound
    snd = analysis.get("sound") or {}
    present = _v(snd.get("present"))
    if present is False:
        F.append(_f("sound", "snd.none", "improve", "observed", 0.95))
    elif present:
        lo = _v(snd.get("loudness"), {}) or {}
        lufs, peak = lo.get("integrated_lufs"), lo.get("true_peak_dbfs")
        ok = True
        if lufs is not None and lufs < -30:
            F.append(_f("sound", "snd.quiet", "improve", "observed", 0.9, lufs=round(lufs, 1))); ok = False
        elif lufs is not None and lufs > -11:
            F.append(_f("sound", "snd.loud", "improve", "observed", 0.9, lufs=round(lufs, 1))); ok = False
        if peak is not None and peak > -1.0:
            F.append(_f("sound", "snd.peaks", "problem", "observed", 0.9, peak=round(peak, 1))); ok = False
        clipped = _v(snd.get("clipping"), 0) or 0
        if clipped > 10:
            F.append(_f("sound", "snd.clipping", "problem", "observed", 0.85, n=clipped)); ok = False
        nf = _v(snd.get("noise_floor_db"))
        content = _v(snd.get("content"), {}) or {}
        if nf is not None and nf > -50 and "music" not in content.get("label", ""):
            F.append(_f("sound", "snd.noise", "improve", "observed", 0.5, db=round(nf))); ok = False
        if ok and lufs is not None:
            F.append(_f("sound", "snd.good", "good", "observed", 0.85, lufs=round(lufs, 1),
                        peak=round(peak, 1) if peak is not None else "?"))
        if content:
            F.append(_f("sound", "snd.content", "good", "heuristic", 0.4, label=content.get("label", "")))
        gaps = _v(snd.get("silences"), []) or []
        dur = _v(analysis.get("clip", {}).get("duration_s"), 0) or 0
        inner = [g for g in gaps if g["start_s"] > 0.5 and g["end_s"] < dur - 0.5]
        if inner:
            F.append(_f("sound", "snd.gaps", "improve", "observed", 0.6, n=len(inner),
                        secs=round(sum(g["end_s"] - g["start_s"] for g in inner), 1)))

    # editing
    n = len(shots)
    asl = pacing.get("average_shot_s") or (_v(analysis.get("clip", {}).get("duration_s"), 0) / max(1, n))
    tempo = "fast" if asl < 2 else "measured" if asl < 6 else "slow"
    F.append(_f("editing", "edit.pace", "good", "observed", 0.7, n=n, asl=round(asl, 1), pacing=tempo, advice_key=tempo))
    flashes = [s for s in shots if s["end_s"] - s["start_s"] < 0.5]
    if flashes and n > 1:
        F.append(_f("editing", "edit.flashy", "improve", "observed", 0.65, n=len(flashes)))

    # titles
    txt = _v(analysis.get("on_screen_text"), []) or []
    if txt:
        for seg in txt:
            if seg["kind"] == "opening titles":
                F.append(_f("titles", "titles.opening", "good", "estimated", 0.45, secs=seg["on_screen_s"]))
                if seg["on_screen_s"] < 2.0:
                    F.append(_f("titles", "titles.fast", "improve", "estimated", 0.45, secs=seg["on_screen_s"]))
            elif seg["kind"] == "end credits":
                F.append(_f("titles", "titles.credits", "good", "estimated", 0.45, secs=seg["on_screen_s"]))
        caps = [s for s in txt if s["kind"].startswith("caption")]
        if caps:
            F.append(_f("titles", "titles.captions", "good", "estimated", 0.4, n=len(caps)))
    elif "on_screen_text" in analysis:
        F.append(_f("titles", "titles.none", "good", "estimated", 0.35))

    departments = []
    for d in ORDER:
        items = [f for f in F if f["department"] == d]
        status = "unknown" if not items else max((f["status"] for f in items), key=lambda s: RANK[s])
        departments.append({"id": d, "status": status, "findings": items})

    verdict = (assessment or {}).get("verdict") or ("good" if not any(d["status"] == "problem" for d in departments)
                                                    and sum(d["status"] == "improve" for d in departments) <= 1
                                                    else "alternatives")
    alternatives = [{"concept_id": m["concept_id"], "name": m["name"], "match": m["score"]}
                    for m in (assessment or {}).get("matches", [])]
    return {"version": 1, "verdict": verdict, "departments": departments, "edit_options": edit_options(analysis),
            "alternatives": alternatives,
            "scores": {d["id"]: (100 if d["status"] == "good" else 70 if d["status"] == "improve" else 40
                                 if d["status"] == "problem" else None) for d in departments}}


def edit_options(analysis: dict) -> list[dict]:
    shots = analysis.get("shots", [])
    n = len(shots)
    energy = []
    for s in shots:
        cv = _v(s.get("movement", {}).get("camera_vs_subject"), {}) or {}
        energy.append((cv.get("camera_motion", 0) + cv.get("subject_motion", 0), s["index"]))
    dynamic = [i for _, i in sorted(energy, reverse=True)]
    longest = max(shots, key=lambda s: s["end_s"] - s["start_s"])["index"] if shots else 0
    x = 50
    if shots:
        c = _v(shots[longest].get("composition", {}).get("subject_centroid"), {}) or {}
        x = round(100 * c.get("x", 0.5))
    dur = _v(analysis.get("clip", {}).get("duration_s"), 0) or 0
    opts = [
        {"id": "continuity", "steps": [("keep_order", {}), ("trim_heads", {"s": 0.3}), ("cut_on_action", {})]},
        {"id": "slowburn", "steps": [("keep_order", {}), ("hold_peak", {"shot": longest + 1}), ("jcut", {}), ("music_low", {})]},
        {"id": "montage", "steps": [("beat", {"bpm": 120, "beats": 4, "s": 2.0}),
                                    ("dynamic_first", {"shots": _shots_list(dynamic[:4])})]},
        {"id": "teaser", "steps": [("teaser_pick", {"shots": _shots_list(sorted(dynamic[:6]))}), ("teaser_build", {}),
                                   ("teaser_sound", {})]},
        {"id": "vertical", "steps": [("vertical_crop", {"x": x}), ("vertical_len", {}), ("captions", {})]},
    ]
    if n <= 1:
        opts = [o for o in opts if o["id"] in ("continuity", "slowburn", "teaser", "vertical")]
    if dur < 12:
        opts = [o for o in opts if o["id"] != "teaser"]
    return opts
