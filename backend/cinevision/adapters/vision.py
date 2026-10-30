"""Optional, free, local vision-language model (via Ollama) that looks at a few keyframes and describes the scene.

Why local: free cloud tiers (e.g. the Gemini API's unpaid tier) allow the provider to use submitted content to
improve their products and let human reviewers read it — incompatible with "footage is never used for training".
A model running in Ollama on this machine is free and keeps frames inside the deployment.

The model's output is stored as clearly-labelled *heuristic* notes and used only as a weak extra signal when
ranking concepts. It never overwrites measured values, and it is told not to identify anyone.
"""
from __future__ import annotations

import base64
import json
import re
from urllib.parse import urlparse

import cv2
import numpy as np

from ..analysis import tag
from ..config import settings
from . import registry
from .llm import AdapterUnavailable, _post

PROMPT = """You are assisting a film director. Look at these {n} frames from one short scene (in time order).
Describe only what is visible. Do NOT identify or name any real person, actor or celebrity, and do not guess
anyone's age, ethnicity or other personal traits. Reply with ONLY this JSON:
{{"setting": "<interior/exterior and place type>",
  "time_of_day": "<day/night/dawn/dusk/unclear>",
  "subjects": ["<short neutral description and what they are doing>"],
  "action": "<one sentence on what happens>",
  "mood": ["<up to 3 single words>"],
  "genre_hints": ["<up to 3 genres this could belong to>"],
  "lighting": "<one sentence: direction, hardness, colour>",
  "strengths": ["<up to 3 things that already work cinematically>"],
  "improvements": ["<up to 3 concrete camera/lighting/framing suggestions>"]}}"""


def available(uid: str | None) -> bool:
    st = registry.adapter_status("ollama-vision", uid)
    return bool(st and st["status"] == "available")


def _encode(img: np.ndarray) -> str:
    h, w = img.shape[:2]
    if w > 768:
        img = cv2.resize(img, (768, int(768 * h / w)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return base64.b64encode(buf.tobytes()).decode()


def _clean_list(v, n=3, maxlen=120) -> list[str]:
    if not isinstance(v, list):
        return []
    return [str(x)[:maxlen] for x in v if isinstance(x, (str, int, float))][:n]


def describe(uid: str, frames: list[np.ndarray]) -> dict:
    s = settings()
    if not available(uid):
        raise AdapterUnavailable("vision model not configured")
    url = s.ollama_url.rstrip("/") + "/api/chat"
    if urlparse(url).hostname not in ("localhost", "127.0.0.1", "::1", "ollama") and "ollama-vision" not in registry._consented(uid):
        raise AdapterUnavailable("non-local vision model requires consent")
    pick = frames[:: max(1, len(frames) // 4)][:4]
    resp = _post(url, {"model": s.ollama_vision_model, "stream": False, "format": "json", "options": {"temperature": 0.2},
                       "messages": [{"role": "user", "content": PROMPT.format(n=len(pick)), "images": [_encode(f) for f in pick]}]},
                 {}, timeout=300)
    text = resp.get("message", {}).get("content", "")
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise AdapterUnavailable("vision model returned no JSON")
    raw = json.loads(m.group(0))
    notes = {
        "setting": str(raw.get("setting", ""))[:120],
        "time_of_day": str(raw.get("time_of_day", ""))[:40],
        "subjects": _clean_list(raw.get("subjects"), 4, 160),
        "action": str(raw.get("action", ""))[:240],
        "mood": _clean_list(raw.get("mood"), 3, 30),
        "genre_hints": _clean_list(raw.get("genre_hints"), 3, 30),
        "lighting": str(raw.get("lighting", ""))[:240],
        "strengths": _clean_list(raw.get("strengths"), 3, 200),
        "improvements": _clean_list(raw.get("improvements"), 3, 200),
    }
    return {
        "model": f"ollama:{s.ollama_vision_model}",
        "frames_sent": len(pick),
        "notes": tag(notes, 0.35, "heuristic", "Written by a local vision-language model from a few keyframes. "
                     "It can misread scenes; measured values elsewhere take precedence."),
    }
