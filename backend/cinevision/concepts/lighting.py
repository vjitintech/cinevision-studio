"""Lighting plan construction. Positions are relative to the subject: azimuth 0° = from the camera side of the
subject's face, positive = camera-left (subject's right); elevation above the subject's eye line."""
from __future__ import annotations

import math

FIXTURES = {
    "micro": {"key_soft": "Bi-colour LED panel through a shower-curtain diffusion", "key_hard": "Bare LED / work light",
              "fill": "5-in-1 reflector (white side)", "back": "Small LED with barn doors", "neg": "Black foam board / duvetyne",
              "practical": "Household bulb (dimmable)", "sun": "Sun", "moon": "LED panel gelled ½ CTB, high and far",
              "haze": "Water-based hazer (rental) or incense in small rooms", "window": "Existing window + bedsheet diffusion"},
    "low": {"key_soft": "300 W COB LED + 90 cm softbox", "key_hard": "300 W COB LED with reflector, no diffusion",
            "fill": "Bounce board or 4×4 poly", "back": "LED tube or fresnel with barn doors", "neg": "4×4 floppy flag",
            "practical": "Tunable practical bulbs", "sun": "Sun + 4×4 silk", "moon": "COB LED gelled to 4300 K, high stand",
            "haze": "Water-based hazer", "window": "Window with 4×4 frame of diffusion"},
    "medium": {"key_soft": "600 W COB / 1.2 kW HMI through 8×8 diffusion", "key_hard": "1.2 kW fresnel / HMI",
               "fill": "8×8 ultrabounce", "back": "LED panel (S60 class) or 650 W fresnel", "neg": "8×8 solid",
               "practical": "Tunable practicals + LED tape", "sun": "Sun + 12×12 overhead silk",
               "moon": "Large soft LED on a high roller, 4300 K", "haze": "Professional hazer",
               "window": "HMI through window with diffusion frame"},
    "high": {"key_soft": "18 kW HMI or large LED array through 12×12 grid cloth", "key_hard": "18 kW HMI / 5 kW fresnel",
             "fill": "12×12 ultrabounce", "back": "SkyPanel S360 class / 2.5 kW HMI", "neg": "12×12 solid / floppy wall",
             "practical": "DMX-controlled practicals", "sun": "Sun + 20×20 silk and condor fill", "moon": "Balloon or condor-mounted soft LED",
             "haze": "Professional hazer with fans", "window": "18 kW HMI through window on condor"},
}


def _cct_for(env: str, tod: str, role: str, style: str) -> int:
    if role == "practical":
        return 2700
    if "moon" in style or "night" in tod:
        return 4300 if role in ("key", "back") else 3200
    if env == "outdoor" and "day" in tod:
        return 5600
    if "dusk" in tod or "dawn" in tod:
        return 3400 if role != "back" else 3000
    return 4300 if role == "fill" else 3800


def build(arch: dict, *, env: str, tod: str, practicals: list, budget: str, complexity: int, key_side: int,
          shot_size: str, warm_cool_shift: int = 0) -> dict:
    L = arch["light"]
    fx = FIXTURES[budget]
    style = L["style"]
    outdoor_day = env == "outdoor" and "day" in tod
    sources = []
    key_fixture = fx["sun"] if outdoor_day else (fx["key_hard"] if L["softness"] < 0.5 else fx["key_soft"])
    if not outdoor_day and practicals and "practical" not in style and complexity <= 1:
        key_fixture = fx["window"]
    key_cct = _cct_for(env, tod, "key", style) + warm_cool_shift
    sources.append({"role": "key", "fixture": key_fixture, "azimuth_deg": L["key_az"] * key_side, "elevation_deg": L["key_el"],
                    "distance_m": 2.2 if not outdoor_day else 150.0, "intensity": 1.0, "cct_k": key_cct,
                    "softness": round(L["softness"], 2), "motivation": "sun" if outdoor_day else
                    ("window / existing source" if practicals or "motivated" in style else "off-screen source")})
    fill_int = round(1 / L["ratio"], 3)
    if L["negative_fill"]:
        sources.append({"role": "negative_fill", "fixture": fx["neg"], "azimuth_deg": -60 * key_side, "elevation_deg": 0,
                        "distance_m": 1.0, "intensity": 0.0, "cct_k": None, "softness": None,
                        "motivation": "removes ambient bounce on the shadow side"})
    if L["ratio"] < 12 and complexity >= 1:
        sources.append({"role": "fill", "fixture": fx["fill"], "azimuth_deg": -35 * key_side, "elevation_deg": 10,
                        "distance_m": 2.0, "intensity": fill_int, "cct_k": _cct_for(env, tod, "fill", style) + warm_cool_shift,
                        "softness": 0.95, "motivation": "bounce from the room"})
    if L["back"] > 0.25 and complexity >= 1:
        back_role = "backlight" if L["back"] > 0.7 else "rim"
        back_fixture = fx["sun"] if outdoor_day and "silhouette" in style else (fx["moon"] if "moon" in style else fx["back"])
        sources.append({"role": back_role, "fixture": back_fixture, "azimuth_deg": (180 - 35) * -key_side
                        if back_role == "rim" else 170 * -key_side, "elevation_deg": 40, "distance_m": 3.0,
                        "intensity": round(L["back"], 2), "cct_k": _cct_for(env, tod, "back", style),
                        "softness": 0.35, "motivation": "separates subject from background"})
    if practicals or "practical" in style or ("night" in tod and env != "outdoor"):
        sources.append({"role": "practical", "fixture": fx["practical"], "azimuth_deg": 120 * key_side, "elevation_deg": 5,
                        "distance_m": 2.5, "intensity": 0.3, "cct_k": 2700, "softness": 0.6,
                        "motivation": "visible in frame; motivates the key"})
    if "coloured" in style or "gel" in style:
        for s in sources:
            if s["role"] in ("backlight", "rim"):
                s["gel"] = "deep blue / cyan"
            if s["role"] == "key":
                s["gel"] = "magenta / amber (complementary)"
    ambient = {"role": "ambient", "level": "low" if L["ratio"] >= 6 else "moderate" if L["ratio"] >= 3 else "high"}
    setup_name = style
    if L["ratio"] >= 6 and 50 <= L["key_az"] <= 80:
        setup_name += " — Rembrandt" if L["key_az"] < 70 else " — split"
    elif L["ratio"] <= 2 and L["key_az"] < 20:
        setup_name += " — high-key butterfly / frontal"
    stops = round(math.log2(L["ratio"]), 1)
    return {
        "setup": setup_name,
        "key_to_fill_ratio": f"{L['ratio']:g}:1 (~{stops} stops)",
        "ratio": L["ratio"],
        "sources": sources,
        "ambient": ambient,
        "haze": L["haze"],
        "exposure": ("Expose for the highlight side of the face; let shadows fall 3+ stops under." if L["ratio"] >= 6 else
                     "Expose skin around +⅓ to +⅔ over middle grey; protect window/sky highlights." if L["ratio"] <= 2.5 else
                     "Key side at normal exposure; shadow side ~1.5–2.5 stops under."),
        "simulation": "conceptual",
    }
