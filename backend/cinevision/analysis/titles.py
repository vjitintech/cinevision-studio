"""On-screen text detection (opening titles, captions/lower thirds, end credits) from sampled frames.

Classic text-region detection: morphological gradient → binarise → horizontal closing joins letters into words →
keep wide, short, dense boxes. It finds text-like regions; it does not read the words.
"""
from __future__ import annotations

import cv2
import numpy as np

from . import tag

_K_GRAD = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))


def text_boxes(bgr: np.ndarray) -> list[tuple[int, int, int, int]]:
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    grad = cv2.morphologyEx(gray, cv2.MORPH_GRADIENT, _K_GRAD)
    _, bw = cv2.threshold(grad, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    if bw.mean() > 255 * 0.35:                      # busy texture everywhere: not a text frame
        return []
    closed = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (max(5, w // 40), 1)))
    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in contours:
        x, y, bw_, bh = cv2.boundingRect(c)
        if bh < max(4, h * 0.015) or bh > h * 0.14 or bw_ < bh * 2.2 or bw_ > w * 0.95:
            continue
        fill = cv2.countNonZero(bw[y:y + bh, x:x + bw_]) / float(bw_ * bh)
        if 0.18 < fill < 0.75:
            out.append((x, y, bw_, bh))
    return out


def detect(samples: list[tuple[float, np.ndarray]], duration: float) -> dict:
    """samples: (time, bgr) in time order."""
    hits = []
    for t, img in samples:
        boxes = text_boxes(img)
        h = img.shape[0]
        lower = [b for b in boxes if b[1] > h * 0.72]
        if len(boxes) >= 3 or (len(boxes) >= 1 and len(lower) == len(boxes)):
            hits.append((t, len(boxes), len(lower) == len(boxes)))
    step = (samples[1][0] - samples[0][0]) if len(samples) > 1 else 1.0
    segs: list[dict] = []
    for t, n, lower in hits:
        if segs and t - segs[-1]["end_s"] <= step * 2.5:
            segs[-1]["end_s"] = round(t + step, 2)
            segs[-1]["max_lines"] = max(segs[-1]["max_lines"], n)
            segs[-1]["lower_only"] = segs[-1]["lower_only"] and lower
        else:
            segs.append({"start_s": round(t, 2), "end_s": round(t + step, 2), "max_lines": n, "lower_only": lower})
    for s in segs:
        if s["start_s"] <= max(2.0, duration * 0.08):
            s["kind"] = "opening titles"
        elif s["end_s"] >= duration - max(2.0, duration * 0.08):
            s["kind"] = "end credits"
        elif s["lower_only"]:
            s["kind"] = "caption / lower third / subtitles"
        else:
            s["kind"] = "on-screen text"
        s["on_screen_s"] = round(s["end_s"] - s["start_s"], 2)
        del s["lower_only"]
    # real captions and title cards stay up for at least a second; shorter hits are usually texture
    segs = [s for s in segs if s["on_screen_s"] >= 0.9 or s["kind"] in ("opening titles", "end credits") and s["max_lines"] >= 2]
    return tag(segs[:100], 0.45, "estimated",
               "Text-like regions found in sampled frames; the words themselves are not read. Busy textures can be "
               "mistaken for text and very thin fonts can be missed.")
