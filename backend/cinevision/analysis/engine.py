"""Orchestrates the full scene analysis for one clip."""
from __future__ import annotations

import math
from typing import Callable

import cv2
import numpy as np

from . import color, composition, environment, motion, narrative, subjects, tag

ENGINE = {"id": "cv-scene", "version": "1.0", "detectors": ["lk-flow+ransac", "hog-people", "spectral-residual"],
          "analysis_fps": 8}

ASSUMED_HFOV_DEG = 55.0   # typical "normal" focal length on Super35 (~24–28 mm); used only for proxy placement


def _sample_video(path: str, duration: float, fps_target: float, progress: Callable[[float], None]) -> list[motion.Sample]:
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise RuntimeError("cannot open proxy for analysis")
    native = cap.get(cv2.CAP_PROP_FPS) or 24.0
    step = max(1, int(round(native / fps_target)))
    out, i = [], 0
    total = max(1, int(duration * native))
    while True:
        ok = cap.grab()
        if not ok:
            break
        if i % step == 0:
            ok, frame = cap.retrieve()
            if ok:
                w = 320
                small = cv2.resize(frame, (w, max(1, int(w * frame.shape[0] / frame.shape[1]))), interpolation=cv2.INTER_AREA)
                out.append(motion.Sample(t=i / native, gray=cv2.cvtColor(small, cv2.COLOR_BGR2GRAY),
                                         hist=motion.hist_of(small), bgr_small=small))
            if len(out) % 20 == 0:
                progress(min(1.0, i / total))
        i += 1
    cap.release()
    return out


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
    duration = float(meta["duration_s"])
    aspect = meta["display_aspect"] or (meta["width"] / meta["height"])
    progress(0.02, "Sampling frames")
    samples = _sample_video(proxy_path, duration, ENGINE["analysis_fps"], lambda f: progress(0.02 + 0.2 * f, "Sampling frames"))
    progress(0.25, "Detecting shot boundaries")
    shots = motion.detect_shots(samples, duration)

    progress(0.3, "Estimating camera motion")
    shot_motion = []
    for si, sh in enumerate(shots):
        sm = [s for s in samples if sh["start_s"] <= s.t < sh["end_s"]]
        pairs = [pm for a, b in zip(sm, sm[1:]) if (pm := motion.pair_motion(a, b)) is not None]
        shot_motion.append(motion.classify(pairs))
        progress(0.3 + 0.3 * (si + 1) / len(shots), "Estimating camera motion")

    progress(0.62, "Analysing colour and exposure")
    kf_imgs = [img for _, img in keyframes] or [s.bgr_small for s in samples[:: max(1, len(samples) // 12)]]
    pick = kf_imgs[:: max(1, len(kf_imgs) // 24)]
    tone = color.tone_and_color(pick)
    pal = color.palette(pick)

    progress(0.7, "Detecting subjects and composition")
    per_shot = []
    all_dets = []
    for si, sh in enumerate(shots):
        kfs = [(t, img) for t, img in keyframes if sh["start_s"] <= t < sh["end_s"]] or \
              [min(keyframes, key=lambda k: abs(k[0] - (sh["start_s"] + sh["end_s"]) / 2))] if keyframes else []
        if not kfs:
            continue
        mid = min(kfs, key=lambda k: abs(k[0] - (sh["start_s"] + sh["end_s"]) / 2))
        comp = composition.analyse_frame(mid[1])
        sal = comp.pop("_saliency")
        dets = [subjects.detect(img) for _, img in kfs[:: max(1, len(kfs) // 6)]]
        all_dets.extend(dets)
        subj = subjects.summarise(dets)
        size = subjects.shot_size(dets, float((sal > 0.35).mean()))
        angle, aconf = _angle_label(comp["camera_tilt"]["value"], comp["horizon_roll_deg"]["value"])
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
            "scene_geometry": tag({"assumed_hfov_deg": ASSUMED_HFOV_DEG,
                                   "subjects_m": _place_subjects(subj["positions"]["value"], aspect)}, 0.2, "estimated",
                                  "Rough metres from camera assuming average human size and a ~55° lens. Use for previs only."),
        })
        progress(0.7 + 0.2 * (si + 1) / len(shots), "Detecting subjects and composition")

    progress(0.92, "Reading environment and mood")
    env = environment.classify(pick, tone["mean_luminance"]["value"], tone["color_temperature"]["value"]["cast"])
    overall_subjects = subjects.summarise(all_dets) if all_dets else subjects.summarise([{"people": [], "faces": []}])
    narr = narrative.interpret(shots=shots, duration=duration, motion=shot_motion, tone=tone, subjects=overall_subjects,
                               env=env, composition=[s["composition"] for s in per_shot])
    composition_changes = []
    for a, b in zip(per_shot, per_shot[1:]):
        if a["shot_size"]["value"] != b["shot_size"]["value"]:
            composition_changes.append(f"Shot {a['index'] + 1}→{b['index'] + 1}: {a['shot_size']['value']} → {b['shot_size']['value']}")
    progress(0.98, "Finalising")
    return {
        "engine": ENGINE,
        "clip": {"duration_s": tag(duration, 1.0, "observed"), "resolution": tag(f"{meta['width']}×{meta['height']}", 1.0, "observed"),
                 "fps": tag(meta["fps"], 1.0, "observed"), "aspect": tag(round(aspect, 3), 1.0, "observed"),
                 "codec": tag(meta["video_codec"], 1.0, "observed")},
        "shots": per_shot,
        "continuity": tag({"shot_count": len(shots),
                           "cuts_at_s": [s["start_s"] for s in shots[1:]],
                           "composition_changes": composition_changes}, 0.65, "estimated",
                          "Hard cuts are detected from colour-histogram changes; dissolves may be missed."),
        "color": {**tone, "palette": tag(pal, 0.9, "observed", "Dominant colours (k-means in Lab) with screen share.")},
        "environment": env,
        "subjects": overall_subjects,
        "narrative": narr,
        "limitations": [
            "Exact camera body, lens, focal length and lighting equipment cannot be recovered from video.",
            "Values marked 'estimated' come from models that can be wrong; 'heuristic' values are interpretations.",
            "Face detection is " + ("enabled." if subjects.face_model_available() else "not configured, so shot sizes rely on body detection or saliency."),
        ],
    }
