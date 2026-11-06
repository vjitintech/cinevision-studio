"""Dialogue: optional speech-to-text, plus measurements that need to know *when* people speak.

Engines, tried in order, all on this computer:
1. `faster_whisper` (pip install faster-whisper — see requirements-optional.txt), model `CINEVISION_WHISPER_MODEL`
   (default "small"; a model name or a local folder), device chosen automatically.
2. The owner's local AI server, when local AI is switched on and its address is loopback (127.0.0.1 / localhost):
   its OpenAI-compatible `/v1/audio/transcriptions` endpoint (speaches, LM Studio, whisper.cpp-style servers).

The sound track is converted to 16 kHz mono WAV with FFmpeg into the job's private temporary folder and deleted
afterwards. The transcript is user data: it is returned only inside the analysis (stored encrypted) and is never
logged. Without an engine everything else still works and the result says how to enable it.

Measurements (labelled estimated): words per minute, dialogue buried under music/effects (little level drop between
words while the bed is loud), long dead air between lines, very fast/slow delivery, hard-to-make-out lines (low
recogniser confidence).
"""
from __future__ import annotations

import io
import ipaddress
import json
import logging
import os
import tempfile
import time
import uuid
import wave
from urllib.parse import urlparse

import numpy as np

from . import tag

log = logging.getLogger("cinevision.analysis")

SR = 16000
FRAME = 320                    # 20 ms envelope frames
CHUNK_S = 600                  # HTTP engines get the audio in 10-minute pieces
NO_WORD_SPACES = {"ja", "zh", "th", "lo", "my", "km", "bo"}
FAST_WPM, SLOW_WPM = 210, 75
DEAD_AIR_S = 6.0
NOT_AVAILABLE = ("Speech to text is not available. Install faster-whisper (see requirements-optional.txt), or switch on "
                 "a local AI server with speech recognition in Settings → AI & language.")

_models: dict = {}


class EngineError(Exception):
    pass


# ---------------------------------------------------------------- engines

def _faster_whisper():
    try:
        import faster_whisper  # noqa: F401 - optional dependency
        return faster_whisper
    except Exception:  # noqa: BLE001 - missing or broken install: not available
        return None


def _model_name() -> str:
    try:
        from ..config import settings
        return settings().whisper_model or "small"
    except Exception:  # noqa: BLE001
        return os.environ.get("CINEVISION_WHISPER_MODEL", "small")


def _strict_loopback(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _local_server() -> dict | None:
    try:
        from ..adapters import localai
        from ..config import settings
        if not settings().db_path.exists():           # no app database (e.g. a bare analysis run): no settings
            return None
        cfg = localai.get_config()
    except Exception:  # noqa: BLE001 - no database / settings: not available
        return None
    if not cfg.get("enabled") or not _strict_loopback(cfg.get("url") or ""):
        return None
    return cfg


def available_engine() -> str | None:
    if _faster_whisper() is not None:
        return "faster-whisper"
    if _local_server() is not None:
        return "local-server"
    return None


def _transcribe_whisper(wav: str, deadline: float) -> dict:
    fw = _faster_whisper()
    name = _model_name()
    if name not in _models:
        _models[name] = fw.WhisperModel(name, device="auto", compute_type="default")
    model = _models[name]
    segments, info = model.transcribe(wav, vad_filter=True, beam_size=1, condition_on_previous_text=False)
    segs, partial = [], False
    for s in segments:                                    # lazy generator: decoding happens while iterating
        segs.append({"start_s": float(s.start), "end_s": float(s.end), "text": (s.text or "").strip(),
                     "avg_logprob": getattr(s, "avg_logprob", None), "no_speech_prob": getattr(s, "no_speech_prob", None)})
        if time.monotonic() > deadline:
            partial = True
            break
    return {"engine": f"faster-whisper ({name})", "language": getattr(info, "language", None),
            "language_probability": getattr(info, "language_probability", None), "segments": segs, "partial": partial}


def _multipart(fields: dict, file_bytes: bytes) -> tuple[bytes, str]:
    b = uuid.uuid4().hex
    out = io.BytesIO()
    for k, v in fields.items():
        out.write(f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    out.write(f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"audio.wav\"\r\n"
              "Content-Type: audio/wav\r\n\r\n".encode())
    out.write(file_bytes)
    out.write(f"\r\n--{b}--\r\n".encode())
    return out.getvalue(), f"multipart/form-data; boundary={b}"


def _post_audio(url: str, wav_bytes: bytes, model: str, timeout: int) -> dict:
    import urllib.error
    import urllib.request
    body, ctype = _multipart({"model": model, "response_format": "verbose_json", "temperature": "0",
                              "timestamp_granularities[]": "segment"}, wav_bytes)
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": ctype, "Authorization": "Bearer local"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - loopback server the owner configured
            raw = r.read(50_000_000)
    except urllib.error.HTTPError as e:
        raise EngineError(f"the AI server answered {e.code} for speech recognition") from None
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        raise EngineError("could not reach the AI server for speech recognition") from None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        text = raw.decode("utf-8", "replace").strip()     # response_format ignored: plain text
        return {"text": text}


def _wav_bytes(pcm: np.ndarray) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def _transcribe_server(wav: str, cfg: dict, deadline: float) -> dict:
    base = (cfg.get("url") or "").rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    url = base + "/audio/transcriptions"
    model = _model_name()
    segs, lang, partial = [], None, False
    with wave.open(wav, "rb") as w:
        total = w.getnframes()
        pos = 0
        while pos < total:
            if time.monotonic() > deadline:
                partial = True
                break
            n = min(CHUNK_S * SR, total - pos)
            pcm = np.frombuffer(w.readframes(n), np.int16)
            off = pos / SR
            r = _post_audio(url, _wav_bytes(pcm), model, timeout=max(120, int(n / SR * 2)))
            lang = lang or r.get("language")
            got = r.get("segments")
            if isinstance(got, list) and got:
                for s in got:
                    if not isinstance(s, dict):
                        continue
                    try:
                        segs.append({"start_s": off + float(s.get("start", 0)), "end_s": off + float(s.get("end", 0)),
                                     "text": str(s.get("text", "")).strip(),
                                     "avg_logprob": s.get("avg_logprob"), "no_speech_prob": s.get("no_speech_prob")})
                    except (TypeError, ValueError):
                        continue
            elif str(r.get("text", "")).strip():
                segs.append({"start_s": off, "end_s": off + n / SR, "text": str(r["text"]).strip(),
                             "avg_logprob": None, "no_speech_prob": None, "coarse": True})
            pos += n
    if isinstance(lang, str) and len(lang) > 3:                # some servers send "english"
        lang = {"english": "en", "hindi": "hi", "spanish": "es", "french": "fr", "german": "de"}.get(lang.lower(), lang)
    return {"engine": "local AI server", "language": lang, "language_probability": None, "segments": segs,
            "partial": partial}


# ---------------------------------------------------------------- audio helpers

def extract_wav(path: str, duration: float, out_path: str) -> bool:
    from .audio import _ffmpeg
    res = _ffmpeg(["-loglevel", "error", "-y", "-i", f"file:{path}", "-vn", "-map", "0:a:0?", "-ac", "1", "-ar", str(SR),
                   "-c:a", "pcm_s16le", "-f", "wav", f"file:{out_path}"], timeout=max(120, int(duration * 0.5)))
    return res.returncode == 0 and os.path.isfile(out_path) and os.path.getsize(out_path) > 44 + SR


def envelope(wav: str) -> np.ndarray:
    """Level of every 20 ms frame in dBFS, read in one-minute pieces (memory stays small for long films)."""
    out = []
    with wave.open(wav, "rb") as w:
        while True:
            pcm = w.readframes(SR * 60)
            if not pcm:
                break
            x = np.frombuffer(pcm, np.int16).astype(np.float32) / 32768.0
            n = len(x) // FRAME
            if n:
                fr = x[: n * FRAME].reshape(n, FRAME)
                out.append(20 * np.log10(np.sqrt((fr ** 2).mean(axis=1)) + 1e-6))
    return np.concatenate(out) if out else np.zeros(0, np.float32)


def _win(env: np.ndarray, a: float, b: float) -> np.ndarray:
    return env[int(a * 50): max(int(a * 50) + 1, int(b * 50))]


def _mmss(t: float) -> str:
    t = max(0, int(round(t)))
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60}:{t % 60:02d}"


def measure(raw: dict, env: np.ndarray, duration: float) -> dict:
    """Turn engine output + level envelope into the stored dialogue reading."""
    lang = (raw.get("language") or "").lower() or None
    spaced = not (lang and lang.split("-")[0] in NO_WORD_SPACES)
    segs = []
    for s in raw.get("segments", []):
        text = (s.get("text") or "").strip()
        a, b = max(0.0, float(s["start_s"])), min(duration or float(s["end_s"]), float(s["end_s"]))
        if not text or b <= a:
            continue
        words = len(text.split()) if spaced else None
        d = b - a
        seg = {"start_s": round(a, 2), "end_s": round(b, 2), "text": text[:1000],
               "words": words, "wpm": round(words / d * 60) if words and d >= 1.0 and not s.get("coarse") else None}
        lp, ns = s.get("avg_logprob"), s.get("no_speech_prob")
        if isinstance(lp, (int, float)):
            seg["avg_logprob"] = round(float(lp), 2)
        if isinstance(ns, (int, float)):
            seg["no_speech_prob"] = round(float(ns), 2)
        w = _win(env, a, b)
        if len(w) >= 40 and not s.get("coarse"):
            p90, p20 = float(np.percentile(w, 90)), float(np.percentile(w, 20))
            seg["level_db"] = round(p90, 1)
            seg["dip_db"] = round(p90 - p20, 1)            # level drop between words: small = a loud bed underneath
            seg["buried"] = bool(p90 - p20 < 8.0 and p20 > -40.0)
        seg["unclear"] = bool(isinstance(lp, (int, float)) and (lp < -1.0 or (isinstance(ns, (int, float)) and ns > 0.5 and lp < -0.6)))
        segs.append(seg)
    speech_s = sum(s["end_s"] - s["start_s"] for s in segs)
    words_total = sum(s["words"] or 0 for s in segs) if spaced else None
    wpm = round(words_total / speech_s * 60) if spaced and speech_s >= 5 and not any("coarse" in s for s in raw.get("segments", [])) else None
    fast = [s for s in segs if s["wpm"] and s["end_s"] - s["start_s"] >= 2.5 and s["wpm"] > FAST_WPM]
    slow = [s for s in segs if s["wpm"] and s["end_s"] - s["start_s"] >= 3.0 and (s["words"] or 0) >= 3 and s["wpm"] < SLOW_WPM]
    buried = [s for s in segs if s.get("buried")]
    unclear = [s for s in segs if s["unclear"]]
    dead = []
    for a, b in zip(segs, segs[1:]):
        gap = b["start_s"] - a["end_s"]
        if gap >= DEAD_AIR_S:
            w = _win(env, a["end_s"], b["start_s"])
            if len(w) and float(np.percentile(w, 90)) < -45.0:      # quiet: no music or effects carrying the gap
                dead.append({"start_s": a["end_s"], "end_s": b["start_s"]})

    def spans(xs):
        return [{"start_s": x["start_s"], "end_s": x["end_s"], "at": _mmss(x["start_s"]), **({"wpm": x["wpm"]} if "wpm" in x else {})}
                for x in xs[:50]]
    return {
        "engine": raw.get("engine"), "language": lang, "language_probability":
            round(float(raw["language_probability"]), 2) if isinstance(raw.get("language_probability"), (int, float)) else None,
        "partial": bool(raw.get("partial")),
        "segments": segs[:3000], "speech_s": round(speech_s, 1), "speech_share": round(speech_s / max(duration, 0.1), 3),
        "words": words_total, "words_per_minute": wpm,
        "buried": spans(buried), "dead_air": [{**d, "at": _mmss(d["start_s"])} for d in dead[:50]],
        "fast": spans(fast), "slow": spans(slow), "unclear": spans(unclear),
    }


# ---------------------------------------------------------------- entry point

def analyse(path: str, duration: float, owner: str | None = None, budget_s: float | None = None) -> dict:
    """Returns {"speech_to_text": tag, "dialogue": tag}. Never raises for a missing or failing engine."""
    engine = available_engine()
    if engine is None:
        return {"speech_to_text": tag("not available", 1.0, "observed", NOT_AVAILABLE),
                "dialogue": tag(None, 0.0, "estimated", NOT_AVAILABLE)}
    deadline = time.monotonic() + max(120.0, float(budget_s or 900))
    folder = os.path.dirname(os.path.abspath(path))
    fd, wav = tempfile.mkstemp(prefix="speech-", suffix=".wav", dir=folder if os.access(folder, os.W_OK) else None)
    os.close(fd)
    try:
        if not extract_wav(path, duration, wav):
            return {"speech_to_text": tag(engine, 1.0, "observed"),
                    "dialogue": tag(None, 0.0, "estimated", "The sound track could not be prepared for speech recognition.")}
        raw = None
        errors = []
        if engine == "faster-whisper":
            try:
                raw = _transcribe_whisper(wav, deadline)
            except Exception as e:  # noqa: BLE001 - e.g. model not downloaded; try the local server next
                errors.append(f"faster-whisper: {type(e).__name__}")
                log.warning("speech to text via faster-whisper failed: %s", type(e).__name__)
        if raw is None:
            cfg = _local_server()
            if cfg is not None:
                try:
                    raw = _transcribe_server(wav, cfg, deadline)
                except EngineError as e:
                    errors.append(str(e))
                    log.warning("speech to text via the local AI server failed")
        if raw is None:
            note = "Speech to text was tried but failed (" + "; ".join(errors) + "). " + NOT_AVAILABLE
            return {"speech_to_text": tag("failed", 1.0, "observed", note), "dialogue": tag(None, 0.0, "estimated", note)}
        env = envelope(wav)
    finally:
        try:
            os.remove(wav)
        except OSError:
            pass
    d = measure(raw, env, duration)
    conf = 0.6 if d["segments"] else 0.4
    note = (f"Speech recognised on this computer with {d['engine']}. Timings, words per minute and the buried / unclear "
            "flags are estimates; names and quiet lines can be misheard.")
    if d["partial"]:
        note += " Only the first part of the sound track was transcribed within the time budget."
    return {"speech_to_text": tag(d["engine"], 1.0, "observed"), "dialogue": tag(d, conf, "estimated", note)}
