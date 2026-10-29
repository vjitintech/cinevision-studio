"""Generate distinct cinematography concepts from a scene analysis + the director's creative controls."""
from __future__ import annotations

import colorsys
import math

from . import geometry as G
from . import lighting
from .archetypes import ARCHETYPES, BUDGET_ORDER

ENGINE = {"id": "rules-cinematography", "version": "1.0"}

RIGS = {
    "micro": {"dolly": "wheelchair / skateboard dolly", "slider": "60 cm slider", "crane": "monopod raise (limited)",
              "gimbal": "handheld (gimbal if available)", "track": "walk-and-talk handheld", "tripod": "tripod"},
    "low": {"dolly": "doorway dolly", "slider": "1 m slider", "crane": "jib arm (short)", "gimbal": "3-axis gimbal",
            "track": "gimbal follow", "tripod": "fluid-head tripod"},
    "medium": {"dolly": "dolly on track", "slider": "motorised slider", "crane": "jib / small crane", "gimbal": "gimbal on easyrig",
               "track": "dolly on track or gimbal", "tripod": "fluid-head tripod"},
    "high": {"dolly": "studio dolly on track", "slider": "motion-control slider", "crane": "telescopic crane",
             "gimbal": "stabilised remote head", "track": "car/tracking vehicle or Steadicam", "tripod": "geared head"},
}


# ---------------------------------------------------------------- helpers
def _v(d: dict | None, default=None):
    return d.get("value", default) if isinstance(d, dict) else default


def _hex_shift(hexcol: str, sat: float, light: float, hue_shift: float) -> str:
    r, g, b = (int(hexcol[i:i + 2], 16) / 255 for i in (1, 3, 5))
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    h = (h + hue_shift / 360) % 1
    l = max(0.03, min(0.97, l * light))
    s = max(0, min(1, s * sat))
    r, g, b = colorsys.hls_to_rgb(h, l, s)
    return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"


def _complement(hexcol: str) -> str:
    return _hex_shift(hexcol, 1.3, 1.0, 180)


def _words(controls: dict) -> str:
    return " ".join(str(controls.get(k) or "") for k in ("genre", "scene_purpose", "desired_emotion", "visual_style", "brief")).lower()


def _signals(analysis: dict) -> dict:
    shots = analysis.get("shots", [])
    env = analysis.get("environment", {})
    color = analysis.get("color", {})
    motions = [s["movement"].get("metrics") or {} for s in shots]
    energy = _v(analysis.get("narrative", {}).get("pacing"), {}).get("motion_energy", 0) if analysis.get("narrative") else 0
    return {
        "env": _v(env.get("setting"), "uncertain"),
        "tod": _v(env.get("time_of_day"), "uncertain"),
        "practicals": _v(env.get("practical_lights"), []) or [],
        "mean_lum": _v(color.get("mean_luminance"), 0.45),
        "contrast": (_v(color.get("contrast"), {}) or {}).get("range_p5_p95", 0.6),
        "cast": (_v(color.get("color_temperature"), {}) or {}).get("cast", "neutral"),
        "palette": [p["hex"] for p in (_v(color.get("palette"), []) or [])] or ["#7a7a7a", "#404040", "#b0a090"],
        "people": _v(analysis.get("subjects", {}).get("count"), 0) or 0,
        "energy": energy or 0,
        "handheld": any(s["movement"].get("handheld", {}).get("value") for s in shots if s.get("movement")),
        "moving_shots": sum(1 for m in motions if m and abs(m.get("pan_rate", 0)) + abs(m.get("zoom_rate", 0)) > 0.02),
    }


# ---------------------------------------------------------------- scoring & selection
def score_archetypes(analysis: dict, c: dict) -> list[tuple[str, float, list[str]]]:
    words = _words(c)
    sig = _signals(analysis)
    pres = c.get("preservation", "medium")
    budget_i = BUDGET_ORDER.index(c.get("budget", "low"))
    out = []
    for key, a in ARCHETYPES.items():
        why = []
        s = 0.0
        hits = [k for k in a["keywords"] if k in words]
        if hits:
            s += 1.4 * min(3, len(hits))
            why.append(f"matches your brief ({', '.join(hits[:3])})")
        s -= 0.6 * abs(a["movement"] - c.get("movement_intensity", 2))
        s -= 0.5 * abs(a["lighting"] - c.get("lighting_complexity", 2))
        t = {"realistic": 0, "balanced": 1, "stylized": 2}
        s -= 0.7 * abs(t[a["treatment"]] - t[c.get("treatment", "balanced")])
        if BUDGET_ORDER.index(a["min_budget"]) > budget_i:
            s -= 1.5
            why.append("stretches the budget")
        # footage fit
        if key == "suspense" and ("night" in sig["tod"] or sig["mean_lum"] < 0.3):
            s += 1.0
            why.append("footage is already dark")
        if key in ("naturalistic", "atmospheric") and sig["env"] == "outdoor" and "day" in sig["tod"]:
            s += 0.6
            why.append("daylight exterior suits it")
        if key == "kinetic" and (sig["energy"] > 0.2 or sig["handheld"]):
            s += 0.9
            why.append("the clip already has energy")
        if key == "observational" and sig["people"] >= 1 and sig["energy"] < 0.1:
            s += 0.5
            why.append("still, character-focused footage")
        if key == "chiaroscuro" and sig["contrast"] > 0.7:
            s += 0.5
        if key == "formal" and sig["people"] in (1, 2):
            s += 0.2
        if pres == "high" and key in ("naturalistic", "observational"):
            s += 0.8
            why.append("stays close to the original")
        if pres == "low" and key in ("stylized", "kinetic", "formal"):
            s += 0.6
        if c.get("intent") == "animation" and key in ("stylized", "formal", "kinetic"):
            s += 0.5
        out.append((key, round(s, 3), why))
    return sorted(out, key=lambda x: -x[1])


def _feature(a: dict) -> list[float]:
    L = a["light"]
    return [math.log2(L["ratio"]) / 4, a["movement"] / 4, (a["lens_ff"][0] + a["lens_ff"][1]) / 300, L["softness"],
            (a["size_step"] + 2) / 4, 1.0 if L["haze"] else 0.0, a["grade"]["sat"] - 1]


def select(scored, n: int) -> list[tuple[str, float, list[str]]]:
    """Greedy max-marginal-relevance: high score, low similarity to what's already chosen."""
    chosen = []
    pool = list(scored)
    while pool and len(chosen) < n:
        def mmr(item):
            if not chosen:
                return item[1]
            f = _feature(ARCHETYPES[item[0]])
            sim = max(1 / (1 + math.dist(f, _feature(ARCHETYPES[c[0]]))) for c in chosen)
            return item[1] - 3.0 * sim
        best = max(pool, key=mmr)
        chosen.append(best)
        pool.remove(best)
    return chosen


# ---------------------------------------------------------------- scene model
def scene_model(analysis: dict) -> dict:
    shots = analysis.get("shots", [])
    geo = []
    for s in shots:
        g = _v(s.get("scene_geometry"), {}) or {}
        if g.get("subjects_m"):
            geo = g["subjects_m"]
            break
    subjects, estimated = [], True
    if geo:
        for i, p in enumerate(geo[:4]):
            subjects.append({"id": f"S{i + 1}", "label": f"Subject {i + 1}", "x": float(p["x"]), "z": max(1.0, float(p["z"])),
                             "height": 1.7, "facing_deg": 180.0})
    else:
        subjects.append({"id": "S1", "label": "Subject / point of interest", "x": 0.0, "z": 3.0, "height": 1.7,
                         "facing_deg": 180.0})
    if len(subjects) >= 2:  # people in a two-shot usually face each other partially
        subjects[0]["facing_deg"] = 180 - 45
        subjects[-1]["facing_deg"] = 180 + 45
    sig = _signals(analysis)
    return {
        "units": "metres",
        "origin": "original camera position on the floor; +z points where the original camera looked",
        "subjects": subjects,
        "set": {"type": sig["env"], "time_of_day": sig["tod"],
                "practicals": [{"x": round((p["x"] - 0.5) * 4, 2), "z": round(subjects[0]["z"] + 1.5, 2), "height": 1.4,
                                "tint": p.get("tint", "warm")} for p in sig["practicals"][:3]],
                "background_z": round(max(s["z"] for s in subjects) + 3.0, 2)},
        "original_camera": {"position": [0.0, 1.5, 0.0], "target": [subjects[0]["x"], 1.4, subjects[0]["z"]]},
        "estimated": estimated,
        "note": "Subject positions are rough estimates from 2D detections, assuming average human height and a ~55° lens.",
    }


def _target(scene: dict, idx: int) -> tuple[list[float], dict]:
    subs = scene["subjects"]
    sub = subs[idx % len(subs)]
    return [sub["x"], 1.45, sub["z"]], sub


# ---------------------------------------------------------------- movement
def camera_path(move: str, pos: list[float], target: list[float], focal_mm: float, speed: float, dur: float,
                sub: dict) -> dict:
    p = list(pos)
    t = list(target)
    m = move.lower()
    dist_total = speed * dur
    kfs = []

    def unit(a, b):
        d = [b[i] - a[i] for i in range(3)]
        n = math.sqrt(sum(x * x for x in d)) or 1
        return [x / n for x in d]

    if "push" in m or "dolly-in" in m or "dolly in" in m or "creeping" in m:
        u = unit(p, t)
        start = [p[i] - u[i] * dist_total / 2 for i in range(3)]
        end = [p[i] + u[i] * dist_total / 2 for i in range(3)]
        kfs = [{"t": 0, "pos": start, "target": t, "focal_mm": focal_mm}, {"t": 1, "pos": end, "target": t, "focal_mm": focal_mm}]
    elif "pull" in m or "dolly out" in m:
        u = unit(p, t)
        kfs = [{"t": 0, "pos": [p[i] + u[i] * dist_total / 2 for i in range(3)], "target": t, "focal_mm": focal_mm},
               {"t": 1, "pos": [p[i] - u[i] * dist_total / 2 for i in range(3)], "target": t, "focal_mm": focal_mm}]
    elif "lateral" in m or "slide" in m or "drift" in m or "track" in m and "handheld" not in m:
        u = unit(p, t)
        side = [u[2], 0, -u[0]]
        a = [p[i] - side[i] * dist_total / 2 for i in range(3)]
        b = [p[i] + side[i] * dist_total / 2 for i in range(3)]
        kfs = [{"t": 0, "pos": a, "target": [t[0] - side[0] * dist_total / 4, t[1], t[2] - side[2] * dist_total / 4], "focal_mm": focal_mm},
               {"t": 1, "pos": b, "target": [t[0] + side[0] * dist_total / 4, t[1], t[2] + side[2] * dist_total / 4], "focal_mm": focal_mm}]
    elif "arc" in m or "orbit" in m:
        r = math.hypot(p[0] - t[0], p[2] - t[2])
        a0 = math.atan2(p[0] - t[0], p[2] - t[2])
        sweep = min(math.radians(60), dist_total / max(r, 0.5))
        kfs = [{"t": i / 4, "pos": [t[0] + r * math.sin(a0 - sweep / 2 + sweep * i / 4), p[1], t[2] + r * math.cos(a0 - sweep / 2 + sweep * i / 4)],
                "target": t, "focal_mm": focal_mm} for i in range(5)]
    elif "crane" in m or "rise" in m:
        kfs = [{"t": 0, "pos": [p[0], max(0.4, p[1] - 0.8), p[2]], "target": t, "focal_mm": focal_mm},
               {"t": 1, "pos": [p[0], p[1] + 1.2, p[2]], "target": t, "focal_mm": focal_mm}]
    elif "whip" in m or "pan" in m:
        off = 1.2 if "whip" in m else 0.5
        kfs = [{"t": 0, "pos": p, "target": [t[0] - off, t[1], t[2]], "focal_mm": focal_mm},
               {"t": 1, "pos": p, "target": t, "focal_mm": focal_mm}]
    elif "handheld" in m or "follow" in m or "breathing" in m:
        amp = 0.03 if "breathing" in m else 0.08
        fwd = unit(p, t)
        kfs = [{"t": i / 6, "pos": [p[0] + amp * math.sin(i * 2.1) + (fwd[0] * dist_total * (i / 6 - 0.5) if "follow" in m or "tracking" in m else 0),
                                    p[1] + amp * 0.6 * math.cos(i * 1.7),
                                    p[2] + amp * math.cos(i * 2.9) + (fwd[2] * dist_total * (i / 6 - 0.5) if "follow" in m or "tracking" in m else 0)],
                "target": [t[0] + amp * math.sin(i * 1.3), t[1], t[2]], "focal_mm": focal_mm} for i in range(7)]
    else:  # static
        kfs = [{"t": 0, "pos": p, "target": t, "focal_mm": focal_mm}, {"t": 1, "pos": p, "target": t, "focal_mm": focal_mm}]
    for k in kfs:
        k["pos"] = [round(v, 3) for v in k["pos"]]
        k["target"] = [round(v, 3) for v in k["target"]]
        k["focal_mm"] = round(k["focal_mm"], 1)
    return {"keyframes": kfs, "estimated": True}


def _rig(move: str, budget: str) -> str:
    r = RIGS[budget]
    m = move.lower()
    for key, rig in (("dolly", "dolly"), ("push", "dolly"), ("creeping", "dolly"), ("slide", "slider"), ("lateral", "dolly"),
                     ("drift", "gimbal"), ("arc", "gimbal"), ("crane", "crane"), ("follow", "gimbal"), ("tracking", "track"),
                     ("whip", "tripod"), ("pan", "tripod"), ("static", "tripod"), ("handheld", None)):
        if key in m:
            return r[rig] if rig else "handheld (shoulder rig if available)"
    return r["tripod"]


# ---------------------------------------------------------------- one concept
def build_concept(key: str, why: list[str], analysis: dict, c: dict, variant: int = 0) -> dict:
    a = ARCHETYPES[key]
    sig = _signals(analysis)
    sensor = c.get("sensor", "super35")
    budget = c.get("budget", "low")
    eff_budget = budget  # archetypes above budget are penalised in scoring; fixtures always fit the stated budget
    complexity_ctl = c.get("lighting_complexity", 2)
    mv_int = c.get("movement_intensity", 2)
    pres = c.get("preservation", "medium")
    scene = scene_model(analysis)
    orig_shots = analysis.get("shots") or [{"index": 0, "start_s": 0, "end_s": _v(analysis["clip"]["duration_s"], 5)}]
    total = sum(s["end_s"] - s["start_s"] for s in orig_shots) or 5
    target_total = c.get("target_duration_s") or total
    scale_t = target_total / total

    # practical key side: motivate from the frame side where practicals/brightness sit
    key_side = 1
    if sig["practicals"]:
        key_side = 1 if sig["practicals"][0]["x"] < 0.5 else -1
    if variant % 2:
        key_side *= -1

    lens_lo, lens_hi = a["lens_ff"]
    shots = []
    t_cursor = 0.0
    n = len(orig_shots)
    for i, os_ in enumerate(orig_shots):
        dur = round((os_["end_s"] - os_["start_s"]) * scale_t, 2)
        orig_size = G.normalise_size(_v(os_.get("shot_size")))
        step = a["size_step"]
        if pres == "high":
            step = max(-1, min(1, step))  # stay within one size of the original
        if pres == "low" and n > 1:
            step += (-1 if i % 2 else 1)  # alternate scale for more contrast between shots
        if i == n - 1 and key in ("chiaroscuro", "suspense", "atmospheric", "kinetic") and n > 1:
            step -= 1  # land the scene tighter
        size = G.step_size(orig_size, step)
        ff = lens_lo + (lens_hi - lens_lo) * ((i / max(1, n - 1)) if n > 1 else 0.5)
        focal = G.to_sensor_focal(ff, sensor)
        dist = G.distance_for(size, ff, sensor)
        if key == "kinetic":
            dist = max(0.6, dist)
        angle = a["angle"]
        if pres == "high":
            angle = (_v(os_.get("camera_angle")) or "eye level").split(" (")[0]
            angle = angle if angle in G.ANGLES else "eye level"
        if key == "suspense" and i == n - 1:
            angle = "eye level"
        height, tilt = G.ANGLES.get(angle, G.ANGLES["eye level"])
        target, sub = _target(scene, i if len(scene["subjects"]) > 1 else 0)
        comp = a["composition"]
        az = {"three-quarter": 35, "profile": 80, "frontal": 0, "over-the-shoulder": 150, "centred": 0, "through": 60}
        azimuth = next((v for k, v in az.items() if k in comp), 30) * (1 if (i + variant) % 2 == 0 else -1)
        if "over-the-shoulder" in comp and len(scene["subjects"]) > 1:
            other = scene["subjects"][(scene["subjects"].index(sub) + 1) % len(scene["subjects"])]
            dx, dz = other["x"] - sub["x"], other["z"] - sub["z"]
            azimuth = math.degrees(math.atan2(dx, dz)) - sub["facing_deg"] + 12
        if key == "formal":
            azimuth = 0
        pos = G.orbit_position(tuple(target), azimuth, dist, height, sub["facing_deg"])
        moves = a["moves"] if mv_int > 0 else ["static"]
        move = moves[(i + variant) % len(moves)]
        if mv_int >= 3 and move.startswith("static") and len(moves) > 1:
            move = moves[(i + variant + 1) % len(moves)]
        speed = round(a["speed"] * (0.5 + mv_int / 4), 3) if not move.startswith("static") else 0.0
        speed_label = "none" if speed == 0 else "slow" if speed < 0.2 else "moderate" if speed < 0.6 else "fast"
        purpose = ("establish geography and mood" if i == 0 and n > 1 else "deliver the emotional beat" if i == n - 1
                   else "develop the moment and relationship")
        if n == 1:
            purpose = "carry the whole moment in a single designed shot"
        two = len(scene["subjects"]) > 1
        focus = ("rack focus between subjects on the key line" if two and "shallow" in a["dof"] else
                 "hold focus on the eyes; pull with the move" if speed > 0 else "focus on the near eye")
        light = lighting.build(a, env=sig["env"], tod=sig["tod"], practicals=sig["practicals"], budget=eff_budget,
                               complexity=min(complexity_ctl, 4), key_side=key_side, shot_size=size)
        blocking = _blocking(key, scene, i, n, move)
        shots.append({
            "index": i,
            "source_shot_index": os_.get("index", i),
            "start_s": round(t_cursor, 2),
            "duration_s": dur,
            "purpose": purpose,
            "size": size,
            "angle": angle,
            "dutch_roll_deg": 12 if angle == "dutch angle" else 0,
            "composition": comp,
            "camera_height_m": height,
            "camera_tilt_deg": tilt,
            "viewing_direction": _view_label(azimuth),
            "subject_target": sub["id"],
            "azimuth_deg": round(azimuth, 1),
            "distance_m": dist,
            "lens": {"category": G.lens_category(ff), "focal_mm": round(focal), "ff_equiv_mm": round(ff),
                     "range_mm": [round(G.to_sensor_focal(lens_lo, sensor)), round(G.to_sensor_focal(lens_hi, sensor))],
                     "sensor": sensor, "perspective": G.perspective_note(ff)},
            "dof": {"aperture": a["aperture"], "strategy": a["dof"], "focus": focus},
            "movement": {"type": move, "speed": speed_label, "speed_m_s": speed, "rig": _rig(move, eff_budget)},
            "blocking": blocking,
            "lighting": light,
            "transition_out": a["transition"].split(",")[0] if i < n - 1 else "end of scene",
            "notes": "",
            "camera_path": camera_path(move, pos, target, focal, speed, dur, sub),
        })
        t_cursor += dur
    palette = sig["palette"][:5]
    g = a["grade"]
    graded = [_hex_shift(h, g["sat"], g["light"], g["shift"]) for h in palette]
    if key == "stylized":
        graded = [graded[0], _complement(graded[0]), "#0b0b12", graded[1] if len(graded) > 1 else "#202030"]
    concept = {
        "archetype": key,
        "name": a["name"],
        "objective": a["objective"],
        "intended_emotion": c.get("desired_emotion") or a["emotion"],
        "rationale": _rationale(a, why, sig, c),
        "aspect_ratio": c.get("aspect_ratio", "16:9"),
        "frame_rate": c.get("frame_rate", 24),
        "sensor": sensor,
        "budget": eff_budget,
        "color": {"palette": graded, "source_palette": palette, "grade": g["contrast"],
                  "direction": _grade_direction(key, sig)},
        "editing": {"rhythm": a["rhythm"], "transitions": a["transition"]},
        "shots": shots,
        "scene": scene,
        "alternative_endings": [],
    }
    concept["production"] = production(concept)
    concept["continuity_risks"] = continuity(concept, sig)
    concept["differs_from_original"] = differences(concept, analysis)
    return concept


def _view_label(az: float) -> str:
    a = abs(az) % 360
    side = "camera-left" if az > 0 else "camera-right"
    if a < 12:
        return "frontal"
    if a < 55:
        return f"three-quarter front ({side})"
    if a < 110:
        return f"profile ({side})"
    if a < 160:
        return f"three-quarter back / over-the-shoulder ({side})"
    return "from behind"


def _blocking(key: str, scene: dict, i: int, n: int, move: str) -> str:
    subs = scene["subjects"]
    if len(subs) == 1:
        base = {"naturalistic": "Let the actor find natural marks; camera adjusts rather than the actor.",
                "chiaroscuro": "Actor turns into the key light on the decisive line, half the face falling into shadow.",
                "atmospheric": "Actor moves slowly toward the backlight so the halo grows through the shot.",
                "kinetic": "Actor keeps moving through the frame; camera chases rather than leads.",
                "suspense": "Actor holds still with their back to unlit space; something off-screen draws their look.",
                "stylized": "Actor hits an exact mark in profile against the bright background.",
                "observational": "Actor is unaware of the camera; partial occlusion by foreground objects is welcome.",
                "formal": "Actor stands dead centre and turns square to the lens on the punchline."}[key]
    else:
        base = {"naturalistic": "Two-person staging with overlapping action; keep both eyelines on the same side of the line.",
                "chiaroscuro": "Separate the characters into light and shadow; the dominant one stands in the key.",
                "atmospheric": "Characters drift closer; the gap between them is lit by the backlight.",
                "kinetic": "Characters cross and re-cross; camera picks up whoever moves.",
                "suspense": "One character stays in the foreground, partly blocking the other.",
                "stylized": "Characters face each other in profile silhouettes, framed symmetrically.",
                "observational": "Shoot through a doorway or window; let one character partly hide the other.",
                "formal": "Characters stand side by side facing the lens, evenly spaced across the centre line."}[key]
    if "push" in move or "creeping" in move:
        base += " Time the push so it lands on the emotional turn."
    return base


def _rationale(a: dict, why: list[str], sig: dict, c: dict) -> str:
    parts = [a["objective"]]
    if why:
        parts.append("Chosen because it " + "; ".join(dict.fromkeys(why)) + ".")
    parts.append(f"Lighting: {a['light']['style']} at about {a['light']['ratio']:g}:1, so the image reads as {a['emotion']}.")
    if c.get("scene_purpose"):
        parts.append(f"It serves the stated purpose — {c['scene_purpose']}.")
    return " ".join(parts)


def _grade_direction(key: str, sig: dict) -> str:
    return {
        "naturalistic": "Match skin tones to reality; keep the existing palette, only balance shots to each other.",
        "chiaroscuro": "Pull midtones down, deepen blacks, keep a single warm accent in the highlights.",
        "atmospheric": "Lift and tint blacks, soften contrast, add gentle halation around highlights.",
        "kinetic": "Push saturation and local contrast; let practical colours bloom.",
        "suspense": "Cool the shadows, keep practicals warm, hold detail just above black.",
        "stylized": "Commit to two complementary hues; everything else drops toward black.",
        "observational": "Filmic, slightly warm, low-saturation midtones.",
        "formal": "Clean, bright primaries; everything colour-coordinated to the set.",
    }[key]


def production(concept: dict) -> dict:
    a = ARCHETYPES[concept["archetype"]]
    eq = set()
    rigs = set()
    for s in concept["shots"]:
        rigs.add(s["movement"]["rig"])
        for src in s["lighting"]["sources"]:
            if src.get("fixture") and src["fixture"] != "Sun":
                eq.add(src["fixture"])
        if s["lighting"]["haze"]:
            eq.add(lighting.FIXTURES[concept["budget"]]["haze"])
    lenses = sorted({s["lens"]["focal_mm"] for s in concept["shots"]})
    complexity = a["complexity"] + (1 if any(s["lighting"]["haze"] for s in concept["shots"]) else 0)
    complexity += (1 if len(rigs - {"tripod", "fluid-head tripod", "geared head"}) > 1 else 0)
    complexity = max(1, min(5, complexity - (1 if concept["budget"] == "micro" else 0)))
    return {
        "complexity": complexity,
        "complexity_label": ["", "very simple", "simple", "moderate", "involved", "demanding"][complexity],
        "camera_support": sorted(rigs),
        "lenses": [f"{l} mm ({concept['sensor'].replace('_', ' ')})" for l in lenses],
        "lighting_equipment": sorted(eq),
        "crew_hint": {1: "1–2 people", 2: "2–3 people", 3: "3–5 people", 4: "5–8 people", 5: "8+ people"}[complexity],
        "setups": len(concept["shots"]),
    }


def continuity(concept: dict, sig: dict) -> list[str]:
    risks = []
    shots = concept["shots"]
    if len(concept["scene"]["subjects"]) > 1:
        sides = {math.copysign(1, s["azimuth_deg"]) for s in shots if abs(s["azimuth_deg"]) > 5}
        if len(sides) > 1:
            risks.append("Camera crosses between sides of the subjects — check the 180° line and eyeline directions.")
    if any(s["lighting"]["haze"] for s in shots):
        risks.append("Haze density drifts between takes; meter it and re-haze before every take.")
    if sig["env"] == "outdoor" or "window" in str([s["lighting"]["sources"] for s in shots]):
        risks.append("Daylight changes over the shoot day; schedule the wide and closer shots in a short window or flag the sun.")
    if any("handheld" in s["movement"]["type"] for s in shots) and any(s["movement"]["type"].startswith("static") for s in shots):
        risks.append("Mixing handheld and locked-off shots: keep the shift deliberate so it reads as a choice.")
    if any(src["role"] == "practical" for s in shots for src in s["lighting"]["sources"]):
        risks.append("Practicals in frame must stay on, at the same level, in every angle.")
    if len({s["lens"]["focal_mm"] for s in shots}) > 2:
        risks.append("Several focal lengths: match perspective on matching singles (same lens for the reverse).")
    if not risks:
        risks.append("Low continuity risk; keep props and actor positions consistent between set-ups.")
    return risks


def differences(concept: dict, analysis: dict) -> list[str]:
    out = []
    orig = {s["index"]: s for s in analysis.get("shots", [])}
    for s in concept["shots"]:
        o = orig.get(s["source_shot_index"])
        if not o:
            continue
        label = f"Shot {s['index'] + 1}"
        osz = G.normalise_size(_v(o.get("shot_size")))
        if osz != s["size"]:
            tighter = G.SHOT_SIZES.index(s["size"]) < G.SHOT_SIZES.index(osz)
            out.append(f"{label}: {s['size']} instead of {osz} — {'tighter, more intimate' if tighter else 'wider, more context'}.")
        omv = (_v(o["movement"].get("movement")) or "undetermined") if o.get("movement") else "undetermined"
        if omv.split(" ")[0] not in s["movement"]["type"]:
            out.append(f"{label}: {s['movement']['type']} replaces the original {omv}.")
        oang = (_v(o.get("camera_angle")) or "").split(" (")[0]
        if oang and oang != s["angle"] and "undetermined" not in oang:
            out.append(f"{label}: {s['angle']} rather than {oang}.")
    tk = _v(analysis.get("color", {}).get("tonal_key"), "")
    ratio = concept["shots"][0]["lighting"]["ratio"] if concept["shots"] else 2
    new_key = "low-key" if ratio >= 6 else "high-key" if ratio <= 2 else "mid-key"
    if tk and tk != new_key:
        out.append(f"Overall lighting becomes {new_key} (original reads as {tk}).")
    cast = (_v(analysis.get("color", {}).get("color_temperature"), {}) or {}).get("cast")
    out.append(f"Grade: {concept['color']['direction']}" + (f" (original cast: {cast})." if cast else ""))
    return out


def generate(analysis: dict, controls: dict) -> list[dict]:
    n = max(1, min(6, int(controls.get("concept_count", 4))))
    scored = score_archetypes(analysis, controls)
    chosen = select(scored, n)
    return [build_concept(key, why, analysis, controls, variant=i) | {"score": score}
            for i, (key, score, why) in enumerate(chosen)]
