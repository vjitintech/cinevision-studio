"""Sound analysis: loudness (EBU R128), peaks and clipping, silences, and a rough speech / music / ambience reading.

Loudness, true peak and silences are measured by FFmpeg's own filters (observed). The content reading (speech vs
music) is a heuristic from the loudness envelope's rhythm and the spectrum's steadiness, and is labelled as such.
"""
from __future__ import annotations

import re

import numpy as np

from . import tag

SR = 16000
MAX_PCM_S = 600            # content reading uses at most 10 minutes of audio, evenly spread


def _ffmpeg(args: list[str], timeout: int, capture_stdout=False):
    from ..config import settings
    from ..media import sandbox
    s = settings()
    return sandbox.run([s.ffmpeg_bin, "-nostdin", "-hide_banner", "-protocol_whitelist", "file", *args],
                       sandbox.Limits(cpu_s=timeout * 2, timeout_s=timeout))


def _num(pattern: str, text: str) -> float | None:
    m = re.findall(pattern, text)
    if not m:
        return None
    try:
        return float(m[-1])
    except ValueError:
        return None


def loudness(path: str, duration: float) -> dict | None:
    """Integrated loudness, loudness range, true peak, silences. None when the clip has no sound."""
    res = _ffmpeg(["-i", f"file:{path}", "-vn", "-map", "0:a:0?",
                   "-af", "ebur128=peak=true:framelog=quiet,silencedetect=noise=-50dB:d=1.5,astats=metadata=0:reset=0",
                   "-f", "null", "-"], timeout=max(120, int(duration * 0.5)))
    err = res.stderr.decode("utf-8", "replace")
    if "Output file does not contain any stream" in err or "matches no streams" in err or "I:" not in err:
        return None
    integrated = _num(r"I:\s+(-?[\d.]+|-inf) LUFS", err)
    lra = _num(r"LRA:\s+(-?[\d.]+) LU", err)
    true_peak = _num(r"Peak:\s+(-?[\d.]+|-inf) dBFS", err)
    starts = [float(x) for x in re.findall(r"silence_start:\s*(-?[\d.]+)", err)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*(-?[\d.]+)", err)]
    silences = []
    for i, s0 in enumerate(starts):
        e = ends[i] if i < len(ends) else duration
        silences.append({"start_s": round(max(0.0, s0), 2), "end_s": round(min(duration, e), 2)})
    clipped = 0
    for m in re.findall(r"Number of samples clipped:\s*(\d+)", err) or []:
        clipped = max(clipped, int(m))
    noise_floor = _num(r"Noise floor dB:\s*(-?[\d.]+|-inf)", err)
    silent_total = sum(s["end_s"] - s["start_s"] for s in silences)
    return {"integrated_lufs": integrated, "lra_lu": lra, "true_peak_dbfs": true_peak, "silences": silences[:200],
            "silent_share": round(silent_total / max(duration, 0.1), 3), "clipped_samples": clipped,
            "noise_floor_db": noise_floor}


def _pcm(path: str, duration: float) -> np.ndarray | None:
    # at most MAX_PCM_S seconds: for long films sample evenly with atrim-free seeking is costly, so read the
    # first MAX_PCM_S of every block via a simple decimation of the whole track at a low rate instead
    rate = SR if duration <= MAX_PCM_S else max(2000, int(SR * MAX_PCM_S / duration))
    res = _ffmpeg(["-loglevel", "error", "-i", f"file:{path}", "-vn", "-map", "0:a:0?", "-ac", "1", "-ar", str(rate),
                   "-f", "s16le", "pipe:1"], timeout=max(120, int(duration * 0.5)))
    if res.returncode != 0 or len(res.stdout) < rate:
        return None
    return np.frombuffer(res.stdout, np.int16).astype(np.float32) / 32768.0, rate


def content_reading(path: str, duration: float) -> dict | None:
    got = _pcm(path, duration)
    if got is None:
        return None
    x, rate = got
    hop = rate // 50                                   # 20 ms frames
    n = len(x) // hop
    if n < 50:
        return None
    frames = x[: n * hop].reshape(n, hop)
    rms = np.sqrt((frames ** 2).mean(axis=1) + 1e-12)
    db = 20 * np.log10(rms)
    active = db > (np.percentile(db, 95) - 35)
    if active.mean() < 0.05:
        return {"label": "mostly silence / room tone", "speech_like": 0.0, "music_like": 0.0}
    # speech: loudness rises and falls at the syllable rate (3–7 Hz) with frequent short pauses
    env = rms - rms.mean()
    spec = np.abs(np.fft.rfft(env * np.hanning(len(env))))
    freqs = np.fft.rfftfreq(len(env), 1 / 50)
    band = spec[(freqs >= 3) & (freqs <= 7)].sum() / (spec[(freqs > 0.3) & (freqs <= 20)].sum() + 1e-9)
    gaps = np.diff(active.astype(int))
    pause_rate = (gaps == -1).sum() / (n / 50)          # pauses per second
    # music: steadier level and a spectrum that changes little frame to frame (sustained tones)
    steadiness = 1.0 - float(np.clip(np.std(db[active]) / 12.0, 0, 1))
    if rate >= 8000:
        win = 512
        m = len(x) // win
        sp = np.abs(np.fft.rfft(x[: m * win].reshape(m, win) * np.hanning(win), axis=1)) + 1e-9
        sp = sp / sp.sum(axis=1, keepdims=True)
        flux = float(np.median(np.abs(np.diff(sp, axis=0)).sum(axis=1)))
        tonal = float(np.clip(1.0 - flux / 0.6, 0, 1))
    else:
        tonal = steadiness
    speech = float(np.clip(band * 2.2 + min(pause_rate, 2) * 0.25 - steadiness * 0.3, 0, 1))
    music = float(np.clip(0.55 * tonal + 0.45 * steadiness - band, 0, 1))
    if speech > 0.45 and music > 0.45:
        label = "dialogue over music"
    elif speech > 0.45:
        label = "mostly dialogue / voice"
    elif music > 0.45:
        label = "mostly music"
    else:
        label = "ambience / effects"
    return {"label": label, "speech_like": round(speech, 2), "music_like": round(music, 2)}


def analyse(path: str, duration: float, has_audio: bool, transcribe: bool = False, owner: str | None = None,
            budget_s: float | None = None) -> dict:
    if not has_audio:
        return {"present": tag(False, 1.0, "observed", "The clip has no sound track.")}
    lo = loudness(path, duration)
    if lo is None:
        return {"present": tag(False, 0.9, "observed", "No decodable sound track.")}
    reading = None
    try:
        reading = content_reading(path, duration)
    except Exception:  # noqa: BLE001 - optional extra
        reading = None
    out = {
        "present": tag(True, 1.0, "observed"),
        "loudness": tag({"integrated_lufs": lo["integrated_lufs"], "range_lu": lo["lra_lu"],
                         "true_peak_dbfs": lo["true_peak_dbfs"]}, 0.95, "observed",
                        "EBU R128 measurement. Streaming platforms normalise to about −14 to −16 LUFS; "
                        "cinema and broadcast mixes sit near −24 to −27 LUFS with more dynamic range."),
        "clipping": tag(lo["clipped_samples"], 0.9, "observed", "Samples at full scale (digital distortion)."),
        "silences": tag(lo["silences"], 0.85, "observed", "Stretches quieter than −50 dB for 1.5 s or more."),
        "silent_share": tag(lo["silent_share"], 0.85, "observed"),
        "noise_floor_db": tag(lo["noise_floor_db"], 0.6, "observed", "Quietest sustained level: hiss / room noise."),
    }
    if reading:
        out["content"] = tag(reading, 0.4, "heuristic",
                             "From loudness rhythm and spectral steadiness — a rough reading, not speech recognition.")
    if transcribe:
        from . import speech
        out.update(speech.analyse(path, duration, owner=owner, budget_s=budget_s))
    return out
