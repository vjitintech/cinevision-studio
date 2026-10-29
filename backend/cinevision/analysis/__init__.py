"""Scene analysis. Every reported value is wrapped as

    {"value": ..., "confidence": 0..1, "basis": "observed" | "estimated" | "heuristic", "note": "..."}

* observed  — measured directly from pixels/metadata (e.g. duration, palette, luminance)
* estimated — inferred with a geometric/statistical model that can be wrong (e.g. camera pan speed)
* heuristic — rule-of-thumb interpretation (e.g. time of day, mood); treat as a suggestion
"""


def tag(value, confidence: float, basis: str, note: str | None = None) -> dict:
    out = {"value": value, "confidence": round(float(max(0.0, min(1.0, confidence))), 2), "basis": basis}
    if note:
        out["note"] = note
    return out
