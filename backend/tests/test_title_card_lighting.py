"""A black title card is not a lighting mistake."""
from cinevision.director import review as RV


def _analysis(text_s, dur=4.0):
    return {"clip": {"duration_s": {"value": dur, "confidence": 1}},
            "color": {"mean_luminance": {"value": 0.03, "confidence": 1},
                      "clipping": {"value": {"highlights": 0.0, "shadows": 0.96}, "confidence": 1},
                      "contrast": {"value": {"range_p5_p95": 0.1}, "confidence": 1}},
            "environment": {"practical_lights": {"value": [{"x": 0.5}], "confidence": 0.5}},
            "on_screen_text": {"value": [{"kind": "opening titles", "start_s": 0, "on_screen_s": text_s, "max_lines": 3}],
                               "confidence": 0.8}}


def _light_codes(a):
    r = RV.build(a, None)
    return {f["code"] for d in r["departments"] if d["id"] == "lighting" for f in d["findings"]}


def test_title_card_gets_no_lighting_complaints():
    assert not (_light_codes(_analysis(3.8)) & {"light.dark", "light.crushed", "light.flat", "light.practicals"})


def test_dark_shot_with_a_short_title_is_still_judged():
    assert "light.dark" in _light_codes(_analysis(0.8))
