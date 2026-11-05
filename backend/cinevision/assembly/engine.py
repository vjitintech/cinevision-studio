"""Movie assembly: put many analysed clips in story order and flag the ones that don't fit.

Inputs are the measured analyses of each clip (setting, time of day, light, mood, people, shot sizes, motion, sound,
titles/credits) plus an optional scene description or screenplay. The description is split into beats; each beat's
words are turned into cues (night, outside, crowd, chase, close-up…) and each clip is matched to the beat it fits best.
Without a description, clips are grouped into scenes by look and ordered by titles → story (camera file order) →
credits, wide → close within a scene.

Every placement and every flag carries a reason code (translated for display). Nothing here watches the story the
way a person does; the optional local AI layer adds that and is labelled separately.
"""
from __future__ import annotations

import math
import re

SIZE_RANK = {"extreme wide": 0, "wide or establishing": 0, "wide": 1, "full / medium-wide": 2,
             "medium / cowboy (body larger than frame)": 3, "medium": 3, "medium close-up": 4, "close-up": 5,
             "close / insert (large single subject)": 5, "extreme close-up": 6}
TIME_ORDER = {"dawn": 0, "day": 1, "golden hour / dusk": 2, "dusk": 2, "night": 3}

CUES = {
    "indoor": r"\b(int\.?|inside|indoors?|room|house|home|kitchen|office|bedroom|hall|corridor|car interior|apartment|shop|restaurant|classroom)\b",
    "outdoor": r"\b(ext\.?|outside|outdoors?|street|road|forest|jungle|beach|sea|field|sky|garden|park|mountain|river|village|city|rooftop|desert|launch ?pad)\b",
    "night": r"\b(night|midnight|dark(ness)?|moon(light)?|late)\b",
    "day": r"\b(day(time)?|morning|noon|afternoon|sun(ny|light)?|bright)\b",
    "dawn": r"\b(dawn|sunrise|first light)\b",
    "dusk": r"\b(dusk|sunset|evening|twilight|golden hour)\b",
    "fast": r"\b(chase|chasing|runs?|running|fight(s|ing)?|race|dance|dancing|explosion|action|rush(es|ing)?|hurr(y|ies)|escape|crash)\b",
    "still": r"\b(calm|quiet|still|sits?|sitting|waits?|waiting|silence|silent|slow(ly)?|alone with|stares?|looks? out)\b",
    "crowd": r"\b(crowd|party|people|everyone|together|family|friends|team|audience|meeting|wedding)\b",
    "alone": r"\b(alone|lonely|solitary|empty|nobody|deserted|by (him|her)self)\b",
    "person": r"\b(he|she|man|woman|girl|boy|hero|heroine|actor|character|astronaut|mother|father|child)\b",
    "close": r"\b(close[- ]?up|face|eyes|hands?|tears?|detail|lips|whisper)\b",
    "wide": r"\b(wide|landscape|skyline|vista|establishing|aerial|panorama|horizon)\b",
    "sad": r"\b(sad|grief|cry|cries|crying|mourn|loss|lonely|tears?)\b",
    "tense": r"\b(tense|fear|afraid|danger|suspense|threat|creep|scared|nervous)\b",
    "warm": r"\b(happy|joy|love|warm|smile|laugh|celebrat\w*|hug)\b",
    "titles": r"\b(title|titles|opening credits|main title)\b",
    "credits": r"\b(end credits|credits|the end|closing)\b",
}
_CUE_RX = {k: re.compile(v, re.I) for k, v in CUES.items()}
SLUG = re.compile(r"^\s*(\d+\.?\s*)?(INT\.|EXT\.|INT/EXT|I/E)", re.I)


def _v(d, default=None):
    return d.get("value", default) if isinstance(d, dict) else default


def split_beats(script: str | None) -> list[dict]:
    if not script or not script.strip():
        return []
    lines = [l.rstrip() for l in script.replace("\r", "").split("\n")]
    beats: list[str] = []
    if any(SLUG.match(l) for l in lines):            # screenplay: one beat per scene heading
        cur: list[str] = []
        for l in lines:
            if SLUG.match(l) and cur:
                beats.append(" ".join(cur))
                cur = []
            if l.strip():
                cur.append(l.strip())
        if cur:
            beats.append(" ".join(cur))
    else:                                              # prose: one beat per line, or per sentence for one paragraph
        parts = [l.strip() for l in lines if l.strip()]
        if len(parts) <= 2:
            parts = [s.strip() for s in re.split(r"(?<=[.!?।])\s+", " ".join(parts)) if s.strip()]
        beats = parts
    out = []
    for i, text in enumerate(beats[:80]):
        cues = sorted(k for k, rx in _CUE_RX.items() if rx.search(text))
        out.append({"index": i, "text": text[:400], "cues": cues})
    return out


def clip_features(c: dict) -> dict:
    """c: {id, name, analysis, meta}"""
    a = c["analysis"]
    env = a.get("environment", {})
    col = a.get("color", {})
    shots = a.get("shots", [])
    sizes = [_v(s.get("shot_size")) for s in shots if s.get("shot_size") and _v(s["shot_size"])]
    ranks = [SIZE_RANK.get(z, 3) for z in sizes]
    pacing = _v(a.get("narrative", {}).get("pacing"), {}) or {}
    moods = " ".join(_v(a.get("narrative", {}).get("mood"), []) or [])
    text = _v(a.get("on_screen_text"), []) or []
    pal = [p.get("hex") for p in (_v(col.get("palette"), []) or []) if isinstance(p, dict)]
    snd = a.get("sound") or {}
    tod = (_v(env.get("time_of_day"), "") or "").lower()
    dur = float(_v(a.get("clip", {}).get("duration_s"), 0) or 0)
    return {
        "id": c["id"], "name": c["name"], "duration": dur,
        "setting": (_v(env.get("setting"), "") or "").lower(),
        "setting_conf": float((env.get("setting") or {}).get("confidence", 0) if isinstance(env.get("setting"), dict) else 0),
        "time": next((k for k in ("dawn", "night", "dusk", "day") if k in tod), "unknown"),
        "lum": float(_v(col.get("mean_luminance"), 0.45) or 0.45),
        "sat": float((_v(col.get("saturation"), {}) or {}).get("mean", 0.3)),
        "cast": (_v(col.get("color_temperature"), {}) or {}).get("cast", "neutral"),
        "palette": pal,
        "moods": moods,
        "people": int(_v(a.get("subjects", {}).get("max_visible"), 0) or 0),
        "size_rank": (sum(ranks) / len(ranks)) if ranks else 3.0,
        "energy": float(pacing.get("motion_energy") or 0),
        # a title/credit *card* is mostly multi-line text for most of the clip (not a stray sign in a shot)
        "text_share": (sum(t.get("on_screen_s", 0) for t in text if t.get("max_lines", 0) >= 2) / dur) if dur else 0.0,
        "titles": False, "credits": False,
        "dialogue": "dialogue" in ((_v(snd.get("content"), {}) or {}).get("label", "")),
        "aspect": float(_v(a.get("clip", {}).get("aspect"), 1.78) or 1.78),
        "fps": float(c.get("meta", {}).get("fps") or _v(a.get("clip", {}).get("fps"), 0) or 0),
        "height": int(c.get("meta", {}).get("height") or 0),
        "subject_x": ((_v(shots[0]["composition"].get("subject_centroid"), {}) or {}).get("x", 0.5) if shots else 0.5),
        "issues": c.get("issues", []),
    }


def _cue_score(beat: dict, f: dict) -> tuple[float, list[str]]:
    """+1 for each cue the clip agrees with, −1 for each it contradicts; returns (score per cue, agreeing cues)."""
    cues = set(beat["cues"])
    if not cues:
        return 0.0, []
    agree, against = [], 0
    checks = {
        # "indoor" is the classifier's fallback when it sees no sky or foliage: only trust confident readings
        "indoor": f["setting"].startswith("indoor") and f.get("setting_conf", 1) >= 0.55,
        "outdoor": f["setting"].startswith("outdoor") and f.get("setting_conf", 1) >= 0.5,
        "night": f["time"] == "night" or f["lum"] < 0.22, "day": f["time"] == "day" or f["lum"] > 0.45,
        "dawn": f["time"] == "dawn", "dusk": f["time"] == "dusk",
        "fast": f["energy"] > 0.12, "still": f["energy"] < 0.05,
        "crowd": f["people"] >= 2, "alone": f["people"] <= 1, "person": f["people"] >= 1,
        "close": f["size_rank"] >= 4, "wide": f["size_rank"] <= 1.5,
        "sad": any(w in f["moods"] for w in ("melancholic", "isolated", "restrained")),
        "tense": "tense" in f["moods"], "warm": "warm" in f["moods"] or f["cast"] == "warm",
        "titles": f["titles"], "credits": f["credits"],
    }
    opposite = {"indoor": "outdoor", "outdoor": "indoor", "night": "day", "day": "night", "fast": "still", "still": "fast",
                "crowd": "alone", "alone": "crowd", "close": "wide", "wide": "close"}
    for cue in cues:
        if checks.get(cue):
            agree.append(cue)
        elif cue in opposite and checks.get(opposite[cue]):
            against += 1
    # "person" is weak evidence (the people detector misses many shots): don't penalise its absence
    return (len(agree) - against) / len(cues), agree


def _hex_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _look_distance(a: dict, b: dict) -> float:
    pa, pb = a["palette"][:3], b["palette"][:3]
    if not pa or not pb:
        return 1.0
    d = sum(min(math.dist(_hex_rgb(x), _hex_rgb(y)) for y in pb) for x in pa) / len(pa) / 441.7
    return d + abs(a["lum"] - b["lum"]) + 0.5 * abs(a["sat"] - b["sat"])


def _natkey(name: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name or "")]


def _scenes(feats: list[dict]) -> list[list[dict]]:
    """Greedy grouping into scenes by setting, time of day and look, keeping camera file order."""
    groups: list[list[dict]] = []
    for f in sorted(feats, key=lambda x: _natkey(x["name"])):
        best, best_d = None, 9.0
        for g in groups:
            ref = g[0]
            if ref["setting"][:5] != f["setting"][:5] or ref["time"] != f["time"]:
                continue
            d = _look_distance(ref, f)
            if d < best_d:
                best, best_d = g, d
        if best is not None and best_d < 0.35:
            best.append(f)
        else:
            groups.append([f])
    return groups


def _tags(f: dict) -> dict:
    return {"setting": f["setting"], "time": f["time"], "people": f["people"], "brightness": round(f["lum"], 2),
            "size_rank": round(f["size_rank"], 1), "motion": round(f["energy"], 3), "card": "titles" if f["titles"]
            else "credits" if f["credits"] else None, "mood": f["moods"]}


def _majority(vals):
    vals = [v for v in vals if v]
    return max(set(vals), key=vals.count) if vals else None


TITLE_WORDS = re.compile(r"title|intro|opening|main", re.I)
CREDIT_WORDS = re.compile(r"credit|end|outro|closing", re.I)


def _assign_cards(feats: list[dict]) -> None:
    """Text cards become opening titles or end credits: by file name first, else the earliest card is the title and
    later ones are credits (camera/file order)."""
    cards = sorted([f for f in feats if f["text_share"] >= 0.5], key=lambda f: _natkey(f["name"]))
    for i, f in enumerate(cards):
        if CREDIT_WORDS.search(f["name"]) and not TITLE_WORDS.search(f["name"]):
            f["credits"] = True
        elif TITLE_WORDS.search(f["name"]):
            f["titles"] = True
        elif i == 0:
            f["titles"] = True
        else:
            f["credits"] = True


def assemble(clips: list[dict], script: str | None) -> dict:
    feats = [clip_features(c) for c in clips]
    _assign_cards(feats)
    beats = split_beats(script)
    by_id = {f["id"]: f for f in feats}
    flags: dict[str, list[dict]] = {f["id"]: [] for f in feats}

    # ---- technical / look misfits
    asp = _majority([round(f["aspect"], 2) for f in feats])
    fps = _majority([round(f["fps"]) for f in feats if f["fps"]])
    hgt = _majority([f["height"] for f in feats if f["height"]])
    lums = [f["lum"] for f in feats]
    mean_l = sum(lums) / len(lums) if lums else 0.4
    sd_l = (sum((x - mean_l) ** 2 for x in lums) / len(lums)) ** 0.5 if lums else 0
    for f in feats:
        card = f["titles"] or f["credits"]          # text on black is meant to be dark: skip look/exposure checks
        if len(feats) >= 3 and asp and abs(f["aspect"] - asp) / asp > 0.03:
            flags[f["id"]].append({"code": "look_aspect", "params": {"aspect": round(f["aspect"], 2), "majority": asp}})
        if len(feats) >= 3 and fps and f["fps"] and abs(round(f["fps"]) - fps) >= 2 and round(f["fps"]) not in (fps * 2, fps * 4):
            flags[f["id"]].append({"code": "look_fps", "params": {"fps": round(f["fps"], 2), "majority": fps}})
        if len(feats) >= 3 and hgt and f["height"] and f["height"] < hgt * 0.5:
            flags[f["id"]].append({"code": "look_resolution", "params": {"height": f["height"], "majority": hgt}})
        if not card and len(feats) >= 4 and sd_l > 0.02 and abs(f["lum"] - mean_l) > 2.5 * sd_l:
            flags[f["id"]].append({"code": "look_exposure", "params": {"pct": round(f["lum"] * 100)}})
        for issue in ([] if card else f["issues"]):
            if issue.get("severity") == "major":
                flags[f["id"]].append({"code": "technical", "params": {"issue": issue.get("issue", "")}})
                break
        if f["duration"] < 0.6:
            flags[f["id"]].append({"code": "too_short", "params": {"secs": round(f["duration"], 2)}})

    # ---- duplicate takes: same setting/time, near-identical look and length → keep the cleaner one
    names = {f["id"]: f["name"] for f in feats}
    dup_of: dict[str, str] = {}
    ordered = sorted(feats, key=lambda x: _natkey(x["name"]))
    for i, a in enumerate(ordered):
        for b in ordered[i + 1:]:
            if a["id"] in dup_of or b["id"] in dup_of:
                continue
            if a["setting"][:5] == b["setting"][:5] and a["time"] == b["time"] and _look_distance(a, b) < 0.06 \
                    and abs(a["duration"] - b["duration"]) <= max(1.0, 0.25 * max(a["duration"], b["duration"])) \
                    and abs(a["size_rank"] - b["size_rank"]) < 0.6:
                worse, better = (b, a) if len(flags[b["id"]]) >= len(flags[a["id"]]) else (a, b)
                dup_of[worse["id"]] = better["id"]
                flags[worse["id"]].append({"code": "duplicate", "params": {"other": better["name"]}})

    # ---- placement
    placed: list[dict] = []
    if beats:
        cue_beats = [b for b in beats if b["cues"]]
        for f in feats:
            scored = [(*_cue_score(b, f), b) for b in beats]
            best = max(scored, key=lambda t: (t[0], -t[2]["index"]))
            score, agree, beat = best
            # misfit only when the clip contradicts every part of the description; a clip that simply has no
            # recognisable cue in common is kept (in camera order) rather than thrown away
            if cue_beats and score < 0 and not f["titles"] and not f["credits"]:
                flags[f["id"]].append({"code": "no_match", "params": {}})
                beat_idx = None
            else:
                beat_idx = beat["index"] if score > 0 else None
            placed.append({"f": f, "beat": beat_idx, "score": round(score, 2), "agree": agree})
        # clips without a matching beat (but not misfits) keep camera order after matched ones of similar look
        def key(p):
            f = p["f"]
            if f["titles"]:
                return (-1, 0, _natkey(f["name"]))
            if f["credits"]:
                return (10 ** 6, 0, _natkey(f["name"]))
            # within a beat keep camera file order: a scene's setups are usually shot in order (wide, then coverage)
            return (p["beat"] if p["beat"] is not None else 10 ** 5, _natkey(f["name"]))
        placed.sort(key=key)
    else:
        groups = _scenes([f for f in feats])
        seq = []
        for gi, g in enumerate(groups):
            for f in sorted(g, key=lambda x: (x["size_rank"], _natkey(x["name"]))):
                seq.append({"f": f, "beat": None, "score": None, "agree": [], "scene": gi})
        seq.sort(key=lambda p: (-1 if p["f"]["titles"] else 10 ** 6 if p["f"]["credits"] else p["scene"]))
        placed = seq

    # ---- build the cut: misfits go to the "not used" list (still shown, with reasons)
    misfit_codes = {"no_match", "duplicate", "look_aspect", "look_fps", "look_resolution", "too_short"}
    used, rejected = [], []
    for p in placed:
        f = p["f"]
        if any(fl["code"] in misfit_codes for fl in flags[f["id"]]):
            rejected.append({"clip_id": f["id"], "name": f["name"], "flags": flags[f["id"]]})
        else:
            used.append(p)

    sequence = []
    t = 0.0
    for i, p in enumerate(used):
        f = p["f"]
        reasons = []
        if f["titles"]:
            reasons.append({"code": "titles_first", "params": {}})
        elif f["credits"]:
            reasons.append({"code": "credits_last", "params": {}})
        elif p["beat"] is not None:
            reasons.append({"code": "beat_match", "params": {"beat": p["beat"] + 1, "cues": ", ".join(p["agree"])}})
        elif beats:
            reasons.append({"code": "no_beat_kept", "params": {}})
        else:
            reasons.append({"code": "scene_group", "params": {"scene": p.get("scene", 0) + 1}})
        prev = used[i - 1]["f"] if i else None
        if prev and prev["setting"][:5] == f["setting"][:5] and prev["time"] == f["time"] and f["size_rank"] > prev["size_rank"]:
            reasons.append({"code": "size_progression", "params": {}})
        # transition into this clip
        if prev is None:
            transition = "fade_in"
        elif prev["time"] != f["time"] or prev["setting"][:5] != f["setting"][:5]:
            transition = "dissolve" if prev["time"] != f["time"] else "cut_new_scene"
        elif abs(prev["subject_x"] - f["subject_x"]) < 0.08 and abs(prev["size_rank"] - f["size_rank"]) >= 1:
            transition = "match_cut"
        else:
            transition = "cut"
        if f["dialogue"] and prev is not None:
            transition_extra = "jcut"
        else:
            transition_extra = None
        head = 0.25 if f["duration"] > 2.5 else 0.0
        tail = 0.25 if f["duration"] > 2.5 else 0.0
        length = max(0.0, f["duration"] - head - tail)
        sequence.append({"position": i + 1, "clip_id": f["id"], "name": f["name"], "beat": p["beat"],
                         "in_s": round(head, 2), "out_s": round(f["duration"] - tail, 2), "length_s": round(length, 2),
                         "starts_at_s": round(t, 2), "transition": transition, "transition_extra": transition_extra,
                         "reasons": reasons, "flags": flags[f["id"]], "score": p["score"], "tags": _tags(f)})
        t += length
    if sequence:
        sequence[-1]["transition_out"] = "fade_out"

    uncovered = [b["index"] + 1 for b in beats if b["cues"] and not any(s["beat"] == b["index"] for s in sequence)]
    notes = []
    if uncovered:
        notes.append({"code": "beats_uncovered", "params": {"beats": ", ".join(map(str, uncovered[:20]))}})
    if beats and not any(b["cues"] for b in beats):
        notes.append({"code": "script_no_cues", "params": {}})
    if not beats:
        notes.append({"code": "no_script", "params": {}})
    return {"version": 1, "beats": beats, "sequence": sequence, "rejected": rejected, "notes": notes,
            "runtime_s": round(t, 2), "clip_count": len(feats)}
