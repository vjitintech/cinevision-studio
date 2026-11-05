"""Turn a stored review (codes + params) into text in the requested language."""
from __future__ import annotations

from .i18n import LANGUAGES, pack


class _Safe(dict):
    def __missing__(self, k):
        return "{" + k + "}"


def _fmt(t: str | None, params: dict) -> str | None:
    return t.format_map(_Safe(params)) if t else None


def render(review: dict, lang: str) -> dict:
    lang = lang if lang in LANGUAGES else "en"
    P = pack(lang)
    deps = []
    for d in review["departments"]:
        items = []
        for f in d["findings"]:
            tpl = P["FINDINGS"].get(f["code"], {})
            params = dict(f["params"])
            if "advice_key" in params:
                params["advice"] = P["PACE_ADVICE"].get(params["advice_key"], "")
            items.append({**f, "observation": _fmt(tpl.get("obs"), params), "suggestion": _fmt(tpl.get("sug"), params),
                          "why": _fmt(tpl.get("why"), params)})
        deps.append({"id": d["id"], "title": P["DEPARTMENTS"].get(d["id"], d["id"]), "status": d["status"],
                     "status_label": P["STATUS"][d["status"]], "findings": items,
                     "score": review.get("scores", {}).get(d["id"])})
    edits = []
    for o in review.get("edit_options", []):
        meta = P["EDIT_OPTIONS"].get(o["id"], {})
        edits.append({"id": o["id"], "name": meta.get("name", o["id"]), "idea": meta.get("idea", ""),
                      "best_for": meta.get("best_for", ""),
                      "steps": [_fmt(P["EDIT_STEPS"].get(k, k), p) for k, p in o["steps"]]})
    counts = {k: sum(d["status"] == k for d in review["departments"]) for k in ("good", "improve", "problem")}
    return {"language": lang, "rtl": bool(LANGUAGES[lang].get("rtl")), "verdict": review["verdict"],
            "verdict_label": P["VERDICT"].get(review["verdict"], ""), "summary": _fmt(P["SUMMARY"]["text"], counts),
            "counts": counts, "departments": deps, "edit_options": edits,
            "alternatives": review.get("alternatives", [])}
