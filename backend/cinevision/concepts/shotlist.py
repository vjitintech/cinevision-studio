"""Production shot list derived from a concept (plus any saved lighting-studio layouts)."""
from __future__ import annotations


def _fmt_light(src: dict) -> str:
    parts = [src["role"].replace("_", " ").title()]
    if src.get("fixture"):
        parts.append(src["fixture"])
    if src["role"] != "negative_fill":
        side = "camera-left" if src["azimuth_deg"] > 0 else "camera-right" if src["azimuth_deg"] < 0 else "on-axis"
        parts.append(f"{abs(round(src['azimuth_deg']))}° {side}, {round(src['elevation_deg'])}° up")
        if src.get("cct_k"):
            parts.append(f"{src['cct_k']} K")
        parts.append(f"{round(src['intensity'] * 100)}% rel.")
    else:
        parts.append(f"shadow side ({'camera-right' if src['azimuth_deg'] < 0 else 'camera-left'})")
    if src.get("gel"):
        parts.append(f"gel: {src['gel']}")
    return " · ".join(parts)


def build(concept: dict, lighting_overrides: dict[int, dict] | None = None) -> dict:
    lighting_overrides = lighting_overrides or {}
    rows = []
    for s in concept["shots"]:
        L = lighting_overrides.get(s["index"]) or s["lighting"]
        equipment = [s["movement"]["rig"], f"{s['lens']['focal_mm']} mm lens"] + \
            [x["fixture"] for x in L["sources"] if x.get("fixture") and x["fixture"] != "Sun"]
        if L.get("haze"):
            equipment.append("hazer")
        rows.append({
            "shot": s["index"] + 1,
            "duration_s": s["duration_s"],
            "purpose": s["purpose"],
            "size": s["size"],
            "angle": s["angle"] + (f" (Dutch {s['dutch_roll_deg']}°)" if s.get("dutch_roll_deg") else ""),
            "camera_position": f"{s['viewing_direction']}, ~{s['distance_m']} m from subject, lens height {s['camera_height_m']} m",
            "lens": f"{s['lens']['focal_mm']} mm {s['lens']['category']} ({s['lens']['ff_equiv_mm']} mm FF-equiv., "
                    f"{s['lens']['sensor'].replace('_', ' ')})",
            "movement": f"{s['movement']['type']} — {s['movement']['speed']}" +
                        (f" (~{s['movement']['speed_m_s']} m/s)" if s["movement"]["speed_m_s"] else "") + f", {s['movement']['rig']}",
            "focus_dof": f"{s['dof']['aperture']}; {s['dof']['strategy']}; {s['dof']['focus']}",
            "blocking": s["blocking"],
            "lighting": f"{L['setup']} — {L['key_to_fill_ratio']}" + ("; haze" if L.get("haze") else ""),
            "lighting_detail": [_fmt_light(x) for x in L["sources"]],
            "equipment": list(dict.fromkeys(equipment)),
            "continuity": "; ".join(concept["continuity_risks"][:2]),
            "transition": s["transition_out"],
            "notes": s.get("notes", ""),
        })
    return {
        "concept_name": concept["name"],
        "aspect_ratio": concept["aspect_ratio"],
        "frame_rate": concept["frame_rate"],
        "total_duration_s": round(sum(r["duration_s"] for r in rows), 2),
        "grade": concept["color"]["direction"],
        "palette": concept["color"]["palette"],
        "rows": rows,
        "alternative_endings": concept.get("alternative_endings", []),
        "production": concept["production"],
    }
