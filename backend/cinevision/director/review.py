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
    face_sized = [(s["index"], s["shot_size"]) for s in detailed
                  if isinstance(s.get("shot_size"), dict) and s["shot_size"].get("source") == "face" and _c(s["shot_size"]) > 0]
    if face_sized:
        abbrs = list(dict.fromkeys(z.get("abbr") or _v(z) for _, z in face_sized))
        F.append(_f("composition", "comp.face_sizes", "good", "estimated", 0.6, n=len(face_sized),
                    sizes=", ".join(abbrs)))
    dofs = [_v(s["composition"].get("depth_of_field"), "") or "" for s in detailed if _c(s.get("composition", {}).get("depth_of_field")) > 0]
    if dofs:
        shallow = sum("shallow" in d for d in dofs) >= len(dofs) / 2
        F.append(_f("composition", "comp.shallow" if shallow else "comp.deep", "good" if shallow else "improve", confidence=0.35))

    # lighting (not judged on title/credit cards: white text on black is meant to be "dark" and high-contrast)
    col = analysis.get("color", {})
    dur = float(_v(analysis.get("clip", {}).get("duration_s"), 0) or 0)
    cards_s = sum(t.get("on_screen_s", 0) for t in (_v(analysis.get("on_screen_text"), []) or []) if t.get("max_lines", 0) >= 2)
    is_text_card = bool(dur) and cards_s / dur >= 0.5
    if _c(col.get("mean_luminance")) > 0 and not is_text_card:
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
    if not is_text_card and (_v(analysis.get("environment", {}).get("practical_lights"), []) or []):
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
        F.extend(_dialogue_findings(snd))

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
        F.extend(_text_findings(analysis.get("on_screen_text") or {}, txt))
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


def _mmss(t: float) -> str:
    t = max(0, int(round(float(t or 0))))
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


def _dialogue_findings(snd: dict) -> list[dict]:
    """Findings from speech-to-text (when an engine was available)."""
    out = []
    stt = _v(snd.get("speech_to_text"))
    if stt in ("not available", "failed"):
        out.append(_f("sound", "snd.stt_missing", "unknown", "observed", 1.0))
        return out
    d = _v(snd.get("dialogue"))
    if not isinstance(d, dict) or not d.get("segments"):
        return out
    conf = _c(snd.get("dialogue"))
    wpm = d.get("words_per_minute")
    out.append(_f("sound", "snd.dialogue", "good", "estimated", conf, mins=round(d.get("speech_s", 0) / 60, 1),
                  lang=d.get("language") or "?", wpm=wpm if wpm is not None else "—"))
    if d.get("buried"):
        out.append(_f("sound", "snd.dialogue_buried", "problem", "estimated", conf * 0.8, n=len(d["buried"]),
                      at=d["buried"][0]["at"]))
    if d.get("unclear"):
        out.append(_f("sound", "snd.mumbled", "improve", "estimated", conf * 0.7, n=len(d["unclear"]), at=d["unclear"][0]["at"]))
    if d.get("dead_air"):
        out.append(_f("sound", "snd.dead_air", "improve", "estimated", conf * 0.8, n=len(d["dead_air"]),
                      secs=round(sum(x["end_s"] - x["start_s"] for x in d["dead_air"]), 1), at=d["dead_air"][0]["at"]))
    if d.get("fast"):
        out.append(_f("sound", "snd.fast_delivery", "improve", "estimated", conf * 0.7, n=len(d["fast"]),
                      wpm=max(x.get("wpm") or 0 for x in d["fast"]), at=d["fast"][0]["at"]))
    if d.get("slow"):
        out.append(_f("sound", "snd.slow_delivery", "improve", "estimated", conf * 0.6, n=len(d["slow"]),
                      wpm=min(x.get("wpm") or 0 for x in d["slow"]), at=d["slow"][0]["at"]))
    return out


def _text_findings(ost: dict, segs: list[dict]) -> list[dict]:
    """Readability of on-screen text: reading time (needs OCR), title-safe area and contrast."""
    out = []
    reading = ost.get("reading") if isinstance(ost, dict) else None
    if isinstance(reading, dict) and not reading.get("available"):
        out.append(_f("titles", "titles.ocr_missing", "unknown", "observed", 1.0))
    read = [s for s in segs if s.get("words")]
    if read:
        out.append(_f("titles", "titles.read", "good", "estimated", 0.5, n=len(read), words=sum(s["words"] for s in read)))
    for s in read:
        if s["words"] >= 2 and s["on_screen_s"] < s.get("needed_s", 0):
            out.append(_f("titles", "titles.too_short_to_read", "improve", "estimated", 0.5, kind=s["kind"],
                          at=_mmss(s["start_s"]), words=s["words"], secs=s["on_screen_s"], need=s["needed_s"]))
    unsafe = [s for s in segs if s.get("title_safe") is False]
    if unsafe:
        out.append(_f("titles", "titles.unsafe", "improve", "estimated", 0.45, n=len(unsafe), at=_mmss(unsafe[0]["start_s"])))
    low = [s for s in segs if isinstance(s.get("contrast_ratio"), (int, float)) and s["contrast_ratio"] < 3.0]
    if low:
        out.append(_f("titles", "titles.low_contrast", "improve", "estimated", 0.45, n=len(low),
                      at=_mmss(low[0]["start_s"]), ratio=min(s["contrast_ratio"] for s in low)))
    return out


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
