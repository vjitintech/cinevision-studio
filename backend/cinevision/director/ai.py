"""AI layer of the Director's review: a local model looks at keyframes plus the measured analysis and writes a
director's notes in the user's language. Stored separately and always labelled as AI-written."""
from __future__ import annotations

import json

from ..adapters import localai
from .i18n import LANGUAGES

SYSTEM = """You are an experienced film director, cinematographer, editor and sound designer mentoring a filmmaker.
You get measured facts about their footage and a few frames from it. Give honest, specific, practical advice that a
beginner can act on and a professional would respect. Never identify or name real people, and never guess anyone's
age, ethnicity or other personal traits. Do not invent facts that contradict the measurements.
Write every text value in {language}. Reply with ONLY one JSON object with these keys:
{{"overall": "<2-3 sentences: is this shot good, and why>",
  "verdict": "good" | "improve" | "reshoot",
  "story": "<what the scene seems to be about and the feeling it gives>",
  "departments": {{"camera": {{"works": [], "improve": []}}, "composition": {{"works": [], "improve": []}},
    "lighting": {{"works": [], "improve": []}}, "colour": {{"works": [], "improve": []}},
    "sound": {{"works": [], "improve": []}}, "editing": {{"works": [], "improve": []}},
    "performance_and_blocking": {{"works": [], "improve": []}}, "titles": {{"works": [], "improve": []}}}},
  "alternative_ways": ["<up to 4 other ways a top director might shoot this scene, one sentence each>"],
  "edit_ideas": ["<up to 3 ways to edit this footage>"],
  "learn": [{{"term": "<film-craft term you used>", "meaning": "<plain explanation>"}}]}}
Use at most 3 items per list. Keep each item under 30 words."""


def _facts(analysis: dict, review: dict) -> str:
    V = lambda d: d.get("value") if isinstance(d, dict) else d  # noqa: E731
    shots = analysis.get("shots", [])
    facts = {
        "duration_s": V(analysis.get("clip", {}).get("duration_s")),
        "shots": [{"n": s["index"] + 1, "len_s": round(s["end_s"] - s["start_s"], 1), "size": V(s.get("shot_size")),
                   "move": V((s.get("movement") or {}).get("movement")), "angle": V(s.get("camera_angle"))}
                  for s in shots[:40]],
        "total_shots": len(shots),
        "light": {k: V(analysis.get("color", {}).get(k)) for k in ("mean_luminance", "contrast", "clipping", "tonal_key")},
        "colour": {"cast": (V(analysis.get("color", {}).get("color_temperature")) or {}).get("cast"),
                   "saturation": (V(analysis.get("color", {}).get("saturation")) or {}).get("label")},
        "environment": {k: V(analysis.get("environment", {}).get(k)) for k in ("setting", "time_of_day")},
        "mood_reading": V(analysis.get("narrative", {}).get("mood")),
        "sound": {k: V((analysis.get("sound") or {}).get(k)) for k in ("present", "loudness", "content")},
        "on_screen_text": V(analysis.get("on_screen_text")),
        "rule_findings": [f["code"] for d in review["departments"] for f in d["findings"]],
    }
    return json.dumps(facts, default=str)[:12000]


def _clean(v, n=3, maxlen=300) -> list[str]:
    return [str(x)[:maxlen] for x in v if isinstance(x, (str, int, float))][:n] if isinstance(v, list) else []


def notes(uid: str, analysis: dict, review: dict, frames, lang: str, brief: str = "") -> dict:
    language = LANGUAGES.get(lang, LANGUAGES["en"])["english_name"]
    user = ("Measured facts (JSON):\n" + _facts(analysis, review) +
            (f"\n\nThe filmmaker's own note: {brief[:600]}" if brief else "") +
            f"\n\n{len(frames)} frames from the video are attached in time order.")
    try:
        raw, model = localai.chat_json(uid, SYSTEM.format(language=language), user, images=frames, vision=True)
    except localai.LocalAIError:
        # the configured model may not accept pictures: try text-only so the user still gets notes
        raw, model = localai.chat_json(uid, SYSTEM.format(language=language), user.replace(
            f"{len(frames)} frames from the video are attached in time order.", "No frames could be attached."))
        frames = []
    deps = raw.get("departments") if isinstance(raw.get("departments"), dict) else {}
    out_deps = {}
    for k, v in deps.items():
        if isinstance(v, dict):
            out_deps[str(k)[:40]] = {"works": _clean(v.get("works")), "improve": _clean(v.get("improve"))}
    return {
        "status": "used", "model": model, "language": lang, "frames_sent": len(frames),
        "overall": str(raw.get("overall", ""))[:900],
        "verdict": raw.get("verdict") if raw.get("verdict") in ("good", "improve", "reshoot") else None,
        "story": str(raw.get("story", ""))[:900],
        "departments": out_deps,
        "alternative_ways": _clean(raw.get("alternative_ways"), 4),
        "edit_ideas": _clean(raw.get("edit_ideas"), 3),
        "learn": [{"term": str(x.get("term", ""))[:60], "meaning": str(x.get("meaning", ""))[:300]}
                  for x in (raw.get("learn") or [])[:6] if isinstance(x, dict)],
        "basis": "heuristic",
        "note": "Written by a local AI model from the measurements and a few frames. It can be wrong; measured values take precedence.",
    }
