"""Composition descriptors from a representative frame."""
from __future__ import annotations

import math

import cv2
import numpy as np

from . import tag


def spectral_residual_saliency(bgr: np.ndarray) -> np.ndarray:
    """Hou & Zhang (2007) spectral residual saliency, returned at 64px width, normalised 0..1."""
    g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    h = int(64 * g.shape[0] / g.shape[1]) or 1
    small = cv2.resize(g, (64, h)).astype(np.float32)
    F = np.fft.fft2(small)
    amp, phase = np.abs(F), np.angle(F)
    log_amp = np.log(amp + 1e-8)
    residual = log_amp - cv2.blur(log_amp, (3, 3))
    sal = np.abs(np.fft.ifft2(np.exp(residual + 1j * phase))) ** 2
    sal = cv2.GaussianBlur(sal.astype(np.float32), (0, 0), 2.5)
    rng = sal.max() - sal.min()
    return (sal - sal.min()) / rng if rng > 1e-9 else np.zeros_like(sal)


def _lines(gray: np.ndarray) -> np.ndarray:
    edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 60, 160)
    w = gray.shape[1]
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=60, minLineLength=int(w * 0.12), maxLineGap=8)
    return lines.reshape(-1, 4).astype(np.float32) if lines is not None else np.zeros((0, 4), np.float32)


def _vanishing_point(lines: np.ndarray, w: int, h: int):
    """Least-squares intersection of oblique lines (RANSAC over pairs)."""
    obl = []
    for x1, y1, x2, y2 in lines:
        ang = abs(math.degrees(math.atan2(y2 - y1, x2 - x1))) % 180
        if 12 < ang < 78 or 102 < ang < 168:
            obl.append((x1, y1, x2, y2))
    if len(obl) < 4:
        return None, len(obl)
    L = np.array([np.cross([x1, y1, 1.0], [x2, y2, 1.0]) for x1, y1, x2, y2 in obl])
    L /= np.linalg.norm(L[:, :2], axis=1, keepdims=True) + 1e-9
    rng = np.random.default_rng(3)
    best, best_in = None, 0
    for _ in range(min(200, len(L) * 4)):
        i, j = rng.choice(len(L), 2, replace=False)
        p = np.cross(L[i], L[j])
        if abs(p[2]) < 1e-9:
            continue
        p = p / p[2]
        d = np.abs(L @ p)
        inl = int((d < 0.02 * w).sum())
        if inl > best_in:
            best, best_in = p, inl
    if best is None or best_in < 4:
        return None, len(obl)
    return (float(best[0] / w), float(best[1] / h)), best_in


def analyse_frame(bgr: np.ndarray) -> dict:
    h, w = bgr.shape[:2]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    sal = spectral_residual_saliency(bgr)
    sh, sw = sal.shape
    ys, xs = np.mgrid[0:sh, 0:sw]
    weight = np.clip(sal - 0.25, 0, None)
    if weight.sum() > 1e-6:
        cx, cy = float((xs * weight).sum() / weight.sum() / sw), float((ys * weight).sum() / weight.sum() / sh)
    else:
        cx, cy = 0.5, 0.5
    thirds = [(a, b) for a in (1 / 3, 2 / 3) for b in (1 / 3, 2 / 3)]
    d_third = min(math.hypot(cx - a, cy - b) for a, b in thirds)
    d_centre = math.hypot(cx - 0.5, cy - 0.5)
    placement = "on a rule-of-thirds intersection" if d_third < 0.09 else "centred" if d_centre < 0.08 else \
        "off-centre"
    negative_space = float((sal < 0.15).mean())

    edges = cv2.Canny(gray, 50, 150).astype(np.float32)
    flipped = edges[:, ::-1]
    sym = 1.0 - float(np.abs(edges - flipped).mean() / (edges.mean() * 2 + 1e-6))
    sym = max(0.0, min(1.0, sym))

    lines = _lines(gray)
    vp, vp_support = _vanishing_point(lines, w, h)
    horiz = [math.degrees(math.atan2(y2 - y1, x2 - x1)) for x1, y1, x2, y2 in lines
             if abs(math.degrees(math.atan2(y2 - y1, x2 - x1))) < 12 and math.hypot(x2 - x1, y2 - y1) > w * 0.2]
    roll = float(np.median(horiz)) if len(horiz) >= 2 else None
    leading = 0
    for x1, y1, x2, y2 in lines:
        a, b = np.array([x1 / w, y1 / h]), np.array([x2 / w, y2 / h])
        d = b - a
        if np.linalg.norm(d) < 1e-6:
            continue
        p = np.array([cx, cy])
        q = p - a
        dist = abs(d[0] * q[1] - d[1] * q[0]) / np.linalg.norm(d)
        ang = abs(math.degrees(math.atan2(d[1], d[0]))) % 180
        if dist < 0.06 and 10 < ang < 170 and not (80 < ang < 100):
            leading += 1

    # depth cue: sharpness in horizontal bands (bottom ≈ foreground, top ≈ background in most shots)
    lap = np.abs(cv2.Laplacian(gray, cv2.CV_32F))
    bands = [float(lap[int(h * i / 3):int(h * (i + 1) / 3)].mean()) for i in range(3)]  # top, mid, bottom
    sharp_sal = float(lap[cv2.resize((sal > 0.5).astype(np.uint8), (w, h)) > 0].mean()) if (sal > 0.5).any() else 0.0
    sharp_bg = float(lap[cv2.resize((sal < 0.15).astype(np.uint8), (w, h)) > 0].mean()) if (sal < 0.15).any() else 0.0
    sep = sharp_sal / (sharp_bg + 1e-6) if sharp_bg > 0 else 1.0
    dof = "shallow (subject sharper than background)" if sep > 2.2 else "deep / everything similarly sharp" if sep < 1.3 \
        else "moderate"

    perspective = "unclear"
    if vp is not None:
        vx, vy = vp
        inside = -0.25 < vx < 1.25 and -0.25 < vy < 1.25
        perspective = "strong one-point convergence (wide-angle character)" if inside and vp_support >= 6 else \
            "converging lines toward a distant point" if inside else "weak convergence (longer-lens / compressed character)"
    tilt_hint = None
    if vp is not None and -0.5 < vp[0] < 1.5:
        tilt_hint = "camera likely tilted down (horizon above centre)" if vp[1] < 0.38 else \
            "camera likely tilted up (horizon below centre)" if vp[1] > 0.62 else "camera roughly level"

    return {
        "subject_centroid": tag({"x": round(cx, 3), "y": round(cy, 3)}, 0.5, "estimated",
                                "Visual-attention centroid (saliency), not a detected person."),
        "placement": tag(placement, 0.5, "estimated"),
        "negative_space": tag(round(negative_space, 3), 0.55, "estimated", "Share of the frame with low visual saliency."),
        "symmetry": tag(round(sym, 3), 0.6, "observed", "Left–right edge mirror similarity (1 = perfectly symmetric)."),
        "leading_lines": tag(leading, 0.45, "estimated", "Straight lines pointing toward the attention centroid."),
        "vanishing_point": tag({"x": round(vp[0], 3), "y": round(vp[1], 3)} if vp else None, 0.4 if vp else 0.2,
                               "estimated"),
        "perspective": tag(perspective, 0.35, "estimated", "Lens character inferred from line convergence only."),
        "horizon_roll_deg": tag(round(roll, 2) if roll is not None else None, 0.5 if roll is not None else 0.1, "estimated",
                                "Positive = clockwise tilt (Dutch angle) of near-horizontal lines."),
        "camera_tilt": tag(tilt_hint or "undetermined", 0.35 if tilt_hint else 0.1, "estimated"),
        "depth_of_field": tag(dof, 0.4, "estimated", "Sharpness of salient vs low-salience regions."),
        "depth_layers": tag({"top_detail": round(bands[0], 2), "middle_detail": round(bands[1], 2),
                             "bottom_detail": round(bands[2], 2)}, 0.35, "estimated",
                            "Edge detail per horizontal band; lower bands are usually foreground."),
        "_saliency": sal,
    }
