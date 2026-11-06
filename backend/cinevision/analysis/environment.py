"""Environment heuristics: interior / exterior, time of day, practical light candidates.

Several independent cues are scored and added up, each with a sign (+ exterior, − interior):

* sky: a smooth band across the full width of the top of frame, with no straight structure inside it, whose colour
  is sky-like (blue, overcast white/grey, sunset warm) — or, when dark, which shows stars, a moon, or a sky gradient
  (lighter toward the horizon) above a textured ground. A dark, featureless top is *not* taken as a ceiling.
* horizon: a near-straight boundary across the frame between that smooth top and a textured lower part.
* foliage: textured green.
* interior structure: long straight lines in the upper frame (ceiling/wall joins, door and window frames).
* window: a bright, smooth, rectangular area that does not span the frame, framed by darker wall.
* practical lamps: small bright sources in a lit (not black) surround.
* colour temperature and key: warm, mid/low-key images lean interior; bright, high-range images lean exterior.

When strong cues point both ways the answer is "uncertain" with low confidence instead of a guess.
"""
from __future__ import annotations

import cv2
import numpy as np

from . import tag

W = 256          # analysis width


def sky_and_foliage(bgr: np.ndarray) -> dict:
    """Kept for callers of the earlier API: colour-only sky and foliage shares."""
    small = cv2.resize(bgr, (160, max(2, int(160 * bgr.shape[0] / bgr.shape[1]))))
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
            # brightness of a ring around the source: a lamp in a room lights its surroundings; a star or the moon doesn't
            x0, y0, x1, y1 = max(0, x - bw), max(0, yy - bh), min(w, x + 2 * bw), min(h, yy + 2 * bh)
            ring = y[y0:y1, x0:x1]
            out.append({"x": round(float(cent[i][0] / w), 3), "y": round(float(cent[i][1] / h), 3),
                        "size": round(float(share), 4), "tint": "warm" if r > b + 12 else "cool" if b > r + 12 else "neutral",
                        "surround": round(float(np.median(ring)) / 255, 3) if ring.size else 0.0})
    out.sort(key=lambda p: -p["size"])
    return out[:6]


def _lines(gray: np.ndarray) -> dict:
    h, w = gray.shape
    edges = cv2.Canny(gray, 50, 140)
    segs = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=40, minLineLength=int(min(h, w) * 0.28), maxLineGap=4)
    vert = horiz = obl_top = vert_top = 0
    horiz_rows = []
    for x1, y1, x2, y2 in (np.asarray(segs).reshape(-1, 4) if segs is not None else []):
        ang = abs(np.degrees(np.arctan2(y2 - y1, x2 - x1))) % 180
        my = (y1 + y2) / 2
        if ang < 8 or ang > 172:
            horiz += 1
            horiz_rows.append(my / h)
        elif 82 < ang < 98:
            vert += 1
            if min(y1, y2) < h * 0.35:            # reaches the upper frame: wall corners, door and window frames
                vert_top += 1
        elif my < h * 0.4:
            obl_top += 1
    return {"vertical": vert, "horizontal": horiz, "vertical_top": vert_top, "oblique_top": obl_top,
            "horizontal_rows": horiz_rows}


def _stars_and_moon(gray: np.ndarray, top_rows: int, smooth_top: np.ndarray) -> tuple[int, bool]:
    """Count small isolated bright points on a dark, smooth upper sky; detect one round bright disc (moon)."""
    top = gray[:top_rows]
    if top.size == 0 or float(np.median(top)) > 90:
        return 0, False
    bg = cv2.medianBlur(top, 5).astype(np.int16)
    peaks = ((top.astype(np.int16) - bg) > 40) & (top > 110)
    n, _, stats, _ = cv2.connectedComponentsWithStats(peaks.astype(np.uint8), 8)
    stars = sum(1 for i in range(1, n) if stats[i][4] <= 6)
    moon = False
    bright = (top > 200).astype(np.uint8)
    n2, lab2, st2, _ = cv2.connectedComponentsWithStats(bright, 8)
    if sum(1 for i in range(1, n2) if st2[i][4] >= 12) >= 3:
        return 0, False             # several larger bright shapes: lettering, signs or lights — not a starry sky
    for i in range(1, n2):
        x, y, bw, bh, area = st2[i]
        if area >= 12 and 0.7 < bw / max(bh, 1) < 1.4 and area / float(bw * bh) > 0.6:
            x0, y0, x1, y1 = max(0, x - bw), max(0, y - bh), min(top.shape[1], x + 2 * bw), min(top.shape[0], y + 2 * bh)
            ring = top[y0:y1, x0:x1].copy()
            ring[lab2[y0:y1, x0:x1] == i] = 0
            if float(np.median(ring)) < 60 and area < top.size * 0.03:
                moon = True
    return stars, moon


def frame_cues(bgr: np.ndarray) -> dict:
    h0, w0 = bgr.shape[:2]
    small = cv2.resize(bgr, (W, max(8, int(W * h0 / w0))), interpolation=cv2.INTER_AREA)
    h, w = small.shape[:2]
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV).astype(np.float32)
    H, S, V = hsv[..., 0], hsv[..., 1] / 255, hsv[..., 2] / 255
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    tex = np.abs(cv2.Laplacian(blur, cv2.CV_32F))
    smooth = tex < 5
    top_n = max(2, h // 3)

    # sky band: per column, how far down from the top edge the image stays smooth (ignoring isolated star dots)
    sm = cv2.morphologyEx(smooth.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8)).astype(bool)
    run = np.argmin(np.vstack([sm, np.zeros((1, w), bool)]), axis=0) / h      # first non-smooth row per column
    sky_cover = float((run >= 0.12).mean())
    depth = float(np.median(run))
    region = sm[:top_n] & (np.arange(top_n)[:, None] < (run * h)[None, :])
    if region.sum() < 20:
        region = sm[:top_n]
    rv, rs, rh = V[:top_n][region], S[:top_n][region], H[:top_n][region]
    sky_v = float(np.median(rv)) if rv.size else 0.0
    sky_s = float(np.median(rs)) if rs.size else 0.0
    sky_h = float(np.median(rh)) if rh.size else 0.0
    blue = 90 < sky_h < 135 and sky_s > 0.12 and sky_v > 0.25
    warm = (sky_h < 25 or sky_h > 160) and sky_s > 0.35 and sky_v > 0.45
    white = sky_s < 0.14 and sky_v > 0.62
    dark = sky_v < 0.3

    # vertical gradient of the smooth band: skies get lighter toward the horizon, smoothly
    rows = [float(V[r][sm[r]].mean()) for r in range(top_n) if sm[r].mean() > 0.5]
    grad, grad_smooth = 0.0, False
    if len(rows) >= 6:
        y = np.array(rows)
        x = np.linspace(0, 1, len(y))
        k, b = np.polyfit(x, y, 1)
        grad = float(k)
        grad_smooth = float(np.abs(y - (k * x + b)).mean()) < 0.03
    col_std = float(np.std([float(V[:top_n, c][sm[:top_n, c]].mean()) for c in range(0, w, 8)
                            if sm[:top_n, c].mean() > 0.5] or [0.0]))

    # horizon: textured ground below a smooth band, boundary roughly level across the frame
    below = float(tex[int(h * 0.55):].mean())
    above = float(tex[:top_n].mean())
    horizon = sky_cover > 0.6 and below > 2.5 * (above + 0.5) and float(np.std(run[run >= 0.12])) < 0.18 if sky_cover > 0 else False

    lines = _lines(blur)
    stars, moon = _stars_and_moon(cv2.resize(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), (512, max(8, int(512 * h0 / w0)))),
                                  max(2, int(512 * h0 / w0) // 2), sm)

    foliage = float(((H > 30) & (H < 90) & (S > 0.22) & (V > 0.12) & (tex > 6)).mean())

    # window: bright smooth rectangle well inside the frame, darker around it
    window = False
    thr = max(0.75, float(np.percentile(V, 50)) + 0.3)
    if thr < 1.0:
        m = ((V >= thr) & sm).astype(np.uint8)
        n, lab, st, _ = cv2.connectedComponentsWithStats(m, 4)
        for i in range(1, n):
            x, y, bw, bh, area = st[i]
            if area < w * h * 0.02 or bw > w * 0.7 or x == 0 and x + bw >= w:
                continue
            if area / float(bw * bh) < 0.75:
                continue
            x0, y0, x1, y1 = max(0, x - bw // 3), max(0, y - bh // 3), min(w, x + bw + bw // 3), min(h, y + bh + bh // 3)
            ring = V[y0:y1, x0:x1][m[y0:y1, x0:x1] == 0]      # surround, not counting other panes
            if ring.size and float(np.median(ring)) < float(V[lab == i].mean()) - 0.25 and not (y == 0 and bw > w * 0.6):
                window = True
                break

    pr = practicals(bgr)
    return {"sky_cover": sky_cover, "sky_depth": depth, "sky_v": sky_v, "sky_h": sky_h, "sky_s": sky_s, "sky_blue": blue, "sky_warm": warm,
            "sky_white": white, "sky_dark": dark, "gradient": grad, "gradient_smooth": grad_smooth, "col_std": col_std,
            "horizon": bool(horizon), "stars": stars, "moon": moon, "foliage": foliage, "window": window,
            "lines": {k: v for k, v in lines.items() if k != "horizontal_rows"}, "practicals": pr,
            "mean_v": float(V.mean()), "range": float(np.percentile(V, 95) - np.percentile(V, 5))}


def _score(c: dict, cast: str) -> tuple[float, float, list[str]]:
    """(exterior evidence, interior evidence, cue names) for one frame."""
    out_e, in_e, why = 0.0, 0.0, []
    band = c["sky_cover"] > 0.75 and c["sky_depth"] > 0.12 and c["col_std"] < 0.08
    sky = 0.0
    if band:
        strength = min(1.0, c["sky_depth"] / 0.3)      # a shallow smooth strip is as likely a ceiling as a sky
        if c["sky_blue"]:
            sky = 1.2 * strength; why.append("blue sky")
        elif c["sky_warm"]:
            sky = 0.9 * strength; why.append("sunset sky")
        elif c["sky_white"]:
            sky = 0.8 * strength; why.append("overcast sky")
        elif c["sky_dark"]:
            # a dark, featureless top is NOT a ceiling by default: it needs stars, a moon or a sky gradient
            night_sky = int(c["stars"] >= 3) + int(c["moon"]) + int(c["gradient"] > 0.04 and c["gradient_smooth"]) \
                + int(90 < c.get("sky_h", 0) < 135 and c.get("sky_s", 0) > 0.3)      # deep blue night sky
            if night_sky:
                sky = (0.6 + 0.3 * night_sky) * strength; why.append("night sky")
        elif c["gradient"] > 0.04 and c["gradient_smooth"]:
            sky = 0.4 * strength; why.append("sky gradient")
    out_e += sky
    if c["horizon"]:
        out_e += 0.4; why.append("horizon")
    if c["stars"] >= 3 and c["sky_v"] < 0.5:
        out_e += 0.5; why.append("stars")
    if c["moon"]:
        out_e += 0.4; why.append("moon")
    if c["foliage"] > 0.06:
        out_e += min(1.0, c["foliage"] * 5); why.append("foliage")
    if c["window"]:
        in_e += 1.0; why.append("window")
    structure_top = c["lines"]["vertical_top"] + c["lines"]["oblique_top"]
    if structure_top >= 2 and sky < 0.6 and not c["horizon"]:
        in_e += min(0.9, 0.25 * structure_top); why.append("walls / ceiling lines")
    lamps = [p for p in c["practicals"] if p.get("surround", 0) > 0.15]
    if lamps:
        in_e += 0.45; why.append("lamps")
    if cast == "warm" and c["mean_v"] < 0.5 and sky < 0.6:
        in_e += 0.25; why.append("warm interior light")
    if c["mean_v"] > 0.55 and c["range"] > 0.6 and (c["sky_blue"] or c["sky_white"]) and band:
        out_e += 0.2
    return out_e, in_e, why


def classify(frames: list[np.ndarray], mean_lum: float, cast: str) -> dict:
    cues = [frame_cues(f) for f in frames]
    scored = [_score(c, cast) for c in cues]
    out_e = float(np.mean([s[0] for s in scored]))
    in_e = float(np.mean([s[1] for s in scored]))
    names: dict[str, int] = {}
    for s in scored:
        for n in s[2]:
            names[n] = names.get(n, 0) + 1
    S = out_e - in_e
    conflict = out_e >= 0.6 and in_e >= 0.6
    if conflict or abs(S) < 0.4:
        env, econf = "uncertain", 0.3 if not conflict else 0.2
    elif S > 0:
        env, econf = "outdoor", min(0.85, 0.4 + 0.25 * S)
    else:
        env, econf = "indoor", min(0.85, 0.4 + 0.25 * -S)
    # frames that disagree with each other lower the confidence
    votes = [np.sign(a - b) for a, b, _ in scored if abs(a - b) >= 0.4]
    if votes and env in ("indoor", "outdoor"):
        econf *= max(0.5, abs(float(np.mean(votes))))

    pr = [p for c in cues for p in c["practicals"]]
    n_pr = len(pr) / max(1, len(frames))
    sky_frames = [c for c in cues if c["sky_cover"] > 0.75 and c["sky_depth"] > 0.12]
    stars = sum(c["stars"] >= 3 or c["moon"] for c in cues) / max(1, len(cues))
    warm_sky = sum(c["sky_warm"] for c in sky_frames) / max(1, len(cues))
    bright_sky = sum((c["sky_blue"] or c["sky_white"]) and c["sky_v"] > 0.5 for c in sky_frames) / max(1, len(cues))
    dusk_blue = sum(c["sky_blue"] and 0.18 < c["sky_v"] <= 0.5 for c in sky_frames) / max(1, len(cues))
    dark_sky = sum(c["sky_dark"] for c in sky_frames) / max(1, len(cues))
    windows = sum(c["window"] for c in cues) / max(1, len(cues))

    if env != "indoor" and (dark_sky >= 0.5 or mean_lum < 0.12) and (stars >= 0.5 or env == "outdoor"):
        tod, tconf = "night", 0.5 + 0.2 * min(1.0, stars)
    elif warm_sky >= 0.3 and mean_lum < 0.6:
        tod, tconf = "dawn / dusk (golden or blue hour)", 0.45
    elif dusk_blue >= 0.5 and mean_lum < 0.4:
        tod, tconf = "dawn / dusk (golden or blue hour)", 0.4
    elif bright_sky >= 0.5 and mean_lum > 0.35:
        tod, tconf = "day", 0.65
    elif env != "indoor" and mean_lum < 0.12 and dark_sky >= 0.5:
        tod, tconf = "night", 0.4
    elif env == "indoor" and windows >= 0.5:
        tod, tconf = "day", 0.4
    elif env != "outdoor" and mean_lum < 0.2 and n_pr >= 0.5:
        tod, tconf = "night", 0.35
    elif env == "indoor":
        tod, tconf = "uncertain (interior)", 0.25
    elif env == "outdoor" and mean_lum > 0.4:
        tod, tconf = "day", 0.45
    else:
        tod, tconf = "uncertain", 0.2
    note = ("Rule-based from several cues (sky, horizon, foliage, walls and ceiling lines, windows, lamps, colour and "
            "brightness). Grading and studio sets can mislead it; 'uncertain' means the cues disagree.")
    seen = {}
    for p in pr:  # dedupe across frames on a coarse grid
        seen.setdefault((round(p["x"], 1), round(p["y"], 1)), {k: v for k, v in p.items() if k != "surround"})
    sky_share = float(np.mean([c["sky_cover"] * min(1.0, c["sky_depth"] / 0.3) for c in cues])) if cues else 0.0
    return {
        "setting": tag(env, econf, "heuristic", note),
        "time_of_day": tag(tod, tconf, "heuristic", note),
        "practical_lights": tag(list(seen.values())[:6], 0.4 if seen else 0.3, "estimated",
                                "Small very bright regions in an otherwise darker frame (lamps, screens, windows)."),
        "cues": tag({"sky_fraction_top": round(sky_share, 3),
                     "foliage_fraction": round(float(np.mean([c["foliage"] for c in cues])), 3),
                     "practicals_per_frame": round(n_pr, 2),
                     "exterior_evidence": round(out_e, 2), "interior_evidence": round(in_e, 2),
                     "conflicting": bool(conflict),
                     "seen": sorted(names, key=lambda k: -names[k])[:8]}, 0.8, "observed"),
    }
