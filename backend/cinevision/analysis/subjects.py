"""Subject detection.

* Faces: YuNet (OpenCV `FaceDetectorYN`), bundled at `cinevision/models/face_detection_yunet_2023mar.onnx` and used
  by default. `CINEVISION_FACE_MODEL` points to another YuNet ONNX file (or to a missing path to switch it off).
  Faces drive shot size (face height → ECU / CU / MCU / MS / WS) and find people the body detector misses
  (close-ups, seated or partly hidden people).
* People: OpenCV HOG + linear SVM pedestrian detector (bundled with OpenCV). It sees upright bodies at medium and
  wide sizes. Its false positives (poles, towers, rockets) are cut with a stricter score, non-maximum suppression,
  a size check, a "pole test" on the edge pattern and, when faces can be seen at that size, a face check.
"""
from __future__ import annotations

import os
from pathlib import Path

import cv2
import numpy as np

from . import tag

BUNDLED_FACE_MODEL = Path(__file__).resolve().parents[1] / "models" / "face_detection_yunet_2023mar.onnx"

HOG_MIN_SCORE = 1.0             # below this, HOG hits are mostly texture
HOG_UNCONFIRMED_SCORE = 1.6     # a body big enough to show a face but with no face must score at least this
FACE_MIN_PX = 12                # YuNet is unreliable below this face height (pixels at analysis size)
FACE_SCORE = 0.72

_hog = None
_face = None
_face_loaded = False
_face_path: str | None = None


def _get_hog():
    global _hog
    if _hog is None:
        _hog = cv2.HOGDescriptor()
        _hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
    return _hog


def face_model_path() -> str | None:
    """The YuNet model to use: CINEVISION_FACE_MODEL if set, else the bundled file. None when it doesn't exist."""
    path = os.environ.get("CINEVISION_FACE_MODEL") or str(BUNDLED_FACE_MODEL)
    return path if os.path.isfile(path) else None


def face_model_available() -> bool:
    return _get_face(320, 240) is not None


def detector_name() -> str:
    return "yunet-faces+hog-people" if face_model_available() else "hog-people"


def _get_face(w, h):
    global _face, _face_loaded, _face_path
    if not _face_loaded:
        _face_loaded = True
        path = face_model_path()
        _face_path = path
        if path and hasattr(cv2, "FaceDetectorYN"):
            try:
                _face = cv2.FaceDetectorYN.create(path, "", (w, h), FACE_SCORE, 0.3, 50)
            except cv2.error:
                _face = None
    if _face is not None:
        _face.setInputSize((int(w), int(h)))
    return _face


def _reset_face_model():
    """Tests: re-read CINEVISION_FACE_MODEL on next use."""
    global _face, _face_loaded
    _face, _face_loaded = None, False


def _iou(a, b) -> float:
    ax, ay, aw, ah = a[:4]
    bx, by, bw, bh = b[:4]
    ix = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0.0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    return inter / max(1e-6, aw * ah + bw * bh - inter)


def _nms(boxes: list, thr=0.35) -> list:
    """Greedy non-maximum suppression; also drops a box mostly inside a stronger one (HOG nests boxes)."""
    out: list = []
    for b in sorted(boxes, key=lambda x: -x[4]):
        inside = False
        for k in out:
            ix = max(0.0, min(b[0] + b[2], k[0] + k[2]) - max(b[0], k[0]))
            iy = max(0.0, min(b[1] + b[3], k[1] + k[3]) - max(b[1], k[1]))
            if ix * iy > 0.6 * b[2] * b[3]:
                inside = True
                break
        if not inside and all(_iou(b, k) < thr for k in out):
            out.append(b)
    return out


def _pole_like(gray: np.ndarray, box) -> bool:
    """True for thin upright objects (poles, towers, rockets, lamp posts): edge energy packed into a narrow
    central column, or an almost featureless box. A person's silhouette spreads edges across shoulders and arms."""
    x, y, w, h = (int(round(v)) for v in box[:4])
    roi = gray[max(0, y):y + h, max(0, x):x + w]
    if roi.size < 64 or roi.shape[1] < 8:
        return True
    if float(roi.std()) < 6:
        return True
    gx = np.abs(cv2.Sobel(roi, cv2.CV_32F, 1, 0, ksize=3))
    gy = np.abs(cv2.Sobel(roi, cv2.CV_32F, 0, 1, ksize=3))
    col = (gx + gy).sum(axis=0)
    total = float(col.sum()) + 1e-6
    cw = roi.shape[1]
    # share of edge energy in the densest 22% of columns
    k = max(2, int(cw * 0.22))
    dens = np.convolve(col, np.ones(k), mode="valid").max() / total
    # shoulders: the upper third of a person is wider than a pole — edge energy there must reach the box sides
    upper = (gx + gy)[: max(2, roi.shape[0] // 3)].sum(axis=0)
    side = float(upper[: cw // 4].sum() + upper[-(cw // 4):].sum()) / (float(upper.sum()) + 1e-6)
    return dens > 0.62 or (dens > 0.5 and side < 0.12)


def detect(bgr: np.ndarray) -> dict:
    h, w = bgr.shape[:2]
    scale = 640 / w if w > 640 else 1.0
    img = cv2.resize(bgr, (int(w * scale), int(h * scale))) if scale != 1 else bgr
    ih, iw = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    faces = []
    fd = _get_face(iw, ih)
    if fd is not None:
        _, dets = fd.detect(img)
        for d in (dets if dets is not None else []):
            fx, fy, fw, fh, sc = float(d[0]), float(d[1]), float(d[2]), float(d[3]), float(d[-1])
            # faces are roughly as tall as wide; very small boxes are mostly noise
            if fh >= FACE_MIN_PX and 0.6 < fh / max(fw, 1) < 2.0:
                faces.append([fx, fy, fw, fh, sc])
        faces = _nms(faces, 0.3)

    rects, weights = _get_hog().detectMultiScale(img, winStride=(8, 8), padding=(8, 8), scale=1.06)
    cand = []
    for (x, y, rw, rh), sc in zip(rects, np.ravel(weights) if len(rects) else []):
        # confident, person-shaped, not cut off by the left/right frame edge, and a sane size
        if sc > HOG_MIN_SCORE and rh / max(rw, 1) > 1.6 and x > 2 and x + rw < iw - 2 and rh < ih * 1.02:
            cand.append([float(x), float(y), float(rw), float(rh), float(sc)])
    cand = _nms(cand)

    people = []
    used_faces = set()
    for p in cand:
        x, y, rw, rh, sc = p
        conf_face = None
        for i, f in enumerate(faces):
            cx, cy = f[0] + f[2] / 2, f[1] + f[3] / 2
            if x <= cx <= x + rw and y <= cy <= y + rh * 0.4:
                conf_face = i
                break
        if conf_face is not None:
            used_faces.add(conf_face)
            people.append(p + ["face+body"])
            continue
        if _pole_like(gray, p):
            continue
        # a body tall enough that its face would be visible to YuNet, yet no face: demand a stronger score
        # (people seen from behind still pass when HOG is sure)
        face_visible = fd is not None and rh / 7.5 >= FACE_MIN_PX * 1.3
        if face_visible and sc < HOG_UNCONFIRMED_SCORE:
            continue
        people.append(p + ["body"])
    # faces with no body box are people too (close-ups, seated, partly hidden): estimate the body from the face
    for i, f in enumerate(faces):
        if i in used_faces:
            continue
        fx, fy, fw, fh, sc = f
        bw, bh = fw * 2.6, min(ih - fy + fh * 0.4, fh * 7.5)
        people.append([max(0.0, fx + fw / 2 - bw / 2), max(0.0, fy - fh * 0.35), min(bw, iw), bh, sc, "face"])

    def norm(bx, src=None):
        x, y, bw, bh, sc = bx[:5]
        out = {"x": round(x / iw, 3), "y": round(y / ih, 3), "w": round(min(bw, iw) / iw, 3),
               "h": round(min(bh, ih) / ih, 3), "score": round(sc, 2)}
        if src:
            out["source"] = src
        return out

    return {"people": [norm(p, p[5]) for p in people], "faces": [norm(f) for f in faces],
            "detector": "yunet+hog" if fd is not None else "hog"}


# face height (share of frame height) → shot size. YuNet's box runs from the brow to the chin (~19 cm on an adult):
# head-and-shoulders ≈ 45 cm of frame (CU), chest up ≈ 70 cm (MCU), waist up ≈ 1 m (MS), full body and more (WS).
FACE_SIZES = [(0.55, "extreme close-up", "ECU"), (0.36, "close-up", "CU"), (0.22, "medium close-up", "MCU"),
              (0.10, "medium", "MS"), (0.035, "wide", "WS"), (0.0, "extreme wide", "EWS")]


def size_from_face(fh: float) -> tuple[str, str]:
    for thr, label, abbr in FACE_SIZES:
        if fh > thr:
            return label, abbr
    return FACE_SIZES[-1][1], FACE_SIZES[-1][2]


def shot_size(dets_per_frame: list[dict], saliency_share: float) -> dict:
    """Estimate shot size from face height (preferred), body height, or salient-area share (weakest)."""
    # only trust detections that persist in at least half of the sampled frames (filters one-off false positives)
    n = len(dets_per_frame)
    half = max(1, (n + 1) // 2)
    face_h = [max(f["h"] for f in d["faces"]) for d in dets_per_frame if d.get("faces")]
    body_h = [max(p["h"] for p in d["people"] if p.get("source", "body") != "face")
              for d in dets_per_frame if any(p.get("source", "body") != "face" for p in d.get("people", []))]
    face_h = face_h if len(face_h) >= half else []
    body_h = body_h if len(body_h) >= half else []
    if face_h:
        fh = float(np.median(face_h))
        size, abbr = size_from_face(fh)
        spread = float(np.std(face_h)) / max(fh, 1e-3) if len(face_h) > 1 else 0.0
        conf = 0.75 * (len(face_h) / max(1, n)) ** 0.3 - min(0.2, spread * 0.4)
        out = tag(size, max(0.4, conf), "estimated", f"From face height ≈{fh:.0%} of frame (YuNet face detector).")
        out.update(abbr=abbr, source="face", face_height=round(fh, 3))
        return out
    if body_h:
        bh = float(np.median(body_h))
        size = ("medium / cowboy (body larger than frame)" if bh > 0.92 else "full / medium-wide" if bh > 0.6
                else "wide" if bh > 0.25 else "extreme wide")
        abbr = "MS" if bh > 0.92 else "FS" if bh > 0.6 else "WS" if bh > 0.25 else "EWS"
        out = tag(size, 0.5, "estimated", f"From full-body height ≈{bh:.0%} of frame (HOG people detector).")
        out.update(abbr=abbr, source="body")
        return out
    size = "close / insert (large single subject)" if saliency_share > 0.45 else "wide or establishing" \
        if saliency_share < 0.15 else "undetermined"
    out = tag(size, 0.2, "heuristic", "No people detected; based only on how much of the frame draws attention.")
    out["source"] = "saliency"
    return out


def summarise(dets_per_frame: list[dict]) -> dict:
    counts = [max(len(d.get("people", [])), len(d.get("faces", []))) for d in dets_per_frame]
    n = int(round(float(np.median(counts)))) if counts else 0
    peak = max(counts) if counts else 0
    if n == 0:
        peak = 0  # a detection seen in fewer than half the frames is treated as noise
    positions = []
    best = max(dets_per_frame, key=lambda d: len(d.get("people", [])) + len(d.get("faces", [])), default=None)
    if best and n > 0:
        src = best.get("faces") or best.get("people") or []
        for b in sorted(src, key=lambda b: b["x"]):
            positions.append({"x": round(b["x"] + b["w"] / 2, 3), "y": round(b["y"] + b["h"] / 2, 3), "h": b["h"],
                              "kind": "face" if best.get("faces") else "body"})
    faces_seen = any(d.get("faces") for d in dets_per_frame)
    confirmed = sum(1 for d in dets_per_frame for p in d.get("people", []) if p.get("source") in ("face", "face+body"))
    body_only = sum(1 for d in dets_per_frame for p in d.get("people", []) if p.get("source", "body") == "body")
    have_faces = face_model_available()
    conf = 0.65 if faces_seen else 0.4 if peak else (0.35 if have_faces else 0.25)
    if peak and body_only and not confirmed:
        conf = 0.4
    if not peak:
        note = ("No people detected. Faces turned away and very small or hidden people can be missed." if have_faces else
                "No people detected. Close-ups, partial bodies and seated people are often missed by this detector.")
    elif have_faces:
        note = ("People found from faces (YuNet) and bodies (HOG). A body with no visible face is kept only when the "
                "detector is sure, so people seen from behind can be missed.")
    else:
        note = ("Classic (HOG) people detector: upright shapes such as poles, statues or a rocket can be counted as a person, "
                "and close-ups or seated people are often missed.")
    return {
        "count": tag(n, conf, "estimated", note),
        "max_visible": tag(peak, conf, "estimated"),
        "positions": tag(positions, conf, "estimated", "Normalised frame coordinates of detected subjects (left→right)."),
        "face_detection": tag("enabled" if have_faces else "not available", 1.0, "observed",
                              None if have_faces else "The bundled YuNet face model could not be loaded (OpenCV 4.8+ "
                              "needed). Set CINEVISION_FACE_MODEL to a YuNet ONNX file to enable it."),
        "detector": tag(detector_name(), 1.0, "observed"),
        "face_confirmed": tag(confirmed, 0.8 if have_faces else 0.0, "estimated",
                              "Detections backed by a visible face (more reliable than body shape alone)."),
    }
