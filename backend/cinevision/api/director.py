"""Director's review, language preference, local AI settings and hardware advice."""
from __future__ import annotations

import os
import platform
import subprocess
import sys

from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.routing import Route

from .. import audit, db
from ..adapters import localai
from ..director import render as RD
from ..director.i18n import LANGUAGES
from ..keys import keyring
from ..pipeline import stages
from ..web import ApiError, current, ok, owned_project, parse


def _lang(request: Request, uid: str) -> str:
    q = request.query_params.get("lang")
    return q if q in LANGUAGES else stages.user_language(uid)


async def get_review(request: Request):
    ctx = current(request)
    pid = request.path_params["pid"]
    owned_project(ctx, pid)
    row = db.one("SELECT * FROM director_reviews WHERE project_id=? AND owner_id=?", (pid, ctx.uid))
    if row is None:
        return ok({"ready": False})          # polled while jobs run; not an error
    k = keyring()
    lang = _lang(request, ctx.uid)
    data = k.dec_json(row["data_enc"], f"director_reviews:data:{row['id']}")
    ai_all = k.dec_json(row["ai_enc"], f"director_reviews:ai:{row['id']}") if row["ai_enc"] else {}
    out = RD.render(data, lang)
    out["ai"] = ai_all.get(lang)
    out["ai_languages"] = sorted(ai_all)
    out["ai_available"] = localai.available(ctx.uid)
    out["created_at"] = row["created_at"]
    out["ready"] = True
    return ok(out)


class ReviewBody(BaseModel):
    lang: str | None = Field(default=None, max_length=8)


async def rerun_review(request: Request):
    ctx = current(request)
    pid = request.path_params["pid"]
    owned_project(ctx, pid)
    body = await parse(request, ReviewBody)
    a = db.one("SELECT id FROM scene_analyses WHERE project_id=? AND owner_id=?", (pid, ctx.uid))
    if a is None:
        raise ApiError(409, "analysis_not_ready", "The review needs a finished analysis first.")
    lang = body.lang if body.lang in LANGUAGES else stages.user_language(ctx.uid)
    from .. import jobs
    job = stages.enqueue_review(ctx.uid, pid, a["id"], lang, force=True)
    return ok({"job": jobs.public(job)}, 202)


# ---------------------------------------------------------------- learn directing
async def learn(request: Request):
    """Lessons = the review's own craft explanations, grouped by department, plus what *your* videos most need."""
    import re
    from collections import Counter
    from ..director import review as RV
    from ..director.i18n import pack
    ctx = current(request)
    lang = _lang(request, ctx.uid)
    P = pack(lang)
    k = keyring()
    seen, problems = Counter(), Counter()
    rows = db.all_("SELECT id, data_enc FROM director_reviews WHERE owner_id=?", (ctx.uid,))
    for r in rows:
        data = k.dec_json(r["data_enc"], f"director_reviews:data:{r['id']}") or {}
        for d in data.get("departments", []):
            for f in d.get("findings", []):
                seen[f["code"]] += 1
                if f["status"] in ("improve", "problem"):
                    problems[f["code"]] += 1
    chapters = []
    for dep in RV.ORDER:
        lessons = []
        for code, t in P["FINDINGS"].items():
            prefix = {"story": "story.", "camera": "cam.", "composition": "comp.", "lighting": "light.", "colour": "col.",
                      "sound": "snd.", "editing": "edit.", "titles": "titles."}[dep]
            if not code.startswith(prefix) or not t.get("why"):
                continue
            blank = lambda s: re.sub(r"\{\w+\}", "…", s) if s else s  # noqa: E731
            lessons.append({"code": code, "symptom": blank(t.get("obs")), "why": blank(t.get("why")),
                            "do": blank(t.get("sug")) if t.get("sug") and "{advice}" not in t.get("sug", "") else None,
                            "seen_in_your_videos": seen.get(code, 0), "to_practise": problems.get(code, 0)})
        chapters.append({"id": dep, "title": P["DEPARTMENTS"][dep], "exercise": P["EXERCISES"].get(dep), "lessons": lessons,
                         "to_practise": sum(l["to_practise"] for l in lessons)})
    focus = [c["id"] for c in sorted(chapters, key=lambda c: -c["to_practise"]) if c["to_practise"]][:3]
    return ok({"language": lang, "chapters": chapters, "focus": focus, "reviews": len(rows)})


# ---------------------------------------------------------------- language
async def get_prefs(request: Request):
    ctx = current(request)
    return ok({"language": stages.user_language(ctx.uid),
               "languages": [{"code": c, **v} for c, v in LANGUAGES.items()]})


class PrefsBody(BaseModel):
    language: str = Field(max_length=8)


async def put_prefs(request: Request):
    ctx = current(request)
    body = await parse(request, PrefsBody)
    if body.language not in LANGUAGES:
        raise ApiError(422, "validation_error", "Unsupported language.")
    db.run("INSERT INTO user_prefs(user_id,language) VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET language=excluded.language",
           (ctx.uid, body.language))
    return ok({"language": body.language})


# ---------------------------------------------------------------- local AI
def _is_owner(uid: str) -> bool:
    from ..roles import is_admin  # the owner or an admin the owner appointed
    return is_admin(uid)


async def get_local_ai(request: Request):
    ctx = current(request)
    st = localai.status(ctx.uid)
    st["can_edit"] = _is_owner(ctx.uid)
    return ok(st)


class LocalAIBody(BaseModel):
    enabled: bool = True
    provider: str = Field(default="colibri", max_length=20)
    url: str = Field(max_length=300)
    model: str = Field(default="", max_length=200)
    vision_model: str = Field(default="", max_length=200)


async def put_local_ai(request: Request):
    ctx = current(request)
    if not _is_owner(ctx.uid):
        raise ApiError(403, "forbidden", "Only the owner or an admin can change the AI settings.")
    body = await parse(request, LocalAIBody)
    try:
        localai.set_config(body.model_dump())
    except localai.LocalAIError as e:
        raise ApiError(422, "validation_error", str(e)) from None
    audit.record("settings.local_ai", user_id=ctx.uid, target_type="app", target_id="local_ai", ip=ctx.ip,
                 details={"enabled": body.enabled, "provider": body.provider, "loopback": localai.registry.is_loopback_url(body.url)})
    st = localai.status(ctx.uid)
    st["can_edit"] = True
    return ok(st)


# ---------------------------------------------------------------- hardware advice
def _ram_bytes() -> int | None:
    try:
        if sys.platform == "win32":
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MS()
            m.dwLength = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return int(m.ullTotalPhys)
        if sys.platform == "darwin":
            return int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5).stdout)
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
    except Exception:  # noqa: BLE001
        return None
    return None


def _nvidia() -> list[dict]:
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=5)
        out = []
        for line in r.stdout.strip().splitlines():
            name, mem = [x.strip() for x in line.split(",")[:2]]
            out.append({"name": name, "vram_gb": round(int(mem) / 1024, 1)})
        return out
    except Exception:  # noqa: BLE001
        return []


def recommend(ram_gb: float | None, gpus: list[dict]) -> list[dict]:
    """Model advice for colibri/Ollama, from colibri's published RAM figures (min / good)."""
    vram = max((g["vram_gb"] for g in gpus), default=0)
    recs = []
    if ram_gb is None:
        return [{"level": "unknown", "text": "Couldn't read this computer's memory. 16 GB RAM or more is recommended."}]
    if ram_gb >= 20:
        recs.append({"level": "best", "role": "director brain (text)", "model": "Qwen3.6-35B-A3B",
                     "colibri": "qwen36-35b", "download_gb": 23,
                     "text": "Runs from RAM on this computer (needs 10 GB, best from 20 GB)."})
    elif ram_gb >= 10:
        recs.append({"level": "ok", "role": "director brain (text)", "model": "Qwen3.6-35B-A3B",
                     "colibri": "qwen36-35b", "download_gb": 23,
                     "text": "Will run, but slowly (this PC has under 20 GB RAM). Close other programs while it works."})
    if ram_gb >= 6:
        recs.append({"level": "best" if ram_gb >= 8 else "ok", "role": "eyes (looks at frames)", "model": "LFM3-VL-2B",
                     "colibri": "LFM3-VL-2B GGUF (see colibri docs/qwen36.md)", "ollama": "qwen2.5vl:3b", "download_gb": 2,
                     "text": "Small and fast vision model: describes what is in the frames."})
    if ram_gb >= 24 or vram >= 16:
        recs.append({"level": "better", "role": "eyes + brain in one", "model": "Qwen3.8-27B (vision)",
                     "ollama": "qwen2.5vl:32b" if vram >= 24 else "qwen2.5vl:7b", "download_gb": 17,
                     "text": "Stronger visual judgement; a GPU with 16 GB+ makes it fast."})
    if ram_gb < 10:
        recs.append({"level": "too_small", "text": f"This computer has {ram_gb:.0f} GB RAM. Large models won't run; the built-in "
                                                   "analyser still gives full reviews. Use a small vision model only."})
    return recs


async def hardware(request: Request):
    current(request)
    ram = _ram_bytes()
    gpus = _nvidia()
    ram_gb = round(ram / 1024 ** 3, 1) if ram else None
    return ok({"os": platform.system(), "cpu_count": os.cpu_count(), "ram_gb": ram_gb, "gpus": gpus,
               "recommendations": recommend(ram_gb, gpus),
               "guide": "https://github.com/JustVugg/colibri/blob/main/docs/AI_SETUP.md"})


routes = [
    Route("/api/projects/{pid}/review", get_review, methods=["GET"]),
    Route("/api/projects/{pid}/review", rerun_review, methods=["POST"]),
    Route("/api/learn", learn, methods=["GET"]),
    Route("/api/me/preferences", get_prefs, methods=["GET"]),
    Route("/api/me/preferences", put_prefs, methods=["PUT"]),
    Route("/api/ai/local", get_local_ai, methods=["GET"]),
    Route("/api/ai/local", put_local_ai, methods=["PUT"]),
    Route("/api/system/hardware", hardware, methods=["GET"]),
]
