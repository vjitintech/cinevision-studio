"""Languages for the Director's review. Each language module mirrors director.texts (same codes); anything missing
falls back to English."""
from __future__ import annotations

import importlib

LANGUAGES = {
    "en": {"name": "English", "english_name": "English"},
    "hi": {"name": "हिन्दी", "english_name": "Hindi"},
    "te": {"name": "తెలుగు", "english_name": "Telugu"},
    "ta": {"name": "தமிழ்", "english_name": "Tamil"},
    "kn": {"name": "ಕನ್ನಡ", "english_name": "Kannada"},
    "ml": {"name": "മലയാളം", "english_name": "Malayalam"},
    "bn": {"name": "বাংলা", "english_name": "Bengali"},
    "mr": {"name": "मराठी", "english_name": "Marathi"},
    "es": {"name": "Español", "english_name": "Spanish"},
    "fr": {"name": "Français", "english_name": "French"},
    "de": {"name": "Deutsch", "english_name": "German"},
    "pt": {"name": "Português", "english_name": "Portuguese"},
    "ar": {"name": "العربية", "english_name": "Arabic", "rtl": True},
    "ja": {"name": "日本語", "english_name": "Japanese"},
    "ko": {"name": "한국어", "english_name": "Korean"},
    "zh": {"name": "中文", "english_name": "Chinese (Simplified)"},
}


def pack(lang: str) -> dict:
    from .. import texts as en
    base = {"SUMMARY": {"text": en.SUMMARY}, "DEPARTMENTS": en.DEPARTMENTS, "STATUS": en.STATUS, "VERDICT": en.VERDICT, "FINDINGS": en.FINDINGS,
            "PACE_ADVICE": en.PACE_ADVICE, "EDIT_OPTIONS": en.EDIT_OPTIONS, "EDIT_STEPS": en.EDIT_STEPS}
    if lang == "en" or lang not in LANGUAGES:
        return base
    try:
        mod = importlib.import_module(f".{lang}", __name__)
    except ModuleNotFoundError:
        return base
    out = {}
    tuples = getattr(mod, "F", {})
    findings_tr = {code: {k: v for k, v in zip(("obs", "sug", "why"), t) if v} for code, t in tuples.items()}
    for k, v in base.items():
        tr = findings_tr if k == "FINDINGS" else getattr(mod, k, {})
        if k == "FINDINGS":
            out[k] = {code: {**v[code], **tr.get(code, {})} for code in v}
        elif k == "EDIT_OPTIONS":
            out[k] = {code: {**v[code], **tr.get(code, {})} for code in v}
        else:
            out[k] = {**v, **tr}
    return out
