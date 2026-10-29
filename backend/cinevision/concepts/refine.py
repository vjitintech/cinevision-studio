"""Iterative refinement: natural-language commands (rule-based parser) and structured patches.

Every refinement returns a *new* concept dict; the caller stores it as a new revision. The original footage and
earlier revisions are never modified. Unrecognised instructions are reported back, never silently guessed.
"""
from __future__ import annotations

import copy
import math
import re

from . import builder
from . import geometry as G
from .archetypes import ARCHETYPES

# ---------------------------------------------------------------- recompute derived fields


def rebuild_shot(concept: dict, s: dict) -> None:
    sensor = concept["sensor"]
    ff = s["lens"]["ff_equiv_mm"]
    s["lens"].update(focal_mm=round(G.to_sensor_focal(ff, sensor)), category=G.lens_category(ff),
                     perspective=G.perspective_note(ff))
    s["distance_m"] = G.distance_for(s["size"], ff, sensor)
    sub = next((x for x in concept["scene"]["subjects"] if x["id"] == s.get("subject_target")), concept["scene"]["subjects"][0])
    target = [sub["x"], round(G.SUBJECT_REF_HEIGHT.get(s["size"], 1.35) * sub.get("height", 1.7) / 1.7, 3), sub["z"]]
    pos = G.orbit_position(tuple(target), s["azimuth_deg"], s["distance_m"], s["camera_height_m"], sub["facing_deg"])
    s["viewing_direction"] = builder._view_label(s["azimuth_deg"])
    sp = s["movement"]["speed_m_s"]
    s["movement"]["speed"] = "none" if sp == 0 else "slow" if sp < 0.2 else "moderate" if sp < 0.6 else "fast"
    s["movement"]["rig"] = builder._rig(s["movement"]["type"], concept["budget"])
    s["camera_path"] = builder.camera_path(s["movement"]["type"], pos, target, s["lens"]["focal_mm"], sp, s["duration_s"], sub)
    L = s["lighting"]
    r = L["ratio"]
    L["key_to_fill_ratio"] = f"{r:g}:1 (~{round(math.log2(r), 1)} stops)"
    for src in L["sources"]:
        if src["role"] == "fill":
            src["intensity"] = round(1 / r, 3)


def refresh(concept: dict, analysis: dict | None) -> dict:
    t = 0.0
    for s in concept["shots"]:
        s["start_s"] = round(t, 2)
        t += s["duration_s"]
        rebuild_shot(concept, s)
    concept["production"] = builder.production(concept)
    concept["continuity_risks"] = builder.continuity(concept, builder._signals(analysis or {}))
    if analysis:
        concept["differs_from_original"] = builder.differences(concept, analysis)
    return concept


# ---------------------------------------------------------------- operations
def _shots(concept, shot_idx):
    return [s for s in concept["shots"] if shot_idx is None or s["index"] == shot_idx]


def op_dramatic(c, idx, n=1):
    for s in _shots(c, idx):
        L = s["lighting"]
        L["ratio"] = min(16, L["ratio"] * 2)
        for src in L["sources"]:
            if src["role"] == "key":
                src["softness"] = round(max(0.15, (src["softness"] or 0.5) - 0.3), 2)
                src["elevation_deg"] = max(src["elevation_deg"], 40)
                src["azimuth_deg"] = math.copysign(max(60, abs(src["azimuth_deg"])), src["azimuth_deg"] or 1)
        if not any(x["role"] == "negative_fill" for x in L["sources"]):
            key = next(x for x in L["sources"] if x["role"] == "key")
            L["sources"].append({"role": "negative_fill", "fixture": "Floppy / black flag", "azimuth_deg": -math.copysign(60, key["azimuth_deg"] or 1),
                                 "elevation_deg": 0, "distance_m": 1.0, "intensity": 0.0, "cct_k": None, "softness": None,
                                 "motivation": "deepens the shadow side"})
        L["setup"] = L["setup"].split(" — ")[0] + " — harder, higher contrast"
        L["exposure"] = "Expose for the highlight side of the face; let shadows fall 3+ stops under."
    c["color"]["grade"] = "deeper blacks, more contrast; " + c["color"]["grade"]
    return "Made the lighting more dramatic (higher key-to-fill ratio, harder key, negative fill)."


def op_softer(c, idx, n=1):
    for s in _shots(c, idx):
        L = s["lighting"]
        L["ratio"] = max(1.5, L["ratio"] / 2)
        for src in L["sources"]:
            if src["role"] == "key":
                src["softness"] = round(min(1.0, (src["softness"] or 0.5) + 0.3), 2)
        L["sources"] = [x for x in L["sources"] if x["role"] != "negative_fill"]
        L["setup"] = L["setup"].split(" — ")[0] + " — softer, lower contrast"
    return "Softened the lighting (lower ratio, larger source, no negative fill)."


def op_lower(c, idx, n=1):
    for s in _shots(c, idx):
        s["camera_height_m"] = round(max(0.15, s["camera_height_m"] - 0.5), 2)
        s["camera_tilt_deg"] = min(25, s["camera_tilt_deg"] + 8)
        s["angle"] = "ground level" if s["camera_height_m"] < 0.4 else "low angle" if s["camera_height_m"] < 1.3 else s["angle"]
    return "Moved the camera lower (≈0.5 m) and tilted up."


def op_higher(c, idx, n=1):
    for s in _shots(c, idx):
        s["camera_height_m"] = round(min(5.0, s["camera_height_m"] + 0.6), 2)
        s["camera_tilt_deg"] = max(-85, s["camera_tilt_deg"] - 10)
        s["angle"] = "overhead" if s["camera_height_m"] > 4 else "high angle" if s["camera_height_m"] > 1.9 else s["angle"]
    return "Raised the camera (≈0.6 m) and tilted down."


def op_slower(c, idx, n=1):
    for s in _shots(c, idx):
        s["movement"]["speed_m_s"] = round(s["movement"]["speed_m_s"] * 0.6, 3)
    return "Slowed camera movement by 40%."


def op_faster(c, idx, n=1):
    for s in _shots(c, idx):
        s["movement"]["speed_m_s"] = round(max(0.2, s["movement"]["speed_m_s"] * 1.5), 3)
    return "Sped up camera movement by 50%."


def op_dolly_in(c, idx, n=1):
    for s in _shots(c, idx):
        s["movement"]["type"] = "slow push-in (dolly)"
        if s["movement"]["speed_m_s"] == 0:
            s["movement"]["speed_m_s"] = 0.12
    return "Changed movement to a dolly push-in."


def op_static(c, idx, n=1):
    for s in _shots(c, idx):
        s["movement"].update(type="static", speed_m_s=0.0)
    return "Locked the camera off (static)."


def op_handheld(c, idx, n=1):
    for s in _shots(c, idx):
        s["movement"]["type"] = "handheld tracking"
        s["movement"]["speed_m_s"] = max(0.3, s["movement"]["speed_m_s"])
    return "Switched to handheld tracking."


def op_separation(c, idx, n=1):
    for s in _shots(c, idx):
        L = s["lighting"]
        back = next((x for x in L["sources"] if x["role"] in ("backlight", "rim")), None)
        if back:
            back["intensity"] = round(min(1.2, back["intensity"] + 0.3), 2)
        else:
            key = next(x for x in L["sources"] if x["role"] == "key")
            L["sources"].append({"role": "rim", "fixture": "LED with barn doors", "azimuth_deg": -math.copysign(145, key["azimuth_deg"] or 1),
                                 "elevation_deg": 40, "distance_m": 3.0, "intensity": 0.7, "cct_k": 4300, "softness": 0.35,
                                 "motivation": "separates subject from background"})
        s["lens"]["ff_equiv_mm"] = round(min(200, s["lens"]["ff_equiv_mm"] * 1.25))
        s["dof"]["aperture"] = "T1.8–T2.8 (one stop wider)"
        s["dof"]["strategy"] = "shallower — background softened; add distance between subject and background"
    return "Increased background separation (stronger rim/backlight, longer lens, wider aperture)."


def op_new_angle(c, idx, n=1):
    for s in _shots(c, idx):
        new = s["azimuth_deg"] + (35 if s["azimuth_deg"] <= 0 else -35)
        s["azimuth_deg"] = round(max(-170, min(170, new)), 1)
    return "Kept the blocking and moved the camera ≈35° around the subject to a new angle."


def op_endings(c, idx, n=3):
    last = c["shots"][-1]
    options = [
        {"name": "Push to extreme close-up", "movement": "slow push-in", "size": "extreme close-up",
         "note": "Ends inside the character's reaction; withholds the wider context."},
        {"name": "Pull-back reveal", "movement": "dolly out + crane up", "size": "wide",
         "note": "Reveals the space or another character, reframing what we just saw."},
        {"name": "Rack focus to background", "movement": "static", "size": last["size"],
         "note": "Focus shifts off the subject to a detail behind them — the scene's real subject."},
        {"name": "Hold and let them exit", "movement": "static", "size": last["size"],
         "note": "The subject leaves frame; the empty frame lingers as the final image."},
        {"name": "Cut to point of view", "movement": "handheld (breathing)", "size": "medium",
         "note": "Ends on what the subject sees, putting the audience in their place."},
    ]
    c["alternative_endings"] = options[:max(1, min(5, n))]
    return f"Created {len(c['alternative_endings'])} alternative endings for the final shot."


def op_budget(c, idx, n=1):
    c["budget"] = "micro"
    from . import lighting
    fx = lighting.FIXTURES["micro"]
    for s in c["shots"]:
        for src in s["lighting"]["sources"]:
            role = src["role"]
            if src.get("fixture") == "Sun":
                continue
            src["fixture"] = {"key": fx["key_soft"] if (src.get("softness") or 0) >= 0.5 else fx["key_hard"], "fill": fx["fill"],
                              "backlight": fx["back"], "rim": fx["back"], "negative_fill": fx["neg"], "practical": fx["practical"]}.get(role, src["fixture"])
        if any(k in s["movement"]["type"] for k in ("crane", "arc")):
            s["movement"]["type"] = "slow push-in (handheld/slider)"
        s["lighting"]["sources"] = [x for x in s["lighting"]["sources"] if not (x["role"] == "fill" and s["lighting"]["ratio"] >= 8)]
    return "Rescoped for a small budget: household/LED fixtures, reflectors, slider/handheld instead of cranes."


def op_wider(c, idx, n=1):
    for s in _shots(c, idx):
        s["size"] = G.step_size(s["size"], 1)
    return "Framed wider by one shot size."


def op_tighter(c, idx, n=1):
    for s in _shots(c, idx):
        s["size"] = G.step_size(s["size"], -1)
    return "Framed tighter by one shot size."


def op_longer_lens(c, idx, n=1):
    for s in _shots(c, idx):
        s["lens"]["ff_equiv_mm"] = round(min(300, s["lens"]["ff_equiv_mm"] * 1.5))
    return "Moved to a longer lens (more compression, camera further back)."


def op_wider_lens(c, idx, n=1):
    for s in _shots(c, idx):
        s["lens"]["ff_equiv_mm"] = round(max(12, s["lens"]["ff_equiv_mm"] / 1.5))
    return "Moved to a wider lens (more depth, camera closer)."


def op_warmer(c, idx, n=1):
    for s in _shots(c, idx):
        for src in s["lighting"]["sources"]:
            if src.get("cct_k") and src["role"] != "practical":
                src["cct_k"] = max(2000, src["cct_k"] - 800)
    c["color"]["palette"] = [builder._hex_shift(h, 1.05, 1.0, -8) for h in c["color"]["palette"]]
    return "Warmed the light (≈−800 K) and the grade."


def op_cooler(c, idx, n=1):
    for s in _shots(c, idx):
        for src in s["lighting"]["sources"]:
            if src.get("cct_k") and src["role"] != "practical":
                src["cct_k"] = min(10000, src["cct_k"] + 800)
    c["color"]["palette"] = [builder._hex_shift(h, 1.0, 1.0, 10) for h in c["color"]["palette"]]
    return "Cooled the light (≈+800 K) and the grade."


def op_haze(c, idx, n=1):
    for s in _shots(c, idx):
        s["lighting"]["haze"] = True
    return "Added atmospheric haze (backlight will read as visible beams)."


def op_no_haze(c, idx, n=1):
    for s in _shots(c, idx):
        s["lighting"]["haze"] = False
    return "Removed haze."


def op_dutch(c, idx, n=1):
    for s in _shots(c, idx):
        s["dutch_roll_deg"] = 12
        s["angle"] = "dutch angle"
    return "Added a 12° Dutch tilt."


RULES: list[tuple[str, callable]] = [
    (r"\b(more|extra)\s+(dramatic|moody|contrast\w*)|\bdarker\b|\bmoodier\b|\bmore\s+shadow", op_dramatic),
    (r"\b(softer|flatter|less\s+contrast|less\s+dramatic|gentler)\b", op_softer),
    (r"\b(camera\s+)?(lower|low(er)?\s+angle|from\s+below)\b", op_lower),
    (r"\b(camera\s+)?(higher|high(er)?\s+angle|from\s+above)\b", op_higher),
    (r"\bdolly[\s-]*in\b|\bpush[\s-]*in\b", op_dolly_in),
    (r"\bslow(er)?\b", op_slower),
    (r"\bfaster\b|\bquicker\b|\bmore\s+energ", op_faster),
    (r"\b(lock(ed)?[\s-]*off|static|no\s+movement|tripod)\b", op_static),
    (r"\bhandheld\b", op_handheld),
    (r"\bseparat|\bbackground\s+(pop|fall\s+away)|\bisolate\s+the\s+subject", op_separation),
    (r"\b(change|different|new)\s+(the\s+)?(camera\s+)?angle\b|\bkeep\b.*\bblocking\b", op_new_angle),
    (r"\b(alternative|alternate|different)\s+endings?\b", op_endings),
    (r"\b(small|low|tiny|micro|cheap|limited)\s+(production\s+)?budget\b|\bcheaper\b|\bon\s+a\s+budget\b", op_budget),
    (r"\bwider\s+(shot|frame|framing)\b|\bframe\s+wider\b|\bpull\s+back\b", op_wider),
    (r"\btighter\b|\bcloser\s+(shot|framing)\b|\bmove\s+in\s+closer\b", op_tighter),
    (r"\blonger\s+lens\b|\btelephoto\b|\bmore\s+compression\b", op_longer_lens),
    (r"\bwider\s+lens\b|\bwide[\s-]angle\b", op_wider_lens),
    (r"\bwarmer\b", op_warmer),
    (r"\bcooler\b|\bcolder\b", op_cooler),
    (r"\bno\s+haze\b|\bremove\s+(the\s+)?haze\b", op_no_haze),
    (r"\b(add\s+)?haze\b|\batmospher\w*\b|\bsmok\w*\b", op_haze),
    (r"\bdutch\b|\bcanted\b", op_dutch),
]
NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}


def parse(command: str) -> tuple[list[tuple[callable, int | None, int, str]], list[str]]:
    """Split into clauses; each clause may match several rules. Returns ops and unrecognised clauses."""
    ops, unknown = [], []
    clauses = [c.strip() for c in re.split(r"[.;\n,]|\band\b|\bthen\b", command, flags=re.I) if c and c.strip()]
    for cl in clauses:
        low = cl.lower()
        m = re.search(r"\bshot\s+(\d+)\b", low)
        idx = int(m.group(1)) - 1 if m else None
        nm = re.search(r"\b(\d|one|two|three|four|five)\b\s+(alternative|alternate|different)", low)
        n = int(NUMBERS.get(nm.group(1), nm.group(1))) if nm else 3
        matched = False
        used = set()
        for pattern, fn in RULES:
            if fn in used:
                continue
            if re.search(pattern, low):
                if fn is op_haze and op_no_haze in used:
                    continue
                if fn is op_lower and op_new_angle in used:
                    continue
                ops.append((fn, idx, n, cl))
                used.add(fn)
                matched = True
        if not matched:
            unknown.append(cl)
    return ops, unknown


def apply_command(concept: dict, command: str, analysis: dict | None) -> tuple[dict, list[str], list[str]]:
    new = copy.deepcopy(concept)
    ops, unknown = parse(command)
    applied = []
    for fn, idx, n, _ in ops:
        if idx is not None and not any(s["index"] == idx for s in new["shots"]):
            unknown.append(f"shot {idx + 1} does not exist")
            continue
        applied.append(fn(new, idx, n))
    if applied:
        refresh(new, analysis)
    return new, applied, unknown


# ---------------------------------------------------------------- structured patches
EDITABLE = {
    "size": lambda v: v in G.SHOT_SIZES,
    "angle": lambda v: v in G.ANGLES,
    "camera_height_m": lambda v: isinstance(v, (int, float)) and 0.05 <= v <= 12,
    "camera_tilt_deg": lambda v: isinstance(v, (int, float)) and -90 <= v <= 90,
    "azimuth_deg": lambda v: isinstance(v, (int, float)) and -180 <= v <= 180,
    "dutch_roll_deg": lambda v: isinstance(v, (int, float)) and -45 <= v <= 45,
    "duration_s": lambda v: isinstance(v, (int, float)) and 0.3 <= v <= 600,
    "lens.ff_equiv_mm": lambda v: isinstance(v, (int, float)) and 8 <= v <= 600,
    "dof.aperture": lambda v: isinstance(v, str) and len(v) <= 40,
    "dof.focus": lambda v: isinstance(v, str) and len(v) <= 300,
    "movement.type": lambda v: isinstance(v, str) and 2 <= len(v) <= 60,
    "movement.speed_m_s": lambda v: isinstance(v, (int, float)) and 0 <= v <= 10,
    "lighting.ratio": lambda v: isinstance(v, (int, float)) and 1 <= v <= 32,
    "lighting.haze": lambda v: isinstance(v, bool),
    "blocking": lambda v: isinstance(v, str) and len(v) <= 1000,
    "notes": lambda v: isinstance(v, str) and len(v) <= 2000,
    "transition_out": lambda v: isinstance(v, str) and len(v) <= 80,
    "composition": lambda v: isinstance(v, str) and len(v) <= 120,
}


class PatchError(ValueError):
    pass


def apply_patch(concept: dict, patches: list[dict], analysis: dict | None) -> tuple[dict, list[str]]:
    new = copy.deepcopy(concept)
    applied = []
    for p in patches:
        field, val, shot = p.get("field"), p.get("value"), p.get("shot")
        if field not in EDITABLE:
            raise PatchError(f"'{field}' cannot be edited")
        if not EDITABLE[field](val):
            raise PatchError(f"invalid value for '{field}'")
        targets = new["shots"] if shot is None else [s for s in new["shots"] if s["index"] == shot]
        if not targets:
            raise PatchError(f"shot {shot} does not exist")
        for s in targets:
            obj, key = s, field
            if "." in field:
                head, key = field.split(".", 1)
                obj = s[head]
            obj[key] = val
        applied.append(f"Set {field} = {val}" + (f" on shot {shot + 1}" if shot is not None else " on all shots"))
    refresh(new, analysis)
    return new, applied


def archetype_names() -> dict:
    return {k: v["name"] for k, v in ARCHETYPES.items()}
