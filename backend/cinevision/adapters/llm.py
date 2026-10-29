"""Optional language-model adapters (Ollama local, or an OpenAI-compatible external endpoint).

Used only to translate refinement commands the rule parser didn't understand into the *same* closed set of
operations. The model never writes concept content directly, and only text is sent (never footage or frames).
"""
from __future__ import annotations

import json
import re
import urllib.request
from urllib.parse import urlparse

from ..config import settings
from . import registry


class AdapterUnavailable(Exception):
    pass


def _post(url: str, payload: dict, headers: dict, timeout: int = 60) -> dict:
    if urlparse(url).scheme not in ("http", "https"):
        raise AdapterUnavailable("adapter URL must be http(s)")
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", **headers},
                                 method="POST")
    # no redirects to other hosts: urllib follows redirects for POST→GET only on 303; acceptable for configured endpoints
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 — operator-configured endpoint only
        return json.loads(r.read(2_000_000))


def pick(uid: str) -> str | None:
    for aid in ("ollama", "external-llm"):
        st = registry.adapter_status(aid, uid)
        if st and st["status"] == "available":
            return aid
    return None


SYSTEM = ("You map a film director's refinement request onto a fixed list of operations. Reply ONLY with JSON: "
          '{"ops":[{"op":"<name>","shot":<1-based int or null>,"n":<int or null>}]}. '
          "Use only these op names: {ops}. If nothing applies, reply {{\"ops\":[]}}.")


def interpret(uid: str, command: str, op_names: list[str], shot_count: int) -> tuple[list[dict], str]:
    aid = pick(uid)
    if aid is None:
        raise AdapterUnavailable("no language model configured")
    s = settings()
    system = SYSTEM.replace("{ops}", ", ".join(op_names)).replace("{{", "{").replace("}}", "}")
    user = f"The concept has {shot_count} shots. Request: {command[:500]}"
    if aid == "ollama":
        if s.mode == "local" and not registry.is_loopback_url(s.ollama_url) and "ollama" not in registry._consented(uid):
            raise AdapterUnavailable("non-local Ollama requires consent")
        resp = _post(s.ollama_url.rstrip("/") + "/api/chat",
                     {"model": s.ollama_model, "stream": False, "format": "json",
                      "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}, {})
        text = resp.get("message", {}).get("content", "")
    else:
        resp = _post(s.external_llm_url.rstrip("/") + "/v1/chat/completions",
                     {"model": s.external_llm_model, "temperature": 0,
                      "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]},
                     {"Authorization": f"Bearer {s.external_llm_key}"})
        text = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return [], aid
    try:
        ops = json.loads(m.group(0)).get("ops", [])
    except json.JSONDecodeError:
        return [], aid
    clean = []
    for o in ops[:8]:
        if isinstance(o, dict) and o.get("op") in op_names:
            shot = o.get("shot")
            shot = shot - 1 if isinstance(shot, int) and 1 <= shot <= shot_count else None
            n = o.get("n") if isinstance(o.get("n"), int) and 1 <= o.get("n") <= 5 else 3
            clean.append({"op": o["op"], "shot": shot, "n": n})
    return clean, aid
