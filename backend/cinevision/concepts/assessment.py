"""Assess the original footage: is it already good, and does it already match one of the suggested approaches?

Two independent questions:
1. Technical health — exposure, clipping, crooked horizon, unintended shake. Measured from the analysis.
2. Creative match — for each concept, how closely the original already does what the concept proposes
   (shot size, angle, movement, lighting key, colour temperature), per shot, 0–100.

Verdict:
* "good"            — a concept matches ≥ 70 and there are no major technical issues → tell the user the footage works.
* "good_with_fixes" — matches a concept but has fixable technical issues.
* "alternatives"    — no concept is close; the concepts are genuine alternatives to what was shot.
Other concepts are always still listed as options.
"""
from __future__ import annotations

from . import geometry as G

GOOD_THRESHOLD = 70


def _v(d, default=None):
    return d.get("value", default) if isinstance(d, dict) else default


def _move_family(label: str) -> str:
    l = (label or "").lower()
    for fam, keys in (("static", ("static", "locked", "tripod")), ("push", ("push", "dolly-in", "dolly in", "creeping", "zoom in")),
                      ("pull", ("pull", "dolly out", "zoom out")), ("pan", ("pan", "whip")), ("tilt", ("tilt", "crane", "rise")),
                      ("lateral", ("lateral", "slide", "drift", "track")), ("arc", ("arc", "orbit")),
                      ("handheld", ("handheld", "follow", "breathing", "gimbal"))):
        if any(k in l for k in keys):
            return fam
    return "other"


def _key_of_ratio(ratio: float) -> str:
    return "low-key" if ratio >= 6 else "high-key" if ratio <= 2 else "mid-key"


def technical_checks(analysis: dict, controls: dict) -> list[dict]:
    """Each issue: {severity: major|minor, issue, fix}. Empty list = technically sound."""
    out = []
    color = analysis.get("color", {})
    clip = _v(color.get("clipping"), {}) or {}
    mean = _v(color.get("mean_luminance"), 0.45) or 0.45
    key = _v(color.get("tonal_key"), "")
    if clip.get("highlights", 0) > 0.03:
        out.append({"severity": "major" if clip["highlights"] > 0.08 else "minor",
                    "issue": f"{clip['highlights']:.0%} of the frame is blown-out white.",
                    "fix": "Expose ⅔–1 stop lower or flag/diffuse the brightest source; protect windows and skies."})
    if clip.get("shadows", 0) > 0.12 and "low-key" not in key:
        out.append({"severity": "minor", "issue": f"{clip['shadows']:.0%} of the frame is crushed to black.",
                    "fix": "Add fill or lift exposure unless the darkness is intended."})
    if mean < 0.12:
        out.append({"severity": "major", "issue": "The footage is very dark overall.",
                    "fix": "Add light or open up 1–2 stops; dark footage gets noisy when brightened later."})
    for s in analysis.get("shots", []):
        n = s["index"] + 1
        roll = _v(s.get("composition", {}).get("horizon_roll_deg"))
        if roll is not None and 2.5 < abs(roll) < 7:
            out.append({"severity": "minor", "issue": f"Shot {n}: horizon is tilted about {abs(roll):.0f}° — reads as a mistake rather than a Dutch angle.",
                        "fix": "Level the camera, or commit to 10°+ if a Dutch angle is intended."})
        mv = s.get("movement", {})
        jitter = (mv.get("metrics") or {}).get("jitter", 0)
        if _v(mv.get("handheld")) and controls.get("movement_intensity", 2) <= 1 and jitter > 0.006:
            out.append({"severity": "minor", "issue": f"Shot {n}: visible handheld shake, but your brief asks for calm movement.",
                        "fix": "Use a tripod, gimbal or stabilise in post."})
    return out


def match(concept: dict, analysis: dict) -> dict:
    orig = {s["index"]: s for s in analysis.get("shots", [])}
    pts, total = 0.0, 0.0
    same, different = [], []
    for s in concept["shots"]:
        o = orig.get(s["source_shot_index"])
        if not o:
            continue
        n = s["index"] + 1
        # shot size (weight 3)
        osz = G.normalise_size(_v(o.get("shot_size")))
        size_known = _v(o.get("shot_size")) not in (None, "undetermined")
        if size_known:
            d = abs(G.SHOT_SIZES.index(osz) - G.SHOT_SIZES.index(s["size"]))
            pts += 3 * (1 if d == 0 else 0.5 if d == 1 else 0)
            total += 3
            (same if d <= 1 else different).append(f"Shot {n} size: {osz} vs {s['size']}")
        # angle (weight 2)
        oang = (_v(o.get("camera_angle")) or "").split(" (")[0]
        if oang and "undetermined" not in oang:
            ok = oang == s["angle"]
            pts += 2 * ok
            total += 2
            (same if ok else different).append(f"Shot {n} angle: {oang} vs {s['angle']}")
        # movement (weight 3)
        omv = _v(o.get("movement", {}).get("movement")) or ""
        if omv and "undetermined" not in omv:
            fo, fc = _move_family(omv), _move_family(s["movement"]["type"])
            ok = fo == fc
            pts += 3 * ok
            total += 3
            (same if ok else different).append(f"Shot {n} movement: {omv} vs {s['movement']['type']}")
    # lighting key (weight 3) and colour temperature (weight 2), whole clip
    tk = _v(analysis.get("color", {}).get("tonal_key"), "")
    if tk and concept["shots"]:
        ck = _key_of_ratio(concept["shots"][0]["lighting"]["ratio"])
        ok = (ck in tk) or (ck == "low-key" and "dark" in tk)
        pts += 3 * ok
        total += 3
        (same if ok else different).append(f"Lighting: {tk} vs {ck}")
    cast = ((_v(analysis.get("color", {}).get("color_temperature"), {}) or {}).get("cast") or "")
    direction = concept["color"]["direction"].lower()
    want = "cool" if "cool" in direction else "warm" if "warm" in direction else "neutral"
    if cast:
        ok = (want == "neutral" and cast in ("neutral", "warm", "cool")) or cast == want
        pts += 2 * ok
        total += 2
        (same if ok else different).append(f"Colour: {cast} cast vs {want} grade")
    score = round(100 * pts / total) if total else 0
    return {"score": score, "confidence": "low" if total < 8 else "medium" if total < 13 else "high",
            "already_matches": same, "differs": different}


def assess(analysis: dict, concepts: list[dict], controls: dict) -> dict:
    issues = technical_checks(analysis, controls)
    matches = [{"concept_id": c["id"], "name": c["data"]["name"], **match(c["data"], analysis)} for c in concepts]
    matches.sort(key=lambda m: -m["score"])
    best = matches[0] if matches else None
    major = [i for i in issues if i["severity"] == "major"]
    if best and best["score"] >= GOOD_THRESHOLD and not major:
        verdict = "good"
        headline = f"Your footage is good — it already works as “{best['name']}”."
        detail = ("You could shoot it this way and keep it. The other concepts are there if you want to explore a "
                  "different feel." + (" A few small fixes would make it stronger." if issues else ""))
    elif best and best["score"] >= GOOD_THRESHOLD:
        verdict = "good_with_fixes"
        headline = f"Your approach already matches “{best['name']}”, but there are technical problems."
        detail = "Keep the approach and fix the issues listed, or try one of the other concepts."
    else:
        verdict = "alternatives"
        headline = "Your footage takes a different approach from all of these concepts."
        detail = ("That isn't a judgement that it's wrong — each concept is an alternative way to shoot the scene. "
                  "Compare them with your original and keep what works.")
        if not issues:
            detail += " Technically, the footage looks sound."
    return {"verdict": verdict, "headline": headline, "detail": detail, "technical_issues": issues,
            "best_match": best, "matches": matches, "threshold": GOOD_THRESHOLD,
            "note": "Matching compares measured shot size, angle, movement, lighting key and colour cast. "
                    "It can't judge performance, story or taste — you decide what's good."}
