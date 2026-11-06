"""Orchestrates the full scene analysis for one clip."""
from __future__ import annotations

import logging
import math
import os
import subprocess
import threading
import time
from typing import Callable

import cv2
import numpy as np

from . import audio, color, composition, environment, motion, narrative, speech, subjects, tag, titles

log = logging.getLogger("cinevision.analysis")

ENGINE = {"id": "cv-scene", "version": "1.1", "detectors": ["lk-flow+ransac", "hog-people", "spectral-residual"],
          "analysis_fps": 8}

ASSUMED_HFOV_DEG = 55.0   # typical "normal" focal length on Super35 (~24–28 mm); used only for proxy placement


MAX_SAMPLES = 900          # caps memory for long clips (~8 fps up to ~2 minutes, sparser beyond)
SAMPLE_W = 320
KEEP_COLOUR = 48           # colour copies of sampled frames kept (for fallbacks); the rest keep only grey


class _Collector:
    """Receives decoded frames one at a time: keeps a grey copy + histogram for cuts and motion, finds text on the
    fly, and keeps only a few colour copies, so memory stays bounded however long the film is."""

    def __init__(self, expected: int, keep_all_colour: bool):
        self.samples: list[motion.Sample] = []
        self.text_hits: list = []
        self.keep_every = 1 if keep_all_colour else max(1, expected // KEEP_COLOUR)

    def add(self, t: float, small: np.ndarray) -> None:
        keep = len(self.samples) % self.keep_every == 0
        self.samples.append(motion.Sample(t=t, gray=cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), hist=motion.hist_of(small),
                                          bgr_small=small if keep else None))
        try:
            hit = titles.frame_hit(t, small)
        except Exception:  # noqa: BLE001 - text finding is optional per frame
            hit = None
        if hit:
            self.text_hits.append(hit)

    def reset(self):
        self.samples, self.text_hits = [], []


def _make_sample(t: float, small: np.ndarray) -> motion.Sample:
    return motion.Sample(t=t, gray=cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), hist=motion.hist_of(small), bgr_small=small)


def _read_exact(f, n: int) -> bytes | None:
    parts, got = [], 0
    while got < n:
        b = f.read(n - got)
        if not b:
            return None
        parts.append(b)
        got += len(b)
    return b"".join(parts)


def _stream_frames(cmd: list[str], lim, frame_bytes: int, on_frame: Callable[[int, bytes], None], max_frames: int) -> int:
    """Run a sandboxed FFmpeg and hand each raw frame to on_frame as it arrives (no full-output buffering)."""
    from ..media import sandbox
    kw: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE, "stderr": subprocess.DEVNULL, "env": sandbox._env()}
    if sandbox.WINDOWS:
        kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        kw.update(preexec_fn=sandbox._preexec(lim), close_fds=True)
    proc = subprocess.Popen(cmd, **kw)
    timed_out = []

    def kill():
        try:
            if sandbox.WINDOWS:
                proc.kill()
            else:
                import signal
                os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass

    def on_timeout():
        timed_out.append(True)
        kill()
    timer = threading.Timer(lim.timeout_s, on_timeout)
    timer.daemon = True
    timer.start()
    n = 0
    try:
        while n < max_frames:
            buf = _read_exact(proc.stdout, frame_bytes)
            if buf is None:
                break
            on_frame(n, buf)
            n += 1
    finally:
        timer.cancel()
        if proc.poll() is None:
            kill()
        proc.stdout.close()
        proc.wait()
    if timed_out and n == 0:
        raise sandbox.SandboxError("media tool timed out")
    return n


def _sample_ffmpeg(path: str, duration: float, fps_target: float, aspect: float, col: _Collector | None = None):
    """Decode with FFmpeg (the same decoder that prepared the clip), streaming raw BGR frames — no OpenCV video I/O."""
    from ..config import settings
    from ..media import sandbox
    col = col or _Collector(MAX_SAMPLES, True)
    fps = min(fps_target, MAX_SAMPLES / max(duration, 0.5))
    h = max(2, int(round(SAMPLE_W / max(aspect, 0.1) / 2)) * 2)
    s = settings()
    size = SAMPLE_W * h * 3
    cmd = [s.ffmpeg_bin, "-nostdin", "-hide_banner", "-loglevel", "error", "-protocol_whitelist", "file",
           "-i", f"file:{path}", "-an", "-vf", f"fps={fps:.4f},scale={SAMPLE_W}:{h}:flags=area",
           "-frames:v", str(MAX_SAMPLES), "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
    lim = sandbox.Limits(cpu_s=max(600, int(duration * 4)), timeout_s=max(s.ffmpeg_timeout_s, int(duration * 2)))

    def on_frame(i: int, buf: bytes):
        col.add(i / fps, np.frombuffer(buf, np.uint8).reshape(h, SAMPLE_W, 3).copy())
    if _stream_frames(cmd, lim, size, on_frame, MAX_SAMPLES) == 0:
        col.reset()
        raise RuntimeError("ffmpeg sampling failed")
    return col.samples


def _sample_opencv(path: str, duration: float, fps_target: float, col: _Collector | None = None):
    col = col or _Collector(MAX_SAMPLES, True)
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError("cannot open proxy for analysis")
    native = cap.get(cv2.CAP_PROP_FPS) or 24.0
    if not (1 <= native <= 240):
        native = 24.0
    step = max(1, int(round(native / min(fps_target, MAX_SAMPLES / max(duration, 0.5)))))
    i = 0
    while len(col.samples) < MAX_SAMPLES and cap.grab():
        if i % step == 0:
            ok, frame = cap.retrieve()
            if ok and frame is not None and frame.size:
                small = cv2.resize(frame, (SAMPLE_W, max(2, int(SAMPLE_W * frame.shape[0] / frame.shape[1]))),
                                   interpolation=cv2.INTER_AREA)
                col.add(i / native, small)
        i += 1
    cap.release()
    if not col.samples:
        raise RuntimeError("opencv decoded no frames")
    return col.samples


def _sample_keyframes(keyframes: list[tuple[float, np.ndarray]], col: _Collector | None = None):
    col = col or _Collector(len(keyframes), True)
    for t, img in keyframes:
        small = cv2.resize(img, (SAMPLE_W, max(2, int(SAMPLE_W * img.shape[0] / img.shape[1]))), interpolation=cv2.INTER_AREA)
        col.add(t, small)
    return col.samples


def _sample_video(path: str, duration: float, fps_target: float, aspect: float,
                  keyframes: list[tuple[float, np.ndarray]]) -> tuple[list[motion.Sample], str, _Collector]:
    """Try FFmpeg, then OpenCV, then fall back to the (sparser) keyframes extracted at ingest. Never raises."""
    expected = int(min(MAX_SAMPLES, fps_target * max(duration, 0.5)))
    col = _Collector(expected, keep_all_colour=not keyframes)
    for name, fn in (("ffmpeg", lambda: _sample_ffmpeg(path, duration, fps_target, aspect, col)),
                     ("opencv", lambda: _sample_opencv(path, duration, fps_target, col))):
        try:
            fn()
            if col.samples:
                return col.samples, name, col
        except Exception as e:  # noqa: BLE001 - fall through to the next decoder
            log.warning("frame sampling via %s failed: %s", name, type(e).__name__)
        col.reset()
    try:
        col = _Collector(len(keyframes), True)
        _sample_keyframes(keyframes, col)
        return col.samples, "keyframes", col
    except Exception as e:  # noqa: BLE001
        log.warning("keyframe sampling failed: %s", type(e).__name__)
        return [], "none", _Collector(0, True)


def _ff_frames(path: str, start: float, span: float, fps: float, width: int, aspect: float,
               max_frames: int) -> list[tuple[float, np.ndarray]]:
    """A short burst of frames at `width` px starting at `start` (fast seek)."""
    from ..config import settings
    from ..media import sandbox
    s = settings()
    h = max(2, int(round(width / max(aspect, 0.1) / 2)) * 2)
    res = sandbox.run([s.ffmpeg_bin, "-nostdin", "-hide_banner", "-loglevel", "error", "-protocol_whitelist", "file",
                       "-ss", f"{max(0.0, start):.3f}", "-i", f"file:{path}", "-an", "-t", f"{max(0.05, span):.3f}",
                       "-vf", f"fps={fps:.4f},scale={width}:{h}:flags=area", "-frames:v", str(max_frames),
                       "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"], sandbox.Limits(cpu_s=60, timeout_s=60))
    size = width * h * 3
    if res.returncode != 0 or len(res.stdout) < size:
        return []
    n = len(res.stdout) // size
    buf = np.frombuffer(res.stdout, np.uint8)
    return [(start + i / fps, buf[i * size:(i + 1) * size].reshape(h, width, 3).copy()) for i in range(n)]


def _frame_at(path: str, t: float) -> np.ndarray | None:
    """One frame at the proxy's own resolution (for reading on-screen text)."""
    from ..config import settings
    from ..media import sandbox
    s = settings()
    res = sandbox.run([s.ffmpeg_bin, "-nostdin", "-hide_banner", "-loglevel", "error", "-protocol_whitelist", "file",
                       "-ss", f"{max(0.0, t):.3f}", "-i", f"file:{path}", "-an", "-frames:v", "1",
                       "-f", "image2pipe", "-vcodec", "png", "pipe:1"], sandbox.Limits(cpu_s=60, timeout_s=60))
    if res.returncode != 0 or not res.stdout:
        return None
    img = cv2.imdecode(np.frombuffer(res.stdout, np.uint8), cv2.IMREAD_COLOR)
    return img if img is not None and img.size else None


LONG_CLIP_S = 150          # beyond this, cuts come from FFmpeg's scene detector over every frame
MAX_DETAILED_SHOTS = 80    # detailed shots for short clips, and the minimum for long films
EST_SHOT_COST_S = 0.8      # rough cost of one detailed shot of a long film (burst decode + motion + people)
DETAIL_SHARE = 0.75        # share of the time budget the detailed-shot pass may use
BURST_FRAMES = 8


def _budget_s() -> float:
    try:
        from ..config import settings
        return float(getattr(settings(), "analysis_budget_s", 900) or 900)
    except Exception:  # noqa: BLE001
        return float(os.environ.get("CINEVISION_ANALYSIS_BUDGET_S") or 900)


def detail_target(n_shots: int, duration: float, budget_s: float) -> int:
    """How many shots get the detailed pass. Short clips: up to 80 (unchanged). Long films: grows with running time
    (+10 per minute beyond the first 2½) within what the time budget allows."""
    if n_shots <= MAX_DETAILED_SHOTS or duration <= LONG_CLIP_S:
        return min(n_shots, MAX_DETAILED_SHOTS)
    by_duration = MAX_DETAILED_SHOTS + int(10 * max(0.0, duration - LONG_CLIP_S) / 60)
    by_time = int(budget_s * DETAIL_SHARE / EST_SHOT_COST_S)
    return max(MAX_DETAILED_SHOTS, min(n_shots, by_duration, by_time))


def detail_plan(shots: list[dict], k: int) -> list[int]:
    """Shot indices for the detailed pass, in processing order: evenly spread shots interleaved with the longest
    ones (always including the first and last), so a pass cut short by the time budget is still representative."""
    n = len(shots)
    if k >= n:
        return list(range(n))
    n_spread = max(2, int(round(k * 0.6)))
    spread = list(dict.fromkeys(int(round(i * (n - 1) / (n_spread - 1))) for i in range(n_spread)))
    in_spread = set(spread)
    longest = [i for i in sorted(range(n), key=lambda i: (-(shots[i]["end_s"] - shots[i]["start_s"]), i))
               if i not in in_spread][: max(0, k - len(spread))]
    order: list[int] = []
    for j in range(max(len(spread), len(longest))):
        for src in (spread, longest):
            if j < len(src) and len(order) < k:
                order.append(src[j])
    return order


def _cuts_ffmpeg(path: str, duration: float) -> list[float]:
    """Hard cuts over the whole clip with FFmpeg's scene-change detector (every frame, tiny resolution)."""
    from ..config import settings
    from ..media import sandbox
    import re
    s = settings()
    res = sandbox.run([s.ffmpeg_bin, "-nostdin", "-hide_banner", "-loglevel", "info", "-protocol_whitelist", "file",
                       "-i", f"file:{path}", "-an", "-vf", "scale=160:-2,scdet=threshold=12", "-f", "null", "-"],
                      sandbox.Limits(cpu_s=max(3600, int(duration * 3)), timeout_s=max(300, int(duration * 2))))
    if res.returncode != 0:
        raise RuntimeError("scene detection failed")
    times = sorted(float(x) for x in re.findall(r"lavfi\.scd\.time:\s*([\d.]+)", res.stderr.decode("utf-8", "replace")))
    cuts, last = [], 0.0
    for t in times:
        if t - last >= 0.35 and duration - t >= 0.35:
            cuts.append(round(t, 3))
            last = t
    return cuts


def _shots_from_cuts(cuts: list[float], duration: float) -> list[dict]:
    edges = [0.0, *cuts, duration]
    return [{"index": i, "start_s": round(a, 3), "end_s": round(b, 3)} for i, (a, b) in enumerate(zip(edges, edges[1:]))]


PLACEHOLDER_NOTE = ("Not measured: this part of the analysis failed on this clip, so a neutral placeholder is shown. "
                    "Treat it as unknown.")


def _degrade(obj):
    """Mark every tagged value in a fallback result as unmeasured (confidence 0) so nothing is presented as observed."""
    if isinstance(obj, dict):
        if {"value", "confidence", "basis"} <= obj.keys():
            return {**obj, "value": _degrade(obj["value"]), "confidence": 0.0, "note": PLACEHOLDER_NOTE}
        return {k: _degrade(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return type(obj)(_degrade(v) for v in obj)
    return obj


class _Guard:
    """Runs one analysis step; on failure logs it, records it, and returns a degraded neutral result instead."""

    def __init__(self):
        self.failed: list[str] = []

    def __call__(self, step: str, fn: Callable, fallback: Callable):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - one weak step must not sink the whole analysis
            log.warning("analysis step '%s' failed: %s", step, type(e).__name__, exc_info=True)
            if step not in self.failed:
                self.failed.append(step)
            return _degrade(fallback())


_NEUTRAL = np.full((180, 320, 3), 128, np.uint8)


def _angle_label(tilt_hint: str, roll) -> tuple[str, float]:
    if roll is not None and abs(roll) > 6:
        return "dutch angle", 0.45
    if "down" in tilt_hint:
        return "high angle", 0.35
    if "up" in tilt_hint:
        return "low angle", 0.35
    if "level" in tilt_hint:
        return "eye level", 0.4
    return "undetermined (assume eye level)", 0.15


def _place_subjects(positions: list[dict], aspect: float) -> list[dict]:
    """Approximate subject positions (metres) in a camera-centred ground plane: x right, z forward."""
    hfov = math.radians(ASSUMED_HFOV_DEG)
    vfov = 2 * math.atan(math.tan(hfov / 2) / aspect)
    out = []
    for p in positions:
        real = 0.23 if p.get("kind") == "face" else 1.7
        frac = max(0.03, p.get("h", 0.3))
        dist = real / (2 * math.tan(vfov / 2) * frac)
        x = (p["x"] - 0.5) * 2 * math.tan(hfov / 2) * dist
        out.append({"x": round(x, 2), "z": round(dist, 2)})
    return out


def _burst_motion(frames: list[tuple[float, np.ndarray]]) -> dict | None:
    """Camera movement from a burst of frames decoded around the middle of a shot (long films)."""
    if len(frames) < 3:
        return None
    sm = []
    for t, img in frames:
        small = cv2.resize(img, (SAMPLE_W, max(2, int(SAMPLE_W * img.shape[0] / img.shape[1]))), interpolation=cv2.INTER_AREA)
        sm.append(motion.Sample(t=t, gray=cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), hist=None, bgr_small=None))
    pairs = []
    for a, b in zip(sm, sm[1:]):
        try:
            pm = motion.pair_motion(a, b)
        except Exception:  # noqa: BLE001
            pm = None
        if pm is not None:
            pairs.append(pm)
    return motion.classify(pairs) if len(pairs) >= 2 else None


def _limitations_engines(face: bool, sound: dict, text: dict) -> list[str]:
    out = []
    out.append("Faces: YuNet face detector (bundled) — used for shot size and to find close-ups and seated people."
               if face else "Faces: not available (the YuNet model could not be loaded), so shot sizes rely on body "
                            "detection or saliency.")
    stt = (sound or {}).get("speech_to_text")
    if isinstance(stt, dict):
        v = stt.get("value")
        if v in ("not available", "failed"):
            out.append("Dialogue: " + (stt.get("note") or speech.NOT_AVAILABLE))
        elif v:
            dl = (sound.get("dialogue") or {}).get("value") or {}
            out.append(f"Dialogue: transcribed on this computer with {v}" +
                       (" (only the first part, within the time budget)." if dl.get("partial") else "."))
    reading = (text or {}).get("reading") if isinstance(text, dict) else None
    if isinstance(reading, dict):
        out.append(f"On-screen text: read with {reading['engine']} ({reading.get('languages')})." if reading.get("available")
                   else "On-screen text: " + titles.NOT_AVAILABLE + ".")
    return out


def run(proxy_path: str, keyframes: list[tuple[float, np.ndarray]], meta: dict,
        progress: Callable[[float, str], None], owner: str | None = None, lang: str = "en") -> dict:
    t_start = time.monotonic()
    budget = _budget_s()
    guard = _Guard()
    try:
        duration = float(meta.get("duration_s") or 0) or (max((t for t, _ in keyframes), default=0.0) + 0.5)
    except (TypeError, ValueError):
        duration = max((t for t, _ in keyframes), default=0.0) + 0.5
    try:
        aspect = float(meta.get("display_aspect") or (meta["width"] / meta["height"]))
    except (TypeError, ValueError, ZeroDivisionError, KeyError):
        aspect = (keyframes[0][1].shape[1] / keyframes[0][1].shape[0]) if keyframes else 16 / 9
    progress(0.02, "Sampling frames")
    samples, decoder, collected = _sample_video(proxy_path, duration, ENGINE["analysis_fps"], aspect, keyframes)
    if not samples and not keyframes:
        from ..worker import UserFacingError
        raise UserFacingError("No picture could be decoded from this clip, so it can't be analysed. "
                              "Try exporting it again as MP4 (H.264) and uploading that.")
    colour_samples = [(s.t, s.bgr_small) for s in samples if s.bgr_small is not None]
    long_clip = duration > LONG_CLIP_S
    progress(0.22, "Detecting shot boundaries")
    if long_clip:
        shots = guard("shot boundaries", lambda: _shots_from_cuts(_cuts_ffmpeg(proxy_path, duration), duration),
                      lambda: motion.detect_shots(samples, duration))
    else:
        shots = guard("shot boundaries", lambda: motion.detect_shots(samples, duration),
                      lambda: [{"index": 0, "start_s": 0.0, "end_s": duration}])
    if not shots:
        shots = [{"index": 0, "start_s": 0.0, "end_s": duration}]

    progress(0.3, "Estimating camera motion")
    shot_motion = []
    si_ = 0
    for si, sh in enumerate(shots):
        def _mo(sh=sh):
            nonlocal si_
            # samples are in time order: walk forward instead of scanning all of them for every shot
            while si_ < len(samples) and samples[si_].t < sh["start_s"]:
                si_ += 1
            sm, j = [], si_
            while j < len(samples) and samples[j].t < sh["end_s"]:
                sm.append(samples[j])
                j += 1
            pairs = []
            for a, b in zip(sm, sm[1:]):
                try:
                    pm = motion.pair_motion(a, b)
                except Exception:  # noqa: BLE001 - one bad frame pair is skipped, not fatal
                    pm = None
                if pm is not None:
                    pairs.append(pm)
            return motion.classify(pairs)
        shot_motion.append(guard("camera motion", _mo, lambda: motion.classify([])))
        if si % 25 == 0 or si == len(shots) - 1:
            progress(0.3 + 0.2 * (si + 1) / len(shots), "Estimating camera motion")

    progress(0.52, "Analysing colour and exposure")
    kf_imgs = [img for _, img in keyframes] or [img for _, img in colour_samples[:: max(1, len(colour_samples) // 12)]]
    pick = kf_imgs[:: max(1, len(kf_imgs) // 24)]
    tone = guard("light and colour", lambda: color.tone_and_color(pick), lambda: color.tone_and_color([_NEUTRAL]))
    pal = guard("palette", lambda: color.palette(pick), lambda: [])

    progress(0.58, "Detecting subjects and composition")
    all_dets = []
    # frames for subject/composition work on short clips: ingest keyframes, else the decoded samples
    frames_for = keyframes or colour_samples
    k = detail_target(len(shots), duration, budget)
    if not long_clip and len(shots) > MAX_DETAILED_SHOTS:          # short clips: unchanged even spread
        step = len(shots) / MAX_DETAILED_SHOTS
        order = sorted({int(i * step) for i in range(MAX_DETAILED_SHOTS)})
    else:
        order = detail_plan(shots, k)
    deadline = t_start + budget * DETAIL_SHARE
    done: dict[int, dict] = {}
    budget_hit = False
    for n_done, si in enumerate(order):
        sh = shots[si]
        if long_clip and n_done >= MAX_DETAILED_SHOTS and time.monotonic() > deadline:
            budget_hit = True
            break
        burst = []
        if long_clip:
            mid = (sh["start_s"] + sh["end_s"]) / 2
            span = min(1.5, max(0.25, (sh["end_s"] - sh["start_s"]) * 0.8))
            fps = min(12.0, max(2.0, (BURST_FRAMES - 1) / span))
            try:
                burst = _ff_frames(proxy_path, max(sh["start_s"] + 0.04, mid - span / 2), span, fps, 640, aspect, BURST_FRAMES)
            except Exception:  # noqa: BLE001 - fall back to keyframes / samples
                burst = []
            if burst:
                bm = guard("camera motion", lambda: _burst_motion(burst), lambda: None)
                if bm is not None:
                    shot_motion[si] = bm
        kfs = burst or [(t, img) for t, img in frames_for if sh["start_s"] <= t < sh["end_s"]] or \
            [min(frames_for, key=lambda kf: abs(kf[0] - (sh["start_s"] + sh["end_s"]) / 2))]
        mid = min(kfs, key=lambda kf: abs(kf[0] - (sh["start_s"] + sh["end_s"]) / 2))

        def _comp(img=mid[1]):
            c = composition.analyse_frame(img)
            return c, c.pop("_saliency")

        def _comp_fallback():
            c = composition.analyse_frame(_NEUTRAL)
            c.pop("_saliency")
            return c, None
        comp, sal = guard("composition", _comp, _comp_fallback)

        def _dets(kfs=kfs):
            out = []
            for _, img in kfs[:: max(1, len(kfs) // (3 if burst else 6))]:
                try:
                    out.append(subjects.detect(img))
                except Exception:  # noqa: BLE001
                    pass
            return out
        dets = guard("subject detection", _dets, lambda: [])
        all_dets.extend(dets)
        empty = [{"people": [], "faces": []}]
        subj = guard("subjects", lambda: subjects.summarise(dets or empty), lambda: subjects.summarise(empty))
        sal_share = float((sal > 0.35).mean()) if sal is not None else 0.3
        size = guard("shot size", lambda: subjects.shot_size(dets, sal_share), lambda: subjects.shot_size([], 0.3))
        try:
            angle, aconf = _angle_label(comp["camera_tilt"]["value"], comp["horizon_roll_deg"]["value"])
        except Exception:  # noqa: BLE001
            angle, aconf = "undetermined (assume eye level)", 0.0
        try:
            geom = _place_subjects(subj["positions"]["value"], aspect)
        except Exception:  # noqa: BLE001
            geom = []
        done[si] = {
            **sh,
            "representative_t": round(mid[0], 3),
            "shot_size": size,
            "camera_angle": tag(angle, aconf, "estimated", "From horizon / vanishing-point position and line roll."),
            "camera_height": tag({"high angle": "above eye level", "low angle": "below eye level"}.get(angle, "about eye level"),
                                 aconf * 0.8, "estimated", "Absolute height cannot be recovered from 2D video."),
            "movement": shot_motion[si],
            "composition": comp,
            "subjects": subj,
            "scene_geometry": tag({"assumed_hfov_deg": ASSUMED_HFOV_DEG, "subjects_m": geom}, 0.2, "estimated",
                                  "Rough metres from camera assuming average human size and a ~55° lens. Use for previs only."),
        }
        if n_done % 10 == 0 or n_done == len(order) - 1:
            progress(0.58 + 0.27 * (n_done + 1) / max(1, len(order)), "Detecting subjects and composition")

    per_shot = []
    light_template = None
    for si, sh in enumerate(shots):
        if si in done:
            per_shot.append(done[si])
            continue
        if light_template is None:
            c0 = composition.analyse_frame(_NEUTRAL)
            c0.pop("_saliency")
            light_template = {"shot_size": subjects.shot_size([], 0.3), "composition": c0,
                              "subjects": subjects.summarise([{"people": [], "faces": []}])}
        lt = {k_: {**v} if isinstance(v, dict) else v for k_, v in _degrade(light_template).items()}
        mid_t = (sh["start_s"] + sh["end_s"]) / 2
        per_shot.append({**sh, "representative_t": round(mid_t, 3), **lt,
                         "camera_angle": tag("undetermined (assume eye level)", 0.0, "estimated", PLACEHOLDER_NOTE),
                         "camera_height": tag("about eye level", 0.0, "estimated", PLACEHOLDER_NOTE),
                         "movement": shot_motion[si], "detailed": False,
                         "scene_geometry": tag({"assumed_hfov_deg": ASSUMED_HFOV_DEG, "subjects_m": []}, 0.0, "estimated",
                                               PLACEHOLDER_NOTE)})
    n_detailed = len(done)

    progress(0.86, "Listening to the sound")
    sound = guard("sound", lambda: audio.analyse(proxy_path, duration, bool(meta.get("audio_codec")), transcribe=True,
                                                 owner=owner, budget_s=budget),
                  lambda: {"present": tag(None, 0.0, "observed")})
    progress(0.89, "Looking for titles and credits")
    if collected.text_hits or decoder != "none":
        hits, step = collected.text_hits, ((samples[1].t - samples[0].t) if len(samples) > 1 else 1.0)
    else:
        hits = [h for h in (titles.frame_hit(t, img) for t, img in keyframes) if h]
        step = (keyframes[1][0] - keyframes[0][0]) if len(keyframes) > 1 else 1.0
    by_t = {round(t, 3): img for t, img in colour_samples}
    on_screen_text = guard("titles and credits",
                           lambda: titles.detect_from_hits(hits, step, duration,
                                                           frame_at=(lambda t: _frame_at(proxy_path, t)) if decoder != "keyframes" else None,
                                                           lang=lang, small_at=lambda t: by_t.get(round(t, 3))),
                           lambda: tag([], 0.0, "estimated"))

    progress(0.92, "Reading environment and mood")
    try:
        lum, cast = tone["mean_luminance"]["value"], tone["color_temperature"]["value"]["cast"]
    except Exception:  # noqa: BLE001
        lum, cast = 0.5, "neutral"
    env = guard("environment", lambda: environment.classify(pick, lum, cast),
                lambda: environment.classify([_NEUTRAL], 0.5, "neutral"))
    empty = [{"people": [], "faces": []}]
    overall_subjects = guard("subjects", lambda: subjects.summarise(all_dets or empty), lambda: subjects.summarise(empty))
    narr = guard("story and mood", lambda: narrative.interpret(
        shots=shots, duration=duration, motion=shot_motion, tone=tone, subjects=overall_subjects, env=env,
        composition=[s["composition"] for s in per_shot]),
        lambda: narrative.interpret(shots=shots, duration=duration, motion=[], tone=color.tone_and_color([_NEUTRAL]),
                                    subjects=subjects.summarise(empty), env=environment.classify([_NEUTRAL], 0.5, "neutral"),
                                    composition=[]))
    composition_changes = []
    for a, b in zip(per_shot, per_shot[1:]):
        if a["shot_size"]["value"] != b["shot_size"]["value"]:
            composition_changes.append(f"Shot {a['index'] + 1}→{b['index'] + 1}: {a['shot_size']['value']} → {b['shot_size']['value']}")
    progress(0.98, "Finalising")
    face_ok = subjects.face_model_available()
    limitations = [
        "Exact camera body, lens, focal length and lighting equipment cannot be recovered from video.",
        "Values marked 'estimated' come from models that can be wrong; 'heuristic' values are interpretations.",
    ]
    limitations += _limitations_engines(face_ok, sound, on_screen_text)
    if decoder == "keyframes":
        limitations.append("The clip could only be read through its extracted keyframes, so camera-motion and cut "
                           "detection are coarse.")
    if n_detailed < len(shots):
        how = ("spread evenly through the film plus the longest shots" if long_clip else "evenly spread representative shots")
        limitations.append(f"This video has {len(shots)} shots; framing and people were analysed in detail on "
                           f"{n_detailed} {how}. The others show cut timing and camera movement only." +
                           (" The time budget for detailed analysis was reached." if budget_hit else ""))
    if guard.failed:
        limitations.append("Some parts could not be measured on this clip and show neutral placeholders with zero "
                           "confidence: " + ", ".join(guard.failed) + ".")
    stt = (sound.get("speech_to_text") or {}).get("value") if isinstance(sound, dict) else None
    reading = on_screen_text.get("reading") if isinstance(on_screen_text, dict) else None
    detectors = list(ENGINE["detectors"]) + (["yunet-faces"] if face_ok else [])
    return {
        "engine": {**ENGINE, "detectors": detectors, "decoder": decoder, "failed_steps": guard.failed,
                   "detailed_shots": n_detailed,
                   "faces": "yunet" if face_ok else None,
                   "speech_to_text": stt if stt not in (None, "not available", "failed") else None,
                   "ocr": (reading or {}).get("engine") if (reading or {}).get("available") else None},
        "clip": {"duration_s": tag(duration, 1.0, "observed"),
                 "resolution": tag(f"{meta.get('width')}×{meta.get('height')}", 1.0, "observed"),
                 "fps": tag(meta.get("fps"), 1.0, "observed"), "aspect": tag(round(aspect, 3), 1.0, "observed"),
                 "codec": tag(meta.get("video_codec"), 1.0, "observed")},
        "shots": per_shot,
        "continuity": tag({"shot_count": len(shots),
                           "cuts_at_s": [s["start_s"] for s in shots[1:]],
                           "composition_changes": composition_changes}, 0.65, "estimated",
                          "Hard cuts are detected from colour-histogram changes; dissolves may be missed."),
        "color": {**tone, "palette": tag(pal, 0.9 if pal and "palette" not in guard.failed else 0.0, "observed",
                                         "Dominant colours (k-means in Lab) with screen share.")},
        "environment": env,
        "subjects": overall_subjects,
        "narrative": narr,
        "sound": sound,
        "on_screen_text": on_screen_text,
        "limitations": limitations,
    }
