"""Shot boundaries and global camera motion.

Camera motion is modelled per frame pair as a similarity transform (scale, rotation, translation) fitted with
RANSAC to Lucas–Kanade tracked corners. Points that disagree with the global model are attributed to subject
motion. This separates camera from subject motion only approximately — large moving subjects can bias it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np

from . import tag


@dataclass
class Sample:
    t: float
    gray: np.ndarray            # ~320px wide
    hist: np.ndarray
    bgr_small: np.ndarray


@dataclass
class PairMotion:
    t: float
    dt: float
    tx: float                   # fraction of frame width (content motion)
    ty: float                   # fraction of frame height
    scale: float                # >1 = content grows
    rot_deg: float
    inlier_ratio: float
    n_points: int
    subject_motion: float       # median outlier displacement (fraction of width) per second
    parallax: float             # inlier residual RMS (fraction of width)


@dataclass
class ShotMotion:
    pairs: list[PairMotion] = field(default_factory=list)


def hist_of(bgr_small: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(bgr_small, cv2.COLOR_BGR2HSV)
    h = cv2.calcHist([hsv], [0, 1, 2], None, [16, 6, 6], [0, 180, 0, 256, 0, 256])
    return cv2.normalize(h, h).flatten()


def detect_shots(samples: list[Sample], duration: float, min_len: float = 0.35) -> list[dict]:
    if not samples:
        return [{"index": 0, "start_s": 0.0, "end_s": duration}]
    d = np.array([cv2.compareHist(samples[i - 1].hist, samples[i].hist, cv2.HISTCMP_BHATTACHARYYA)
                  for i in range(1, len(samples))]) if len(samples) > 1 else np.array([])
    cuts = []
    if d.size:
        med = float(np.median(d))
        mad = float(np.median(np.abs(d - med))) + 1e-6
        thr = max(0.35, med + 8 * mad)
        for i, v in enumerate(d, start=1):
            if v > thr and (not cuts or samples[i].t - cuts[-1] >= min_len):
                cuts.append(samples[i].t)
    bounds = [0.0, *cuts, duration]
    return [{"index": i, "start_s": round(bounds[i], 3), "end_s": round(bounds[i + 1], 3)} for i in range(len(bounds) - 1)]


def pair_motion(a: Sample, b: Sample) -> PairMotion | None:
    h, w = a.gray.shape
    p0 = cv2.goodFeaturesToTrack(a.gray, maxCorners=400, qualityLevel=0.01, minDistance=6, blockSize=7)
    if p0 is None or len(p0) < 12:
        return None
    p1, st, _ = cv2.calcOpticalFlowPyrLK(a.gray, b.gray, p0, None, winSize=(21, 21), maxLevel=3,
                                         criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01))
    if p1 is None:
        return None
    ok = st.ravel() == 1
    p0, p1 = p0[ok].reshape(-1, 2), p1[ok].reshape(-1, 2)
    if len(p0) < 12:
        return None
    M, inl = cv2.estimateAffinePartial2D(p0, p1, method=cv2.RANSAC, ransacReprojThreshold=2.0, maxIters=2000,
                                         confidence=0.99)
    if M is None:
        return None
    inl = inl.ravel().astype(bool)
    # express translation about the frame centre so zoom doesn't masquerade as pan
    c = np.array([w / 2, h / 2])
    centre_moved = M[:, :2] @ c + M[:, 2]
    tx, ty = (centre_moved - c)
    scale = math.hypot(M[0, 0], M[1, 0])
    rot = math.degrees(math.atan2(M[1, 0], M[0, 0]))
    pred = (M[:, :2] @ p0.T).T + M[:, 2]
    resid = np.linalg.norm(p1 - pred, axis=1)
    dt = max(1e-3, b.t - a.t)
    subj = float(np.median(resid[~inl])) / w / dt if (~inl).sum() >= 5 else 0.0
    parallax = float(np.sqrt(np.mean(resid[inl] ** 2))) / w if inl.sum() else 0.0
    return PairMotion(t=a.t, dt=dt, tx=tx / w, ty=ty / h, scale=scale, rot_deg=rot,
                      inlier_ratio=float(inl.mean()), n_points=int(len(p0)), subject_motion=subj, parallax=parallax)


def classify(pairs: list[PairMotion]) -> dict:
    """Aggregate per-pair motion within a shot into a labelled camera-movement estimate."""
    if len(pairs) < 2:
        return {"movement": tag("undetermined", 0.2, "estimated", "Too little texture or too few frames to track."),
                "camera_vs_subject": None, "metrics": None}
    dts = np.array([p.dt for p in pairs])
    T = dts.sum()
    pan = -np.array([p.tx for p in pairs])        # camera pans right → content moves left
    tilt = -np.array([p.ty for p in pairs])       # camera tilts up → content moves down (image y grows down)
    zoom = np.log(np.array([p.scale for p in pairs]))
    roll = np.array([p.rot_deg for p in pairs])
    pan_rate, tilt_rate = pan.sum() / T, tilt.sum() / T          # frame widths/heights per second
    zoom_rate, roll_rate = zoom.sum() / T, roll.sum() / T        # log-scale/s, deg/s

    def jitter(x):
        if len(x) < 4:
            return 0.0
        hp = x - np.convolve(x, np.ones(3) / 3, mode="same")
        return float(np.std(hp[1:-1]))

    jit = max(jitter(pan), jitter(tilt))
    subj = float(np.median([p.subject_motion for p in pairs]))
    parallax = float(np.median([p.parallax for p in pairs]))
    inlier = float(np.mean([p.inlier_ratio for p in pairs]))

    comps = {"pan": abs(pan_rate), "tilt": abs(tilt_rate) * 0.9, "push": abs(zoom_rate) * 1.6, "roll": abs(roll_rate) / 40}
    main, mag = max(comps.items(), key=lambda kv: kv[1])
    handheld = jit > 0.0035
    if mag < 0.012 and not handheld:
        label = "static (locked-off)"
    elif mag < 0.012 and handheld:
        label = "handheld (no dominant move)"
    else:
        if main == "pan":
            label = f"pan {'right' if pan_rate > 0 else 'left'}"
        elif main == "tilt":
            label = f"tilt {'up' if tilt_rate > 0 else 'down'}"
        elif main == "push":
            direction = "in" if zoom_rate > 0 else "out"
            label = (f"zoom {direction}" if parallax < 0.0025 else f"dolly / push {direction}")
        else:
            label = f"roll {'clockwise' if roll_rate > 0 else 'counter-clockwise'}"
        if main == "pan" and subj < 0.02 and inlier < 0.75:
            label += " (possibly tracking a subject)"
        if handheld:
            label += ", handheld"
    speed = "slow" if mag < 0.05 else "moderate" if mag < 0.18 else "fast"
    conf = 0.45 + 0.4 * min(1.0, inlier) - (0.15 if len(pairs) < 5 else 0)
    note = "Optical-flow estimate. Zoom vs dolly is inferred from parallax and can be confused on flat scenes."
    return {
        "movement": tag(label, conf, "estimated", note),
        "speed": tag(speed, conf * 0.9, "estimated"),
        "handheld": tag(handheld, 0.55, "estimated"),
        "camera_vs_subject": tag({"camera_motion": round(float(mag), 4), "subject_motion": round(subj, 4),
                                  "dominant": "camera" if mag > subj else "subject" if subj > 0.01 else "neither"},
                                 0.5, "estimated", "Units: frame-widths per second."),
        "metrics": {"pan_rate": round(float(pan_rate), 4), "tilt_rate": round(float(tilt_rate), 4),
                    "zoom_rate": round(float(zoom_rate), 4), "roll_rate_deg_s": round(float(roll_rate), 3),
                    "jitter": round(jit, 5), "parallax": round(parallax, 5), "inlier_ratio": round(inlier, 3),
                    "pairs": len(pairs)},
    }
