from __future__ import annotations

import cv2
import numpy as np

from . import tag


def _hex(bgr) -> str:
    b, g, r = (int(round(x)) for x in bgr)
    return f"#{r:02x}{g:02x}{b:02x}"


def palette(frames_bgr: list[np.ndarray], k: int = 5) -> list[dict]:
    px = np.concatenate([cv2.resize(f, (96, int(96 * f.shape[0] / f.shape[1]))).reshape(-1, 3) for f in frames_bgr])
    lab = cv2.cvtColor(px.reshape(-1, 1, 3), cv2.COLOR_BGR2LAB).reshape(-1, 3).astype(np.float32)
    k = min(k, max(1, len(np.unique(px, axis=0))))
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.5)
    cv2.setRNGSeed(7)
    _, labels, centers = cv2.kmeans(lab, k, None, crit, 3, cv2.KMEANS_PP_CENTERS)
    counts = np.bincount(labels.ravel(), minlength=k) / len(labels)
    bgr = cv2.cvtColor(centers.reshape(-1, 1, 3).astype(np.uint8), cv2.COLOR_LAB2BGR).reshape(-1, 3)
    order = np.argsort(-counts)
    return [{"hex": _hex(bgr[i]), "share": round(float(counts[i]), 3)} for i in order]


def tone_and_color(frames_bgr: list[np.ndarray]) -> dict:
    ys, sats, a_s, b_s = [], [], [], []
    for f in frames_bgr:
        small = cv2.resize(f, (160, int(160 * f.shape[0] / f.shape[1])))
        ycc = cv2.cvtColor(small, cv2.COLOR_BGR2YCrCb)
        ys.append(ycc[..., 0].ravel())
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        sats.append(hsv[..., 1].ravel())
        lab = cv2.cvtColor(small, cv2.COLOR_BGR2LAB).astype(np.int16)
        mid = (lab[..., 0] > 60) & (lab[..., 0] < 220)
        a_s.append((lab[..., 1][mid] - 128).ravel())
        b_s.append((lab[..., 2][mid] - 128).ravel())
    y = np.concatenate(ys).astype(np.float32) / 255.0
    sat = float(np.concatenate(sats).mean() / 255.0)
    a = float(np.concatenate(a_s).mean()) if sum(len(x) for x in a_s) else 0.0
    b = float(np.concatenate(b_s).mean()) if sum(len(x) for x in b_s) else 0.0
    mean, p5, p50, p95 = float(y.mean()), *(float(np.percentile(y, q)) for q in (5, 50, 95))
    contrast = p95 - p5
    clip_hi, clip_lo = float((y > 0.98).mean()), float((y < 0.02).mean())
    hist = np.histogram(y, bins=10, range=(0, 1))[0] / len(y)

    if mean > 0.58 and contrast < 0.65:
        key = "high-key"
    elif mean < 0.32 and p95 > 0.45:
        key = "low-key"
    elif mean < 0.25:
        key = "dark / underexposed"
    else:
        key = "mid-key"
    if b > 6 and b > abs(a) * 0.6:
        temp, cct = "warm", "≈3200–4300 K look"
    elif b < -5:
        temp, cct = "cool", "≈6000–8000 K look"
    elif a > 8:
        temp, cct = "magenta-shifted", "mixed / stylised"
    elif a < -8:
        temp, cct = "green-shifted", "mixed / fluorescent-like"
    else:
        temp, cct = "neutral", "≈5000–5600 K look"
    contrast_label = "high" if contrast > 0.75 else "low" if contrast < 0.4 else "medium"
    return {
        "mean_luminance": tag(round(mean, 3), 0.95, "observed"),
        "median_luminance": tag(round(p50, 3), 0.95, "observed"),
        "contrast": tag({"range_p5_p95": round(contrast, 3), "label": contrast_label}, 0.9, "observed"),
        "clipping": tag({"highlights": round(clip_hi, 4), "shadows": round(clip_lo, 4)}, 0.95, "observed"),
        "histogram": tag([round(float(x), 4) for x in hist], 0.95, "observed"),
        "saturation": tag({"mean": round(sat, 3), "label": "rich" if sat > 0.45 else "muted" if sat < 0.2 else "moderate"},
                          0.9, "observed"),
        "tonal_key": tag(key, 0.7, "estimated", "From luminance distribution of the graded footage, not the set."),
        "color_temperature": tag({"cast": temp, "approx": cct, "lab_a": round(a, 1), "lab_b": round(b, 1)}, 0.55,
                                 "estimated", "Apparent colour cast in the footage; camera white balance and grade both affect it."),
    }
