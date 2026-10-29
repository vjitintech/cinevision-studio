"""Narrative / emotional reading — strictly from visible, measurable cues. No identities, no invented story."""
from __future__ import annotations

from . import tag


def interpret(*, shots: list[dict], duration: float, motion: list[dict], tone: dict, subjects: dict, env: dict,
              composition: list[dict]) -> dict:
    n_shots = len(shots)
    avg_shot = duration / max(1, n_shots)
    cam = [m["camera_vs_subject"]["value"]["camera_motion"] for m in motion if m.get("camera_vs_subject")]
    subj = [m["camera_vs_subject"]["value"]["subject_motion"] for m in motion if m.get("camera_vs_subject")]
    energy = (sum(cam) / len(cam) if cam else 0) + (sum(subj) / len(subj) if subj else 0)
    handheld = any(m.get("handheld", {}).get("value") for m in motion)
    mean_l = tone["mean_luminance"]["value"]
    contrast = tone["contrast"]["value"]["range_p5_p95"]
    cast = tone["color_temperature"]["value"]["cast"]
    sat = tone["saturation"]["value"]["mean"]
    n_people = subjects["count"]["value"]
    neg = sum(c["negative_space"]["value"] for c in composition) / max(1, len(composition))

    pacing = ("rapid cutting" if avg_shot < 1.5 else "measured" if avg_shot < 5 else "long take / unhurried")
    if energy > 0.25:
        pacing += ", high on-screen energy"
    elif energy < 0.04:
        pacing += ", very still"

    moods = []
    if mean_l < 0.3 and contrast > 0.55:
        moods.append("tense / mysterious")
    if mean_l < 0.3 and cast == "cool":
        moods.append("melancholic / cold")
    if mean_l > 0.55 and cast == "warm":
        moods.append("warm / inviting")
    if mean_l > 0.6 and contrast < 0.5:
        moods.append("light / airy")
    if sat < 0.18:
        moods.append("restrained / desaturated")
    if handheld or energy > 0.25:
        moods.append("urgent / immediate")
    if neg > 0.6 and n_people <= 1:
        moods.append("isolated / contemplative")
    if not moods:
        moods.append("neutral / observational")

    if n_people == 0:
        relation = "No people detected — the scene may be an environment, object or insert shot."
    elif n_people == 1:
        relation = "A single visible subject."
    else:
        pos = subjects["positions"]["value"]
        spread = (max(p["x"] for p in pos) - min(p["x"] for p in pos)) if len(pos) >= 2 else 0
        relation = f"{n_people} visible subjects, " + ("framed close together" if spread < 0.3 else "spread across the frame")

    purpose_guess = ("establishing the space" if n_people == 0 and avg_shot > 3 else
                     "character moment / dialogue coverage" if n_people in (1, 2) and energy < 0.15 else
                     "action or movement beat" if energy > 0.25 else "scene coverage")
    return {
        "pacing": tag({"label": pacing, "shots": n_shots, "average_shot_s": round(avg_shot, 2),
                       "motion_energy": round(energy, 3)}, 0.6, "estimated"),
        "mood": tag(moods, 0.35, "heuristic",
                    "Inferred from light, colour and movement only. Add your intended emotion in the director's brief."),
        "spatial_relationships": tag(relation, 0.4, "estimated"),
        "apparent_purpose": tag(purpose_guess, 0.25, "heuristic",
                                "A guess from visual structure. The brief's scene purpose overrides this."),
        "setting_summary": tag(f"{env['setting']['value']}, {env['time_of_day']['value']}", 0.3, "heuristic"),
    }
