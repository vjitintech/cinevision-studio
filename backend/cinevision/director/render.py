"""Turn a stored review (codes + params) into text in the requested language."""
from __future__ import annotations

import re

from .i18n import LANGUAGES, pack
from .i18n import vocab

# params whose values are analysis vocabulary (translated term by term); others are numbers, colours or shot lists
VOCAB_PARAMS = {"kind", "mood", "pacing", "moves", "size", "sizes", "placement", "key", "contrast", "cast", "label"}
_PATTERNS: dict[str, tuple] = {}


def _tr_value(v, lang: str):
    if lang == "en" or lang not in vocab.V or not isinstance(v, str):
        return v
    if lang not in _PATTERNS:
        pairs = sorted(zip(vocab.TERMS, vocab.V[lang]), key=lambda p: -len(p[0]))
        rx = re.compile("|".join(r"(?<![\w-])" + re.escape(t) + r"(?![\w-])" for t, _ in pairs))
        _PATTERNS[lang] = (rx, dict(pairs))
    rx, table = _PATTERNS[lang]
    return rx.sub(lambda m: table[m.group(0)], v)


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
            params = {k: (_tr_value(v, lang) if k in VOCAB_PARAMS else v) for k, v in f["params"].items()}
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


def render_assembly(data: dict, lang: str) -> dict:
    lang = lang if lang in LANGUAGES else "en"
    P = pack(lang)
    A, C = P["ASSEMBLY"], P["CUES"]

    def params(x):
        p = dict(x.get("params", {}))
        if isinstance(p.get("cues"), str):
            p["cues"] = ", ".join(C.get(c.strip(), c.strip()) for c in p["cues"].split(",") if c.strip())
        return p

    def txt(items):
        return [{**x, "text": _fmt(A.get(x["code"], x["code"]), params(x))} for x in items]
    seq = []
    for s in data["sequence"]:
        seq.append({**s, "reasons": txt(s["reasons"]), "flags": txt(s["flags"]),
                    "transition_label": A.get(s["transition"], s["transition"]),
                    "transition_extra_label": A.get(s["transition_extra"]) if s.get("transition_extra") else None,
                    "transition_out_label": A.get(s["transition_out"]) if s.get("transition_out") else None})
    return {"language": lang, "rtl": bool(LANGUAGES[lang].get("rtl")), "beats": data["beats"], "sequence": seq,
            "rejected": [{**r, "flags": txt(r["flags"])} for r in data["rejected"]], "notes": txt(data["notes"]),
            "runtime_s": data["runtime_s"], "clip_count": data["clip_count"]}


def render_assembly_order(data: dict, lang: str) -> dict:
    """The owner's own order (assembly.engine.check_order): its sequence like render_assembly, plus each continuity
    note with its reason (text), its suggested fix (fix_text) and, for a set-aside clip, why (because_text)."""
    lang = lang if lang in LANGUAGES else "en"
    P = pack(lang)
    A, C = P["ASSEMBLY"], P["CUES"]
    base = render_assembly({"beats": [], "sequence": data.get("sequence", []), "rejected": [], "notes": [],
                            "runtime_s": data.get("runtime_s", 0), "clip_count": len(data.get("order", []))}, lang)

    def params(p):
        p = dict(p or {})
        for k in ("time_a", "time_b"):
            if isinstance(p.get(k), str):
                p[k] = C.get(p[k], p[k])
        return p
    notes = []
    for n in data.get("notes", []):
        item = {**n, "text": _fmt(A.get(n["code"], n["code"]), params(n.get("params")))}
        if n.get("fix"):
            item["fix_text"] = _fmt(A.get(n["fix"]["code"], n["fix"]["code"]), params(n["fix"].get("params")))
        if n.get("because"):
            item["because_text"] = _fmt(A.get(n["because"]["code"], n["because"]["code"]), params(n["because"].get("params")))
        notes.append(item)
    return {"language": lang, "rtl": base["rtl"], "sequence": base["sequence"], "runtime_s": data.get("runtime_s", 0),
            "notes": notes}
