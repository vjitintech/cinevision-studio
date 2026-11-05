"""Free local AI through any OpenAI-compatible server on this machine: colibri (`coli start`), Ollama (`/v1`),
LM Studio, llama.cpp server.

Settings are stored in the database (set from Settings → Local AI) and can be preset with environment variables
CINEVISION_LOCAL_AI_URL / _MODEL / _VISION_MODEL. Only loopback addresses are used without consent: a server on
another machine needs the owner's external-AI consent for provider "local-ai", because frames would leave this PC.
"""
from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request
from urllib.parse import urlparse

import cv2
import numpy as np

from .. import db
from . import registry

PRESETS = {
    "colibri": {"label": "colibri", "url": "http://127.0.0.1:8800/v1",
                "help": "Run START-HERE.bat in the colibri folder, then: py -3 c\\coli start --port 8800 --background --no-browser"},
    "ollama": {"label": "Ollama", "url": "http://127.0.0.1:11434/v1",
               "help": "Install Ollama, then: ollama pull qwen2.5vl:7b"},
    "lmstudio": {"label": "LM Studio", "url": "http://127.0.0.1:1234/v1", "help": "Start the local server in LM Studio."},
}


class LocalAIError(Exception):
    pass


def _ensure_table():
    db.conn().execute("CREATE TABLE IF NOT EXISTS app_config (key TEXT PRIMARY KEY, value TEXT NOT NULL)")


def get_config() -> dict:
    _ensure_table()
    row = db.one("SELECT value FROM app_config WHERE key='local_ai'")
    cfg = json.loads(row["value"]) if row else {}
    return {
        "enabled": cfg.get("enabled", bool(os.environ.get("CINEVISION_LOCAL_AI_URL"))),
        "provider": cfg.get("provider", "colibri"),
        "url": cfg.get("url") or os.environ.get("CINEVISION_LOCAL_AI_URL") or PRESETS["colibri"]["url"],
        "model": cfg.get("model") or os.environ.get("CINEVISION_LOCAL_AI_MODEL", ""),
        "vision_model": cfg.get("vision_model") or os.environ.get("CINEVISION_LOCAL_AI_VISION_MODEL", ""),
    }


def set_config(cfg: dict) -> dict:
    _ensure_table()
    url = (cfg.get("url") or "").strip().rstrip("/")
    if urlparse(url).scheme not in ("http", "https") or not urlparse(url).hostname:
        raise LocalAIError("Enter an address like http://127.0.0.1:8800/v1")
    clean = {"enabled": bool(cfg.get("enabled")), "provider": str(cfg.get("provider", "custom"))[:20], "url": url[:300],
             "model": str(cfg.get("model", "")).strip()[:200], "vision_model": str(cfg.get("vision_model", "")).strip()[:200]}
    db.run("INSERT INTO app_config(key,value) VALUES ('local_ai',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
           (json.dumps(clean),))
    return get_config()


def allowed_for(uid: str | None, cfg: dict) -> bool:
    return registry.is_loopback_url(cfg["url"]) or "local-ai" in registry._consented(uid)


def _request(url: str, payload: dict | None, timeout: int) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET",
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer local"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - operator-configured local endpoint
            return json.loads(r.read(5_000_000))
    except urllib.error.HTTPError as e:
        raise LocalAIError(f"The AI server answered {e.code}.") from None
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        raise LocalAIError("Could not reach the AI server. Is it running?") from None
    except json.JSONDecodeError:
        raise LocalAIError("The AI server sent an unreadable reply.") from None


def list_models(cfg: dict | None = None) -> list[str]:
    cfg = cfg or get_config()
    r = _request(cfg["url"] + "/models", None, 10)
    return [m.get("id") for m in r.get("data", []) if isinstance(m, dict) and m.get("id")]


def status(uid: str | None) -> dict:
    cfg = get_config()
    out = {"config": cfg, "presets": PRESETS, "reachable": False, "models": [], "ready": False, "message": ""}
    if not cfg["enabled"]:
        out["message"] = "Local AI is switched off. The built-in analyser is used."
        return out
    if not allowed_for(uid, cfg):
        out["message"] = "This AI server is not on this computer. Give consent for 'local-ai' under Privacy first."
        return out
    try:
        out["models"] = list_models(cfg)
        out["reachable"] = True
    except LocalAIError as e:
        out["message"] = str(e)
        return out
    model = cfg["model"] or (out["models"][0] if out["models"] else "")
    out["ready"] = bool(model)
    out["message"] = f"Connected. Using {model}." if model else "Connected, but no model is loaded."
    return out


def available(uid: str | None) -> bool:
    cfg = get_config()
    return bool(cfg["enabled"] and allowed_for(uid, cfg))


def _encode(img: np.ndarray, width=640) -> str:
    h, w = img.shape[:2]
    if w > width:
        img = cv2.resize(img, (width, int(width * h / w)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 82])
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()


def chat_json(uid: str | None, system: str, user: str, images: list[np.ndarray] | None = None,
              timeout: int = 900, vision: bool = False) -> tuple[dict, str]:
    """One chat call that must return a JSON object. Returns (object, model id)."""
    cfg = get_config()
    if not available(uid):
        raise LocalAIError("Local AI is not enabled.")
    model = (cfg["vision_model"] if vision and cfg["vision_model"] else cfg["model"])
    if not model:
        models = list_models(cfg)
        if not models:
            raise LocalAIError("No model is loaded in the AI server.")
        model = models[0]
    content: list | str = user
    if images:
        content = [{"type": "text", "text": user}] + [{"type": "image_url", "image_url": {"url": _encode(i)}} for i in images]
    payload = {"model": model, "temperature": 0.3, "max_tokens": 2500,
               "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
               "response_format": {"type": "json_object"}}
    try:
        r = _request(cfg["url"] + "/chat/completions", payload, timeout)
    except LocalAIError:
        payload.pop("response_format")       # some servers reject response_format; ask again without it
        r = _request(cfg["url"] + "/chat/completions", payload, timeout)
    text = (r.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)        # reasoning models
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise LocalAIError("The model did not return JSON.")
    try:
        return json.loads(m.group(0)), model
    except json.JSONDecodeError:
        raise LocalAIError("The model returned malformed JSON.") from None
