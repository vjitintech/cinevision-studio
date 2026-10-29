"""Subject detection.

* People: OpenCV HOG + linear SVM pedestrian detector (bundled with OpenCV). Works for visible bodies at
  medium/wide sizes; it misses close-ups and seated/occluded people.
* Faces: optional YuNet model (`CINEVISION_FACE_MODEL` = path to face_detection_yunet_*.onnx). Without it,
  face-based shot sizing is reported as unavailable instead of guessed.
"""
from __future__ import annotations

import os

import cv2
import numpy as np

from . import tag

_hog = None
_face = None
_face_loaded = False


def _get_hog():
    global _hog
    if _hog is None:
        _hog = cv2.HOGDescriptor()
        _hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
    return _hog


def face_model_available() -> bool:
    return _get_face(320, 240) is not None


def _get_face(w, h):
    global _face, _face_loaded
    if not _face_loaded:
        _face_loaded = True
        path = os.environ.get("CINEVISION_FACE_MODEL")
        if path and os.path.isfile(path) and hasattr(cv2, "FaceDetectorYN"):
            try:
                _face = cv2.FaceDetectorYN.create(path, "", (w, h), 0.7, 0.3, 50)
            except cv2.error:
                _face = None
    if _face is not None:
        _face.setInputSize((w, h))
    return _face


def _nms(boxes: list, thr=0.4) -> list:
    if not boxes:
        return []
    b = np.array([x[:4] for x in boxes], np.float32)
    s = np.array([x[4] for x in boxes], np.float32)
    idx = cv2.dnn.NMSBoxes(b.tolist(), s.tolist(), 0.0, thr) if hasattr(cv2, "dnn") else range(len(boxes))
    return [boxes[i] for i in np.array(idx).ravel()]


def detect(bgr: np.ndarray) -> dict:
    h, w = bgr.shape[:2]
    scale = 640 / w if w > 640 else 1.0
    img = cv2.resize(bgr, (int(w * scale), int(h * scale))) if scale != 1 else bgr
    ih, iw = img.shape[:2]
    rects, weights = _get_hog().detectMultiScale(img, winStride=(8, 8), padding=(8, 8), scale=1.06)
    people = []
    for (x, y, rw, rh), sc in zip(rects, np.ravel(weights) if len(rects) else []):
        if sc > 0.7 and rh / max(rw, 1) > 1.6:
            people.append([float(x), float(y), float(rw), float(rh), float(sc)])
    people = _nms(people)
    faces = []
    fd = _get_face(iw, ih)
    if fd is not None:
        _, dets = fd.detect(img)
        for d in (dets if dets is not None else []):
            faces.append([float(d[0]), float(d[1]), float(d[2]), float(d[3]), float(d[-1])])

    def norm(bx):
        x, y, bw, bh, sc = bx
        return {"x": round(x / iw, 3), "y": round(y / ih, 3), "w": round(bw / iw, 3), "h": round(bh / ih, 3),
                "score": round(sc, 2)}

    return {"people": [norm(p) for p in people], "faces": [norm(f) for f in faces]}


def shot_size(dets_per_frame: list[dict], saliency_share: float) -> dict:
    """Estimate shot size from face height (preferred), body height, or salient-area share (weakest)."""
    face_h = [max(f["h"] for f in d["faces"]) for d in dets_per_frame if d["faces"]]
    body_h = [max(p["h"] for p in d["people"]) for d in dets_per_frame if d["people"]]
    if face_h:
        fh = float(np.median(face_h))
        size = ("extreme close-up" if fh > 0.55 else "close-up" if fh > 0.28 else "medium close-up" if fh > 0.16
                else "medium" if fh > 0.08 else "wide" if fh > 0.035 else "extreme wide")
        return tag(size, 0.7, "estimated", f"From face height ≈{fh:.0%} of frame.")
    if body_h:
        bh = float(np.median(body_h))
        size = ("medium / cowboy (body larger than frame)" if bh > 0.92 else "full / medium-wide" if bh > 0.6
                else "wide" if bh > 0.25 else "extreme wide")
        return tag(size, 0.5, "estimated", f"From full-body height ≈{bh:.0%} of frame (HOG people detector).")
    size = "close / insert (large single subject)" if saliency_share > 0.45 else "wide or establishing" \
        if saliency_share < 0.15 else "undetermined"
    return tag(size, 0.2, "heuristic", "No people detected; based only on how much of the frame draws attention.")


def summarise(dets_per_frame: list[dict]) -> dict:
    counts = [max(len(d["people"]), len(d["faces"])) for d in dets_per_frame]
    n = int(round(float(np.median(counts)))) if counts else 0
    peak = max(counts) if counts else 0
    positions = []
    best = max(dets_per_frame, key=lambda d: len(d["people"]) + len(d["faces"]), default=None)
    if best:
        src = best["faces"] or best["people"]
        for b in sorted(src, key=lambda b: b["x"]):
            positions.append({"x": round(b["x"] + b["w"] / 2, 3), "y": round(b["y"] + b["h"] / 2, 3), "h": b["h"],
                              "kind": "face" if best["faces"] else "body"})
    conf = 0.55 if any(d["faces"] for d in dets_per_frame) else 0.4 if peak else 0.25
    note = None if peak else "No people detected. Close-ups, partial bodies and non-human subjects are often missed."
    return {
        "count": tag(n, conf, "estimated", note),
        "max_visible": tag(peak, conf, "estimated"),
        "positions": tag(positions, conf, "estimated", "Normalised frame coordinates of detected subjects (left→right)."),
        "face_detection": tag("enabled" if face_model_available() else "not configured", 1.0, "observed",
                              None if face_model_available() else "Set CINEVISION_FACE_MODEL to a YuNet ONNX file to enable."),
    }
