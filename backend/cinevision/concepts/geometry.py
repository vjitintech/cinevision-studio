"""Lens, sensor and framing geometry used by the concept builder and (mirrored in TypeScript) the previs engine."""
from __future__ import annotations

import math

# 16:9 active image areas (mm)
SENSORS = {
    "full_frame": {"w": 36.0, "h": 20.25, "crop": 1.0, "label": "Full frame"},
    "super35": {"w": 24.89, "h": 14.0, "crop": 1.45, "label": "Super 35"},
    "micro_four_thirds": {"w": 17.3, "h": 9.73, "crop": 2.0, "label": "Micro Four Thirds"},
    "phone": {"w": 6.4, "h": 3.6, "crop": 5.6, "label": "Phone main camera"},
}

# vertical extent of the subject that the frame should hold, in metres, for each shot size
SHOT_SIZES = ["extreme close-up", "close-up", "medium close-up", "medium", "medium wide", "full", "wide", "extreme wide"]
FRAMING_HEIGHT_M = {"extreme close-up": 0.14, "close-up": 0.32, "medium close-up": 0.55, "medium": 0.9,
                    "medium wide": 1.35, "full": 2.1, "wide": 4.0, "extreme wide": 11.0}
SUBJECT_REF_HEIGHT = {"extreme close-up": 1.62, "close-up": 1.58, "medium close-up": 1.5, "medium": 1.35,
                      "medium wide": 1.15, "full": 0.95, "wide": 0.95, "extreme wide": 0.95}

ANGLES = {  # camera height (m) and tilt (deg, + = up) relative to a standing subject
    "ground level": (0.15, 18), "low angle": (0.75, 12), "eye level": (1.6, 0), "high angle": (2.6, -18),
    "overhead": (4.5, -80), "dutch angle": (1.55, 0),
}


def fov_deg(focal_mm: float, sensor: str) -> tuple[float, float]:
    s = SENSORS[sensor]
    return (math.degrees(2 * math.atan(s["w"] / (2 * focal_mm))), math.degrees(2 * math.atan(s["h"] / (2 * focal_mm))))


def to_sensor_focal(ff_equiv_mm: float, sensor: str) -> float:
    return ff_equiv_mm / SENSORS[sensor]["crop"]


def lens_category(ff_equiv_mm: float) -> str:
    if ff_equiv_mm < 20:
        return "ultra-wide"
    if ff_equiv_mm < 35:
        return "wide"
    if ff_equiv_mm < 60:
        return "standard"
    if ff_equiv_mm < 110:
        return "short telephoto"
    return "telephoto"


def perspective_note(ff: float) -> str:
    if ff < 24:
        return "Exaggerated depth and size differences; close camera feels immersive, edges stretch."
    if ff < 35:
        return "Expansive space with natural-looking distortion when kept at subject distance."
    if ff < 60:
        return "Close to natural human perspective; neutral and honest."
    if ff < 110:
        return "Flattering compression of faces; background starts to fall away."
    return "Strong compression stacks foreground and background; observational distance."


def distance_for(size: str, ff_equiv_mm: float, sensor: str) -> float:
    focal = to_sensor_focal(ff_equiv_mm, sensor)
    _, vfov = fov_deg(focal, sensor)
    return round(FRAMING_HEIGHT_M[size] / (2 * math.tan(math.radians(vfov) / 2)), 2)


def step_size(size: str, steps: int) -> str:
    i = SHOT_SIZES.index(size) if size in SHOT_SIZES else 3
    return SHOT_SIZES[max(0, min(len(SHOT_SIZES) - 1, i + steps))]


def normalise_size(label: str | None) -> str:
    """Map analysis labels (which may be hedged) to the planner's vocabulary."""
    if not label:
        return "medium"
    l = label.lower()
    for key, val in (("extreme close", "extreme close-up"), ("medium close", "medium close-up"), ("close", "close-up"),
                     ("extreme wide", "extreme wide"), ("cowboy", "medium wide"), ("medium-wide", "medium wide"),
                     ("medium wide", "medium wide"), ("full", "full"), ("establishing", "wide"), ("wide", "wide"),
                     ("medium", "medium")):
        if key in l:
            return val
    return "medium"


def orbit_position(target: tuple[float, float, float], azimuth_deg: float, distance: float, height: float,
                   facing_deg: float = 180.0) -> list[float]:
    """Camera position around a subject. azimuth 0 = straight in front of the subject's face (facing_deg is the
    direction the subject faces, measured in the ground plane; 180 = toward the original camera at the origin)."""
    a = math.radians(facing_deg + azimuth_deg)
    horiz = math.sqrt(max(0.01, distance ** 2 - (height - target[1]) ** 2)) if distance > abs(height - target[1]) else distance
    return [round(target[0] + horiz * math.sin(a), 3), round(height, 3), round(target[2] + horiz * math.cos(a), 3)]
