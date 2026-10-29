"""Environment heuristics: indoor/outdoor, time of day, practical light candidates."""
from __future__ import annotations

import cv2
import numpy as np

from . import tag


def sky_and_foliage(bgr: np.ndarray) -> dict:
    small = cv2.resize(bgr, (160, int(160 * bgr.shape[0] / bgr.shape[1])))
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV).astype(np.float32)
    H, S, V = hsv[..., 0], hsv[..., 1] / 255, hsv[..., 2] / 255
    top = slice(0, small.shape[0] // 3)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    tex = np.abs(cv2.Laplacian(gray, cv2.CV_32F))
    smooth = tex[top] < 6
    blue_sky = (H[top] > 95) & (H[top] < 130) & (S[top] > 0.15) & (V[top] > 0.45) & smooth
    white_sky = (S[top] < 0.12) & (V[top] > 0.8) & smooth
    warm_sky = ((H[top] < 25) | (H[top] > 160)) & (S[top] > 0.25) & (V[top] > 0.5) & smooth
    foliage = (H > 33) & (H < 85) & (S > 0.25) & (V > 0.15)
    return {"blue_sky": float(blue_sky.mean()), "white_sky": float(white_sky.mean()), "warm_sky": float(warm_sky.mean()),
            "foliage": float(foliage.mean())}


def practicals(bgr: np.ndarray) -> list[dict]:
    h, w = bgr.shape[:2]
    y = cv2.cvtColor(bgr, cv2.COLOR_BGR2YCrCb)[..., 0]
    thr = max(235, float(np.percentile(y, 99.6)))
    if float(np.percentile(y, 50)) > 170:  # bright scene: blobs are more likely windows/sky than lamps
        return []
    mask = (y >= thr).astype(np.uint8)
    n, labels, stats, cent = cv2.connectedComponentsWithStats(mask, 8)
    out = []
    for i in range(1, n):
        x, yy, bw, bh, area = stats[i]
        share = area / (w * h)
        if 0.00015 < share < 0.02 and bw < w * 0.25 and bh < h * 0.3:
            region = bgr[labels == i]
            b, g, r = region.mean(axis=0)
            out.append({"x": round(float(cent[i][0] / w), 3), "y": round(float(cent[i][1] / h), 3),
                        "size": round(float(share), 4), "tint": "warm" if r > b + 12 else "cool" if b > r + 12 else "neutral"})
    out.sort(key=lambda p: -p["size"])
    return out[:6]


def classify(frames: list[np.ndarray], mean_lum: float, cast: str) -> dict:
    feats = [sky_and_foliage(f) for f in frames]
    sky = float(np.mean([f["blue_sky"] + f["white_sky"] + f["warm_sky"] for f in feats]))
    warm_sky = float(np.mean([f["warm_sky"] for f in feats]))
    foliage = float(np.mean([f["foliage"] for f in feats]))
    pr = [p for f in frames for p in practicals(f)]
    n_pr = len(pr) / max(1, len(frames))

    outdoor_score = 2.2 * sky + 1.2 * foliage - 0.25 * min(n_pr, 3)
    if outdoor_score > 0.35:
        env, econf = "outdoor", min(0.8, 0.45 + outdoor_score / 2)
    elif outdoor_score < 0.08 and (n_pr >= 0.5 or mean_lum < 0.5):
        env, econf = "indoor", 0.45 + (0.15 if n_pr >= 0.5 else 0)
    else:
        env, econf = "uncertain", 0.3

    if mean_lum < 0.2 and sky < 0.05:
        tod, tconf = "night", 0.5 + (0.15 if n_pr >= 0.5 else 0)
    elif warm_sky > 0.06 and mean_lum < 0.55:
        tod, tconf = "dawn / dusk (golden or blue hour)", 0.45
    elif sky > 0.08 and mean_lum > 0.4:
        tod, tconf = "day", 0.6
    elif env == "indoor":
        tod, tconf = "uncertain (interior)", 0.25
    else:
        tod, tconf = "uncertain", 0.2
    note = "Rule-based from sky/foliage colour, brightness and light sources; grading can mislead it."
    seen = {}
    for p in pr:  # dedupe across frames on a coarse grid
        seen.setdefault((round(p["x"], 1), round(p["y"], 1)), p)
    return {
        "setting": tag(env, econf, "heuristic", note),
        "time_of_day": tag(tod, tconf, "heuristic", note),
        "practical_lights": tag(list(seen.values())[:6], 0.4 if seen else 0.3, "estimated",
                                "Small very bright regions in an otherwise darker frame (lamps, screens, windows)."),
        "cues": tag({"sky_fraction_top": round(sky, 3), "foliage_fraction": round(foliage, 3),
                     "practicals_per_frame": round(n_pr, 2)}, 0.8, "observed"),
    }
