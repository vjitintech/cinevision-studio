"""Synthetic frames with a known answer for the environment classifier (interior/exterior, day/night)."""
from __future__ import annotations

import cv2
import numpy as np

W, H = 640, 360


def _texture(img, y0, y1, rng, base, spread, n=1400, maxsize=10):
    for _ in range(n):
        x, y = int(rng.integers(0, W)), int(rng.integers(y0, y1))
        c = tuple(int(np.clip(b + rng.integers(-spread, spread), 0, 255)) for b in base)
        s = int(rng.integers(2, maxsize))
        cv2.rectangle(img, (x, y), (x + s, y + s // 2 + 1), c, -1)


def night_exterior(seed=1) -> np.ndarray:
    """Dark navy sky, lighter toward the horizon, stars and a moon; dark textured ground with a few lit windows."""
    rng = np.random.default_rng(seed)
    img = np.zeros((H, W, 3), np.uint8)
    hz = int(H * 0.62)
    for y in range(hz):
        t = y / hz
        img[y, :] = (int(28 + 50 * t), int(8 + 22 * t), int(4 + 12 * t))       # BGR navy → lighter blue at horizon
    for _ in range(60):
        x, y = int(rng.integers(0, W)), int(rng.integers(0, int(hz * 0.8)))
        v = int(rng.integers(170, 255))
        img[y, x] = (v, v, v)
    cv2.circle(img, (520, 60), 11, (235, 240, 245), -1)
    _texture(img, hz, H, rng, (22, 24, 26), 14)
    # rooftop silhouette line and a few warm windows
    cv2.rectangle(img, (60, hz - 40), (200, hz), (15, 16, 18), -1)
    cv2.rectangle(img, (380, hz - 25), (470, hz), (15, 16, 18), -1)
    for x in (80, 120, 160, 400, 440):
        cv2.rectangle(img, (x, hz - 30), (x + 8, hz - 22), (90, 190, 240), -1)
    return img


def lit_room_with_lamp(seed=2) -> np.ndarray:
    """Warm interior at night: ceiling, two walls meeting in a corner, a door frame, a picture, a table lamp."""
    rng = np.random.default_rng(seed)
    img = np.zeros((H, W, 3), np.uint8)
    img[:] = (70, 105, 140)                           # warm beige wall (BGR)
    img[: int(H * 0.16)] = (55, 80, 105)              # ceiling, darker
    cv2.line(img, (0, int(H * 0.16)), (W, int(H * 0.16)), (40, 55, 70), 2)
    cv2.line(img, (400, int(H * 0.16)), (400, int(H * 0.78)), (45, 70, 95), 2)   # wall corner
    cv2.line(img, (400, int(H * 0.16)), (460, 0), (45, 70, 95), 2)               # ceiling corner
    cv2.rectangle(img, (470, int(H * 0.25)), (570, int(H * 0.78)), (35, 50, 70), 3)   # door frame
    cv2.rectangle(img, (120, 80), (230, 150), (60, 50, 40), -1)                   # picture
    cv2.rectangle(img, (120, 80), (230, 150), (20, 30, 40), 3)
    img[int(H * 0.78):] = (40, 60, 85)                                            # floor
    for y in range(int(H * 0.78), H, 9):
        cv2.line(img, (0, y), (W, y + 3), (30, 45, 65), 1)
    # glow + lamp
    glow = np.zeros((H, W), np.float32)
    cv2.circle(glow, (300, 200), 120, 1.0, -1)
    glow = cv2.GaussianBlur(glow, (0, 0), 50)
    img = np.clip(img.astype(np.float32) + glow[..., None] * np.array([20, 45, 70], np.float32), 0, 255).astype(np.uint8)
    cv2.rectangle(img, (270, 230), (330, 280), (30, 40, 60), -1)                  # table
    cv2.ellipse(img, (300, 205), (14, 18), 0, 0, 360, (200, 245, 255), -1)        # bulb / shade
    img += rng.integers(0, 3, img.shape, dtype=np.uint8)
    return img


def overcast_day_exterior(seed=3) -> np.ndarray:
    """Flat white-grey sky over a horizon, grass and a grey road, a few tree trunks."""
    rng = np.random.default_rng(seed)
    img = np.zeros((H, W, 3), np.uint8)
    hz = int(H * 0.45)
    for y in range(hz):
        v = int(205 + 25 * y / hz)
        img[y, :] = (v + 4, v + 2, v)
    _texture(img, hz, H, rng, (60, 130, 85), 40, n=2500)                         # grass
    pts = np.array([[250, H], [390, H], [330, hz], [310, hz]], np.int32)
    cv2.fillPoly(img, [pts], (120, 122, 125))                                       # road
    _texture(img, hz, int(hz + 25), rng, (40, 90, 50), 30, n=500, maxsize=14)      # tree line
    return img


def daylight_interior_with_window(seed=4) -> np.ndarray:
    """Plain interior wall with a bright window (four panes), a corner, a sofa and a floor."""
    rng = np.random.default_rng(seed)
    img = np.zeros((H, W, 3), np.uint8)
    img[:] = (120, 135, 150)
    cv2.line(img, (500, 0), (500, int(H * 0.75)), (95, 110, 125), 2)              # wall corner
    x0, y0, x1, y1 = 180, 50, 400, 210
    cv2.rectangle(img, (x0 - 8, y0 - 8), (x1 + 8, y1 + 8), (70, 80, 90), -1)      # window frame
    img[y0:y1, x0:x1] = (250, 246, 238)                                           # daylight
    cv2.line(img, ((x0 + x1) // 2, y0), ((x0 + x1) // 2, y1), (70, 80, 90), 5)     # mullions
    cv2.line(img, (x0, (y0 + y1) // 2), (x1, (y0 + y1) // 2), (70, 80, 90), 5)
    img[int(H * 0.75):] = (70, 90, 110)
    for y in range(int(H * 0.75), H, 10):
        cv2.line(img, (0, y), (W, y), (55, 70, 90), 1)
    cv2.rectangle(img, (60, 220), (330, 300), (60, 50, 110), -1)                  # sofa
    img += rng.integers(0, 3, img.shape, dtype=np.uint8)
    return img


ALL = {"night_exterior": night_exterior, "lit_room": lit_room_with_lamp, "overcast_day": overcast_day_exterior,
       "window_interior": daylight_interior_with_window}
