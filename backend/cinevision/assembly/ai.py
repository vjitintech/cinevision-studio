"""Local-AI opinion on the movie order: an experienced-editor prompt over the measured clip facts. The answer is
validated (every clip at most once, only real clip numbers) and stored next to the rule-based order, labelled."""
from __future__ import annotations

import json

from ..adapters import localai
from ..director.i18n import LANGUAGES
from . import engine as AE

SYSTEM = """You are an experienced film editor and director. You get measured facts about each uploaded clip (numbered)
and, if given, the filmmaker's scene description or screenplay. Decide the best story order, which clips do not
belong in the film, and how to cut between them. Never identify or name real people. Write every text value in
{language}. Reply with ONLY one JSON object:
{{"order": [<clip numbers in the order they should play>],
  "unused": [{{"clip": <number>, "reason": "<why it doesn't fit>"}}],
  "notes": ["<up to 5 short editing notes: pacing, transitions, music, what is missing>"]}}"""


def order(uid: str, clips: list[dict], rules: dict, script: str, lang: str) -> dict:
    feats = [AE.clip_features(c) for c in clips]
    facts = [{"n": i + 1, "file": f["name"][:60], "secs": round(f["duration"], 1), "setting": f["setting"], "time": f["time"],
              "brightness": round(f["lum"], 2), "mood": f["moods"], "people": f["people"], "shot_size_rank_0wide_6close": round(f["size_rank"], 1),
              "motion": round(f["energy"], 3), "opening_titles": f["titles"], "end_credits": f["credits"], "dialogue": f["dialogue"]}
             for i, f in enumerate(feats)]
    user = ("Clips:\n" + json.dumps(facts)[:14000] + "\n\nRule-based order for reference: " +
            json.dumps([next(i + 1 for i, f in enumerate(feats) if f["id"] == s["clip_id"]) for s in rules["sequence"]]) +
            (f"\n\nScene description / screenplay:\n{script[:6000]}" if script else "\n\nNo scene description was given."))
    language = LANGUAGES.get(lang, LANGUAGES["en"])["english_name"]
    raw, model = localai.chat_json(uid, SYSTEM.format(language=language), user, timeout=900)
    n = len(feats)
    seen, seq = set(), []
    for x in raw.get("order") or []:
        if isinstance(x, int) and 1 <= x <= n and x not in seen:
            seen.add(x)
            seq.append(x)
    unused = []
    for u in raw.get("unused") or []:
        if isinstance(u, dict) and isinstance(u.get("clip"), int) and 1 <= u["clip"] <= n and u["clip"] not in seen:
            seen.add(u["clip"])
            unused.append({"clip_id": feats[u["clip"] - 1]["id"], "name": feats[u["clip"] - 1]["name"],
                           "reason": str(u.get("reason", ""))[:300]})
    if not seq:
        raise localai.LocalAIError("The model did not return a usable order.")
    return {"status": "used", "model": model, "language": lang,
            "sequence": [{"clip_id": feats[i - 1]["id"], "name": feats[i - 1]["name"]} for i in seq],
            "unused": unused,
            "missing": [{"clip_id": f["id"], "name": f["name"]} for i, f in enumerate(feats) if i + 1 not in seen],
            "notes": [str(x)[:300] for x in (raw.get("notes") or [])[:5] if isinstance(x, (str, int, float))],
            "note": "Written by a local AI model from measured clip facts. It can be wrong; check it against your story."}
