"""Model/service adapter registry.

Each service interface from the spec has a slot. Built-in local engines fill most slots; optional adapters
are reported honestly as `not_configured` until an operator configures them, and external ones additionally
need the owner's per-provider consent (`needs_consent`) before anything is sent.
"""
from __future__ import annotations

import ipaddress
import json
from urllib.parse import urlparse

from .. import db
from ..config import settings

SERVICES = {
    "ingest": "Video ingestion & transcoding",
    "scene_analysis": "Scene & frame analysis",
    "motion_tracking": "Subject & motion tracking",
    "cinematography": "Cinematography recommendation",
    "storyboard": "Storyboard generation",
    "image_generation": "Image generation",
    "video_generation": "Video generation",
    "scene3d": "3D scene & camera-path generation",
    "reports": "Report & shot-list generation",
    "language": "Language model (free-text refinement)",
}


def is_loopback_url(url: str | None) -> bool:
    if not url:
        return False
    host = urlparse(url).hostname or ""
    if host in ("localhost", "ollama"):  # "ollama" = the optional compose service on the private network
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _consented(uid: str | None) -> set[str]:
    if not uid:
        return set()
    r = db.one("SELECT external_ai_consent, consented_providers FROM privacy_settings WHERE user_id=?", (uid,))
    if not r or not r["external_ai_consent"]:
        return set()
    return set(json.loads(r["consented_providers"]))


def list_adapters(uid: str | None = None) -> list[dict]:
    s = settings()
    consent = _consented(uid)

    def builtin(aid, service, name, version, note):
        return {"id": aid, "service": service, "service_label": SERVICES[service], "name": name, "version": version,
                "external": False, "status": "available", "data_flow": "Runs inside this deployment. No data leaves it.",
                "note": note}

    out = [
        builtin("ffmpeg-ingest", "ingest", "FFmpeg proxy + keyframes", "1", "H.264 720p browser proxy and ~2 fps keyframes."),
        builtin("cv-scene", "scene_analysis", "OpenCV scene analysis", "1",
                "Colour, exposure, composition, environment heuristics. Estimates are labelled."),
        builtin("cv-motion", "motion_tracking", "OpenCV motion & subjects", "1",
                "LK optical flow + RANSAC camera model; Haar/HOG subject detection."),
        builtin("rules-cinematography", "cinematography", "Archetype concept engine", "1",
                "Deterministic, explainable recommendations adapted to the analysis."),
        builtin("procedural-storyboard", "storyboard", "Procedural storyboard & animatic", "1",
                "Panels drawn from the 3D proxy projection; not generated imagery."),
        builtin("proxy-previs", "scene3d", "Proxy 3D previs", "1",
                "Subjects as proxies placed from estimated positions; not a photogrammetric reconstruction."),
        builtin("report-shotlist", "reports", "Shot list & PDF/CSV/JSON export", "1", "User-initiated downloads only."),
    ]

    # Local LLM via Ollama (optional)
    if s.ollama_url:
        local = is_loopback_url(s.ollama_url)
        out.append({"id": "ollama", "service": "language", "service_label": SERVICES["language"],
                    "name": f"Ollama ({s.ollama_model or 'model not set'})", "version": "1", "external": not local,
                    "status": ("available" if s.ollama_model else "not_configured") if local or "ollama" in consent
                    else "needs_consent",
                    "data_flow": "Sends concept text and your refinement command (never footage or frames) to "
                                 f"{urlparse(s.ollama_url).hostname}.",
                    "note": "Interprets free-text refinement commands the built-in parser does not recognise."})
    else:
        out.append({"id": "ollama", "service": "language", "service_label": SERVICES["language"], "name": "Ollama (local LLM)",
                    "version": "1", "external": False, "status": "not_configured",
                    "data_flow": "Would send concept text only, to a model on this machine.",
                    "note": "Set CINEVISION_OLLAMA_URL and CINEVISION_OLLAMA_MODEL to enable."})

    # Local vision-language model via Ollama (optional, free)
    vision_local = bool(s.ollama_url) and is_loopback_url(s.ollama_url)
    if s.ollama_url and s.ollama_vision_model:
        vstatus = "available" if vision_local or "ollama-vision" in consent else "needs_consent"
    else:
        vstatus = "not_configured"
    out.append({"id": "ollama-vision", "service": "scene_analysis", "service_label": SERVICES["scene_analysis"],
                "name": f"Ollama vision ({s.ollama_vision_model})" if s.ollama_vision_model else "Ollama vision model (free, local)",
                "version": "1", "external": bool(s.ollama_url) and not vision_local, "status": vstatus,
                "data_flow": ("Sends up to 4 downscaled keyframes to the Ollama server on this machine. Nothing leaves it."
                              if vision_local or not s.ollama_url else
                              f"Sends up to 4 downscaled keyframes to {urlparse(s.ollama_url).hostname}."),
                "note": "Adds AI scene notes (setting, action, mood, strengths, improvements) and nudges concept ranking. "
                        "Set CINEVISION_OLLAMA_URL and CINEVISION_OLLAMA_VISION_MODEL (e.g. qwen2.5vl:7b) to enable."})

    out.append({"id": "external-llm", "service": "language", "service_label": SERVICES["language"],
                "name": "External OpenAI-compatible LLM", "version": "1", "external": True,
                "status": "not_configured" if not (s.external_llm_url and s.external_llm_model and s.external_llm_key)
                else ("available" if "external-llm" in consent else "needs_consent"),
                "data_flow": "Sends concept text and your refinement command (never footage or frames) to "
                             f"{urlparse(s.external_llm_url).hostname if s.external_llm_url else 'the configured provider'}. "
                             "Retention and training terms are the provider's; review them before consenting.",
                "note": "Disabled by default."})
    for aid, service, name, url in (("image-gen", "image_generation", "AI concept stills", s.image_gen_url),
                                    ("video-gen", "video_generation", "AI concept motion preview", s.video_gen_url)):
        out.append({"id": aid, "service": service, "service_label": SERVICES[service], "name": name, "version": "0",
                    "external": True, "status": "not_configured",
                    "data_flow": "Would send a text prompt and, only if you choose, one reference keyframe.",
                    "note": "Interface defined; no provider adapter ships with this build"
                            + (" (an endpoint is set but no compatible adapter is installed)." if url else ".")})
    return out


def adapter_status(adapter_id: str, uid: str | None) -> dict | None:
    return next((a for a in list_adapters(uid) if a["id"] == adapter_id), None)
