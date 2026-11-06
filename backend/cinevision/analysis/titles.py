"""On-screen text (opening titles, captions/lower thirds, end credits) from sampled frames.

1. Finding text: classic text-region detection — morphological gradient → binarise → horizontal closing joins
   letters into words → keep wide, short, dense boxes. Runs on every sampled frame.
2. Measuring it: for each text segment, title-safe position (central 90% of the frame) and contrast between the
   letters and what is behind them (WCAG-style luminance ratio).
3. Reading it (optional): if the Tesseract OCR program is installed, the clearest frame of each segment is cropped,
   upscaled, binarised and read locally (`tesseract stdin stdout`, no files written, timeout per call). The words
   are user data: they stay inside the encrypted analysis and are never logged.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from typing import Callable

import cv2
import numpy as np

from . import tag

_K_GRAD = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
SAFE_MARGIN = 0.05            # title-safe = central 90% of width and height (SMPTE ST 2046-1)
READ_WPS = 2.5                # comfortable reading speed for on-screen text, words per second
NOTICE_S = 1.0                # time to notice a new card before reading starts
LOW_CONTRAST = 3.0            # below 3:1 large text is hard to read (WCAG 2 AA, large text)
MAX_OCR_SEGMENTS = 40
MIN_READ_CONF = 0.65          # mean Tesseract word confidence below this is noise read from a non-text region
WINDOWS_TESSERACT = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
NOT_AVAILABLE = "text reading not available — install Tesseract to enable"

# app language → Tesseract traineddata
TESS_LANG = {"en": "eng", "hi": "hin", "te": "tel", "ta": "tam", "kn": "kan", "ml": "mal", "bn": "ben", "mr": "mar",
             "es": "spa", "fr": "fra", "de": "deu", "pt": "por", "ar": "ara", "ja": "jpn", "ko": "kor", "zh": "chi_sim"}


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


CARD_FLAT_STD = 12.0          # grey-level spread outside the text below which the frame is a plain title card


def _plain_card(img: np.ndarray, boxes) -> bool:
    """True when everything outside the text boxes is (nearly) one flat tone: a one- or two-line title card such as
    'THE END' or a title + 'directed by' line, which has too few text lines for the busy-frame rule."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    keep = np.ones(gray.shape, bool)
    for x, y, bw, bh in boxes:
        p = max(2, bh)
        keep[max(0, y - p):y + bh + p, max(0, x - p):x + bw + p] = False
    rest = gray[keep]
    return rest.size > gray.size * 0.5 and float(rest.std()) < CARD_FLAT_STD


def frame_hit(t: float, img: np.ndarray):
    """One sampled frame → (t, normalised boxes, all-in-lower-band) when it looks like a text frame, else None."""
    boxes = text_boxes(img)
    h, w = img.shape[:2]
    lower = [b for b in boxes if b[1] > h * 0.72]
    if len(boxes) >= 3 or (len(boxes) >= 1 and len(lower) == len(boxes)) or (boxes and _plain_card(img, boxes)):
        nb = [(x / w, y / h, bw / w, bh / h) for x, y, bw, bh in boxes]
        return (t, nb, len(lower) == len(boxes))
    return None


# ---------------------------------------------------------------- Tesseract

_tess: dict | None = None


def _tesseract_path() -> str | None:
    cand = [os.environ.get("CINEVISION_TESSERACT")]
    try:
        from ..config import settings
        cand.insert(0, getattr(settings(), "tesseract_bin", None))
    except Exception:  # noqa: BLE001 - settings need a master key; analysis tests may run without one
        pass
    cand += [shutil.which("tesseract")]
    if sys.platform == "win32":
        cand.append(WINDOWS_TESSERACT)
    for c in cand:
        if c and (os.path.isfile(c) or shutil.which(c)):
            return shutil.which(c) or c
    return None


def _run(args: list[str], data: bytes | None = None, timeout: int = 30) -> subprocess.CompletedProcess:
    kw: dict = {"input": data, "capture_output": True, "timeout": timeout}
    if sys.platform == "win32":
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.run(args, **kw)  # noqa: S603 - fixed binary, our own PNG on stdin


def ocr_engine() -> dict:
    """{"available", "path", "version", "languages"} — detected once per process."""
    global _tess
    if _tess is not None:
        return _tess
    info = {"available": False, "path": None, "version": None, "languages": []}
    path = _tesseract_path()
    if path:
        try:
            v = _run([path, "--version"], timeout=15)
            langs = _run([path, "--list-langs"], timeout=15)
            out = (langs.stdout or b"").decode("utf-8", "replace").splitlines()
            ll = [x.strip() for x in out[1:] if x.strip() and x.strip() != "osd" and re.fullmatch(r"[\w+-]+", x.strip())]
            m = re.search(r"tesseract\s+v?([\d.]+)", (v.stdout or b"").decode("utf-8", "replace") +
                          (v.stderr or b"").decode("utf-8", "replace"))
            if ll:
                info = {"available": True, "path": path, "version": m.group(1) if m else None, "languages": ll}
        except (OSError, subprocess.SubprocessError):
            pass
    _tess = info
    return info


def _reset_engine():
    global _tess
    _tess = None


def ocr_languages(lang: str) -> str | None:
    have = ocr_engine()["languages"]
    if not have:
        return None
    want = [TESS_LANG.get(lang), "eng"]
    picked = [x for x in dict.fromkeys(w for w in want if w) if x in have]
    return "+".join(picked) if picked else have[0]


def _prepare(crop: np.ndarray) -> np.ndarray:
    """Upscale so letters are ~40 px tall, grey, binarise (dark text on white, as Tesseract prefers)."""
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    h = gray.shape[0]
    f = float(np.clip(120.0 / max(h, 1), 1.0, 4.0))
    if f > 1.01:
        gray = cv2.resize(gray, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
    _, bw = cv2.threshold(cv2.GaussianBlur(gray, (3, 3), 0), 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    if (bw > 0).mean() < 0.5:          # mostly black → light text on dark: invert
        bw = 255 - bw
    return cv2.copyMakeBorder(bw, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)


def read_text(crop: np.ndarray, langs: str) -> tuple[str, float] | None:
    """OCR one crop → (text, mean word confidence 0..1). None when nothing legible was read."""
    eng = ocr_engine()
    if not eng["available"]:
        return None
    ok, png = cv2.imencode(".png", _prepare(crop))
    if not ok:
        return None
    try:
        r = _run([eng["path"], "stdin", "stdout", "--psm", "6", "-l", langs, "tsv"], png.tobytes(), timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    lines: dict[tuple, list[str]] = {}
    confs = []
    for row in (r.stdout or b"").decode("utf-8", "replace").splitlines()[1:]:
        p = row.split("\t")
        if len(p) < 12 or p[0] != "5":
            continue
        try:
            c = float(p[10])
        except ValueError:
            continue
        word = p[11].strip()
        if not word or c < 45 or not re.search(r"\w", word):
            continue
        lines.setdefault((p[2], p[3], p[4]), []).append(word)
        confs.append(c / 100)
    text = "\n".join(" ".join(ws) for ws in lines.values()).strip()
    if not text:
        return None
    return text[:600], float(np.mean(confs))


# ---------------------------------------------------------------- measurements

def _rel_lum(v: float) -> float:
    c = v / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def contrast_ratio(bgr: np.ndarray, boxes: list[tuple[float, float, float, float]]) -> float | None:
    """Median WCAG contrast between letter pixels and their background over the text boxes."""
    h, w = bgr.shape[:2]
    lum = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    out = []
    for x, y, bw, bh in boxes[:12]:
        x0, y0 = int(x * w), int(y * h)
        x1, y1 = int((x + bw) * w) + 1, int((y + bh) * h) + 1
        roi = lum[max(0, y0):y1, max(0, x0):x1]
        if roi.size < 30:
            continue
        t, _ = cv2.threshold(roi, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
        hi, lo = roi[roi > t], roi[roi <= t]
        if hi.size < 5 or lo.size < 5:
            continue
        a, b = _rel_lum(float(np.median(hi))), _rel_lum(float(np.median(lo)))
        out.append((max(a, b) + 0.05) / (min(a, b) + 0.05))
    return round(float(np.median(out)), 1) if out else None


def _union(boxes):
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[0] + b[2] for b in boxes)
    y1 = max(b[1] + b[3] for b in boxes)
    return x0, y0, x1 - x0, y1 - y0


def _safe(box) -> bool:
    x, y, w, h = box
    m = SAFE_MARGIN - 0.005
    return x >= m and y >= m and x + w <= 1 - m and y + h <= 1 - m


def _crop(img: np.ndarray, box, pad=0.25) -> np.ndarray:
    h, w = img.shape[:2]
    x, y, bw, bh = box
    px, py = bw * pad * 0.3 + 0.01, bh * pad + 0.01
    x0, y0 = max(0, int((x - px) * w)), max(0, int((y - py) * h))
    x1, y1 = min(w, int((x + bw + px) * w) + 1), min(h, int((y + bh + py) * h) + 1)
    return img[y0:y1, x0:x1]


def needed_seconds(words: int) -> float:
    return round(NOTICE_S + words / READ_WPS, 1)


def detect_from_hits(hits: list, step: float, duration: float, frame_at: Callable[[float], np.ndarray | None] | None = None,
                     lang: str = "en", small_at: Callable[[float], np.ndarray | None] | None = None) -> dict:
    """hits: frame_hit() results in time order; step: seconds between sampled frames."""
    segs: list[dict] = []
    for t, boxes, lower in hits:
        n = len(boxes)
        if segs and t - segs[-1]["end_s"] <= step * 2.5:
            s = segs[-1]
            s["end_s"] = round(t + step, 2)
            s["max_lines"] = max(s["max_lines"], n)
            s["lower_only"] = s["lower_only"] and lower
            if n > len(s["_best"][1]):
                s["_best"] = (t, boxes)
        else:
            segs.append({"start_s": round(t, 2), "end_s": round(t + step, 2), "max_lines": n, "lower_only": lower,
                         "_best": (t, boxes)})
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
    segs = segs[:100]

    eng = ocr_engine()
    langs = ocr_languages(lang) if eng["available"] else None
    order = sorted(range(len(segs)), key=lambda i: (segs[i]["kind"] not in ("opening titles", "end credits"),
                                                     -segs[i]["on_screen_s"]))[:MAX_OCR_SEGMENTS]
    read_n = 0
    not_text: set[int] = set()
    for i, s in enumerate(segs):
        t, boxes = s.pop("_best")
        s["best_t"] = round(t, 2)
        ub = _union(boxes)
        s["box"] = [round(v, 3) for v in ub]
        s["title_safe"] = _safe(ub)
        img = None
        if frame_at is not None:
            try:
                img = frame_at(t)
            except Exception:  # noqa: BLE001 - a frame we can't grab just isn't measured
                img = None
        if img is not None:            # at sample size, anti-aliasing makes thin letters look low-contrast: skip
            s["contrast_ratio"] = contrast_ratio(img, boxes)
        elif small_at is not None:
            img = small_at(t)
        if img is None:
            continue
        if langs and i in order:
            got = read_text(_crop(img, ub), langs)
            if not got or got[1] < MIN_READ_CONF:
                # OCR is installed and finds no legible words here: the "text" was a face, a pattern or a texture
                # (eyes, brows, a mug's rim). Reporting it would give title findings on a shot with no titles.
                not_text.add(i)
                continue
            if got:
                text, conf = got
                words = len(re.findall(r"\w+", text))
                s.update(text=text, words=words, read_confidence=round(conf, 2), needed_s=needed_seconds(words))
                read_n += 1
    segs = [s for i, s in enumerate(segs) if i not in not_text]
    if eng["available"]:
        note = (f"Text-like regions found in sampled frames; the words were read on this computer with Tesseract OCR "
                f"({langs}). Stylised fonts, motion blur and busy backgrounds can be misread.")
    else:
        note = ("Text-like regions found in sampled frames. Words are not read: " + NOT_AVAILABLE + ". Busy textures "
                "can be mistaken for text and very thin fonts can be missed.")
    out = tag(segs, 0.5 if read_n else 0.45, "estimated", note)
    out["reading"] = {"available": eng["available"], "engine": f"tesseract {eng['version'] or ''}".strip()
                      if eng["available"] else None, "languages": langs, "segments_read": read_n,
                      "note": None if eng["available"] else NOT_AVAILABLE}
    return out


def detect(samples: list[tuple[float, np.ndarray]], duration: float,
           frame_at: Callable[[float], np.ndarray | None] | None = None, lang: str = "en") -> dict:
    """samples: (time, bgr) in time order."""
    hits = [h for h in (frame_hit(t, img) for t, img in samples) if h]
    step = (samples[1][0] - samples[0][0]) if len(samples) > 1 else 1.0
    by_t = {round(t, 3): img for t, img in samples}
    return detect_from_hits(hits, step, duration, frame_at, lang, small_at=lambda t: by_t.get(round(t, 3)))
