"""Orchestrates the full scene analysis for one clip."""
from __future__ import annotations

import logging
import math
from typing import Callable

import cv2
import numpy as np

from . import color, composition, environment, motion, narrative, subjects, tag

log = logging.getLogger("cinevision.analysis")

ENGINE = {"id": "cv-scene", "version": "1.0", "detectors": ["lk-flow+ransac", "hog-people", "spectral-residual"],
          "analysis_fps": 8}

ASSUMED_HFOV_DEG = 55.0   # typical "normal" focal length on Super35 (~24–28 mm); used only for proxy placement


MAX_SAMPLES = 900          # caps memory for long clips (~8 fps up to ~2 minutes, sparser beyond)
SAMPLE_W = 320


def _make_sample(t: float, small: np.ndarray) -> motion.Sample:
    return motion.Sample(t=t, gray=cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), hist=motion.hist_of(small), bgr_small=small)


def _sample_ffmpeg(path: str, duration: float, fps_target: float, aspect: float) -> list[motion.Sample]:
    """Decode with FFmpeg (the same decoder that prepared the clip), piping raw BGR frames — no OpenCV video I/O."""
    from ..config import settings
    from ..media import sandbox
    fps = min(fps_target, MAX_SAMPLES / max(duration, 0.5))
    h = max(2, int(round(SAMPLE_W / max(aspect, 0.1) / 2)) * 2)
    s = settings()
    res = sandbox.run([s.ffmpeg_bin, "-nostdin", "-hide_banner", "-loglevel", "error", "-protocol_whitelist", "file",
                       "-i", f"file:{path}", "-an", "-vf", f"fps={fps:.4f},scale={SAMPLE_W}:{h}:flags=area",
                       "-frames:v", str(MAX_SAMPLES), "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"],
                      sandbox.Limits(cpu_s=600, timeout_s=s.ffmpeg_timeout_s))
    size = SAMPLE_W * h * 3
    if res.returncode != 0 or len(res.stdout) < size:
        raise RuntimeError("ffmpeg sampling failed")
    buf = np.frombuffer(res.stdout, np.uint8)
    n = len(buf) // size
    return [_make_sample(i / fps, buf[i * size:(i + 1) * size].reshape(h, SAMPLE_W, 3).copy()) for i in range(n)]


def _sample_opencv(path: str, duration: float, fps_target: float) -> list[motion.Sample]:
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError("cannot open proxy for analysis")
    native = cap.get(cv2.CAP_PROP_FPS) or 24.0
    if not (1 <= native <= 240):
        native = 24.0
    step = max(1, int(round(native / min(fps_target, MAX_SAMPLES / max(duration, 0.5)))))
    out, i = [], 0
    while len(out) < MAX_SAMPLES and cap.grab():
        if i % step == 0:
            ok, frame = cap.retrieve()
            if ok and frame is not None and frame.size:
                small = cv2.resize(frame, (SAMPLE_W, max(2, int(SAMPLE_W * frame.shape[0] / frame.shape[1]))),
                                   interpolation=cv2.INTER_AREA)
                out.append(_make_sample(i / native, small))
        i += 1
    cap.release()
    if not out:
        raise RuntimeError("opencv decoded no frames")
    return out


def _sample_keyframes(keyframes: list[tuple[float, np.ndarray]]) -> list[motion.Sample]:
    out = []
    for t, img in keyframes:
        small = cv2.resize(img, (SAMPLE_W, max(2, int(SAMPLE_W * img.shape[0] / img.shape[1]))), interpolation=cv2.INTER_AREA)
        out.append(_make_sample(t, small))
    return out


def _sample_video(path: str, duration: float, fps_target: float, aspect: float,
                  keyframes: list[tuple[float, np.ndarray]]) -> tuple[list[motion.Sample], str]:
    """Try FFmpeg, then OpenCV, then fall back to the (sparser) keyframes extracted at ingest. Never raises."""
    for name, fn in (("ffmpeg", lambda: _sample_ffmpeg(path, duration, fps_target, aspect)),
                     ("opencv", lambda: _sample_opencv(path, duration, fps_target))):
        try:
            return fn(), name
        except Exception as e:  # noqa: BLE001 - fall through to the next decoder
            log.warning("frame sampling via %s failed: %s", name, type(e).__name__)
    try:
        return _sample_keyframes(keyframes), "keyframes"
    except Exception as e:  # noqa: BLE001
        log.warning("keyframe sampling failed: %s", type(e).__name__)
        return [], "none"


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


def run(proxy_path: str, keyframes: list[tuple[float, np.ndarray]], meta: dict,
        progress: Callable[[float, str], None]) -> dict:
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
    samples, decoder = _sample_video(proxy_path, duration, ENGINE["analysis_fps"], aspect, keyframes)
    if not samples and not keyframes:
        from ..worker import UserFacingError
        raise UserFacingError("No picture could be decoded from this clip, so it can't be analysed. "
                              "Try exporting it again as MP4 (H.264) and uploading that.")
    progress(0.22, "Detecting shot boundaries")
    shots = guard("shot boundaries", lambda: motion.detect_shots(samples, duration),
                  lambda: [{"index": 0, "start_s": 0.0, "end_s": duration}])
    if not shots:
        shots = [{"index": 0, "start_s": 0.0, "end_s": duration}]

    progress(0.3, "Estimating camera motion")
    shot_motion = []
    for si, sh in enumerate(shots):
        def _mo(sh=sh):
            sm = [s for s in samples if sh["start_s"] <= s.t < sh["end_s"]]
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
        progress(0.3 + 0.3 * (si + 1) / len(shots), "Estimating camera motion")

    progress(0.62, "Analysing colour and exposure")
    kf_imgs = [img for _, img in keyframes] or [s.bgr_small for s in samples[:: max(1, len(samples) // 12)]]
    pick = kf_imgs[:: max(1, len(kf_imgs) // 24)]
    tone = guard("light and colour", lambda: color.tone_and_color(pick), lambda: color.tone_and_color([_NEUTRAL]))
    pal = guard("palette", lambda: color.palette(pick), lambda: [])

    progress(0.7, "Detecting subjects and composition")
    per_shot = []
    all_dets = []
    # keyframes to use for subject/composition work: ingest keyframes, else the decoded samples
    frames_for = keyframes or [(s.t, s.bgr_small) for s in samples]
    for si, sh in enumerate(shots):
        kfs = [(t, img) for t, img in frames_for if sh["start_s"] <= t < sh["end_s"]] or \
              [min(frames_for, key=lambda k: abs(k[0] - (sh["start_s"] + sh["end_s"]) / 2))]
        mid = min(kfs, key=lambda k: abs(k[0] - (sh["start_s"] + sh["end_s"]) / 2))

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
            for _, img in kfs[:: max(1, len(kfs) // 6)]:
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
        per_shot.append({
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
        })
        progress(0.7 + 0.2 * (si + 1) / len(shots), "Detecting subjects and composition")

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
    limitations = [
        "Exact camera body, lens, focal length and lighting equipment cannot be recovered from video.",
        "Values marked 'estimated' come from models that can be wrong; 'heuristic' values are interpretations.",
        "Face detection is " + ("enabled." if subjects.face_model_available() else "not configured, so shot sizes rely on body detection or saliency."),
    ]
    if decoder == "keyframes":
        limitations.append("The clip could only be read through its extracted keyframes, so camera-motion and cut "
                           "detection are coarse.")
    if guard.failed:
        limitations.append("Some parts could not be measured on this clip and show neutral placeholders with zero "
                           "confidence: " + ", ".join(guard.failed) + ".")
    return {
        "engine": {**ENGINE, "decoder": decoder, "failed_steps": guard.failed},
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
        "limitations": limitations,
    }
