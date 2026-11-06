"""Timeline export of a movie assembly for editing software: CMX3600 EDL and FCPXML 1.9.

Both files describe the same frame-accurate timeline:
* the sequence runs at the frame rate most clips share (NTSC rates such as 23.976 are kept exact in FCPXML;
  the EDL counts them in non-drop-frame timecode at the nominal 24/30/60, as editing software expects);
* each clip is referenced by its original file name (EDL: an 8-character reel plus "* FROM CLIP NAME:");
* source in/out points come from the assembly's trims; source timecode is assumed to start at 00:00:00:00
  (files from phones and most cameras). Files with embedded timecode need "relink by file name" in the editor;
* dissolves are centred on the cut and use handles from both clips (the cut point is pulled in if a clip has no
  spare frames); a fade from/to black uses the first/last clip's own frames.

Nothing here touches media: only names, numbers and the order go into the file.
"""
from __future__ import annotations

import re
import unicodedata
from fractions import Fraction
from urllib.parse import quote
from xml.sax.saxutils import quoteattr

DISSOLVE_S = 1.0
FADE_S = 1.0
NTSC = {23.976: (24000, 1001), 29.97: (30000, 1001), 59.94: (60000, 1001), 47.952: (48000, 1001), 119.88: (120000, 1001)}
CROSS_DISSOLVE_UID = "FxPlug:4731E73A-8DAC-4113-9A30-AE85B1761265"   # Final Cut Pro's built-in Cross Dissolve


def frame_rate(fps: float) -> Fraction:
    """Exact frames per second: 23.976 → 24000/1001, 25 → 25."""
    fps = float(fps or 0) or 24.0
    for nominal, (n, d) in NTSC.items():
        if abs(fps - nominal) < 0.01:
            return Fraction(n, d)
    if abs(fps - round(fps)) < 0.01:
        return Fraction(round(fps))
    return Fraction(fps).limit_denominator(1001)


def _majority(vals):
    vals = [v for v in vals if v]
    return max(set(vals), key=vals.count) if vals else None


def _clean(name: str) -> str:
    return "".join(ch for ch in (name or "") if unicodedata.category(ch)[0] != "C").strip() or "clip"


def _reel(name: str, used: set) -> str:
    stem = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", name or "")
    base = re.sub(r"[^A-Za-z0-9_]", "", unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode()).upper()[:8] or "CLIP"
    reel, n = base, 1
    while reel in used:
        n += 1
        reel = f"{base[:8 - len(str(n))]}{n}"
    used.add(reel)
    return reel


def build(entries: list[dict], title: str) -> dict:
    """entries (playing order): {name, fps, width, height, duration_s, in_s, out_s, transition, transition_out, audio}.
    Returns the frame-accurate timeline both writers use."""
    rate = frame_rate(_majority([round(float(e.get("fps") or 0), 3) for e in entries]) or 24)
    tb = round(rate)                                     # timecode base (non-drop)
    width = _majority([int(e.get("width") or 0) for e in entries]) or 1920
    height = _majority([int(e.get("height") or 0) for e in entries]) or 1080
    reels: set = set()
    clips = []
    for e in entries:
        total = max(1, int(float(e.get("duration_s") or 0) * rate + 1e-6))
        src_in = max(0, min(total - 1, round(float(e.get("in_s") or 0) * rate)))
        src_out = max(src_in + 1, min(total, round(float(e.get("out_s") or e.get("duration_s") or 0) * rate)))
        clips.append({"name": _clean(e.get("name")), "reel": None, "total": total, "src_in": src_in, "src_out": src_out,
                      "audio": bool(e.get("audio")), "transition": e.get("transition") or "cut",
                      "transition_out": e.get("transition_out"), "width": int(e.get("width") or width),
                      "height": int(e.get("height") or height), "fps": float(e.get("fps") or 0) or float(rate)})
    reel_of: dict[str, str] = {}
    for c in clips:                                       # the same file always gets the same reel
        c["reel"] = reel_of.get(c["name"]) or reel_of.setdefault(c["name"], _reel(c["name"], reels))
    # transitions in frames
    for i, c in enumerate(clips):
        c["in_kind"], c["in_frames"] = "cut", 0
        length = c["src_out"] - c["src_in"]
        if i == 0 and c["transition"] == "fade_in":
            d = min(round(FADE_S * rate), length // 2)
            if d >= 2:
                c["in_kind"], c["in_frames"] = "fade", d
        elif i and c["transition"] == "dissolve":
            p = clips[i - 1]
            h = min(round(DISSOLVE_S * rate) // 2, (p["src_out"] - p["src_in"]) // 3, length // 3)
            if h >= 1:
                c["src_in"] = max(c["src_in"], h)                       # handle before the incoming clip's in point
                p["src_out"] = min(p["src_out"], p["total"] - h)        # handle after the outgoing clip's out point
                c["in_kind"], c["in_frames"] = "dissolve", 2 * h
    last = clips[-1] if clips else None
    fade_out = 0
    if last and last.get("transition_out") == "fade_out":
        d = min(round(FADE_S * rate), (last["src_out"] - last["src_in"]) // 2)
        fade_out = d if d >= 2 else 0
    rec = 0
    for c in clips:
        c["length"] = c["src_out"] - c["src_in"]
        c["rec_in"], c["rec_out"] = rec, rec + c["length"]
        rec += c["length"]
    return {"title": _clean(title)[:70], "rate": rate, "timebase": tb, "width": width, "height": height,
            "clips": clips, "fade_out": fade_out, "frames": rec}


def tc(frames: int, tb: int, hour: int = 0) -> str:
    frames += hour * 3600 * tb
    f = frames % tb
    s = frames // tb
    return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}:{f:02d}"


def _edl_line(n, reel, chan, kind, dur, si, so, ri, ro, tb):
    d = f"{dur:03d}" if dur else "   "
    return f"{n:03d}  {reel:<8} {chan:<5} {kind:<4} {d} {tc(si, tb)} {tc(so, tb)} {tc(ri, tb, 1)} {tc(ro, tb, 1)}"


def to_edl(tl: dict) -> str:
    """CMX3600. Record timecode starts at 01:00:00:00. Dissolves/fades are D events with their length in frames."""
    tb = tl["timebase"]
    title = re.sub(r"[^\x20-\x7e]", "_", tl["title"]) or "CineVision"
    out = [f"TITLE: {title}", "FCM: NON-DROP FRAME", ""]
    clips = tl["clips"]
    n = 0
    for i, c in enumerate(clips):
        n += 1
        chan = "AA/V" if c["audio"] else "V"
        h = c["in_frames"] // 2 if c["in_kind"] == "dissolve" else 0
        h_out = clips[i + 1]["in_frames"] // 2 if i + 1 < len(clips) and clips[i + 1]["in_kind"] == "dissolve" else 0
        fo = tl["fade_out"] if i == len(clips) - 1 else 0
        # this clip's own segment: from (cut − incoming half-dissolve) to (next cut − outgoing half-dissolve)
        si, so = c["src_in"] - h, c["src_out"] - h_out - fo
        ri, ro = c["rec_in"] - h, c["rec_out"] - h_out - fo
        if c["in_kind"] == "cut":
            out.append(_edl_line(n, c["reel"], chan, "C", 0, si, so, ri, ro, tb))
            out.append(f"* FROM CLIP NAME: {c['name']}")
        else:
            if c["in_kind"] == "fade":
                out.append(_edl_line(n, "BL", chan, "C", 0, 0, 0, ri, ri, tb))
            else:
                p = clips[i - 1]
                out.append(_edl_line(n, p["reel"], "AA/V" if p["audio"] else "V", "C", 0, p["src_out"] - h, p["src_out"] - h, ri, ri, tb))
            out.append(_edl_line(n, c["reel"], chan, "D", c["in_frames"], si, so, ri, ro, tb))
            if c["in_kind"] == "dissolve":
                out.append(f"* FROM CLIP NAME: {clips[i - 1]['name']}")
                out.append(f"* TO CLIP NAME: {c['name']}")
            else:
                out.append("* FROM CLIP NAME: BLACK")
                out.append(f"* TO CLIP NAME: {c['name']}")
        out.append(f"* SOURCE FILE: {c['name']}")
        out.append("")
        if fo:
            n += 1
            out.append(_edl_line(n, c["reel"], chan, "C", 0, so, so, ro, ro, tb))
            out.append(_edl_line(n, "BL", chan, "D", fo, 0, fo, ro, ro + fo, tb))
            out.append(f"* FROM CLIP NAME: {c['name']}")
            out.append("* TO CLIP NAME: BLACK")
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def _t(frames: int, rate: Fraction) -> str:
    """Rational seconds as FCPXML writes them, always a whole number of frames: '1001/24000s', '5s'."""
    v = Fraction(frames) / rate
    if v.denominator == 1:
        return f"{v.numerator}s"
    fd = 1 / rate                                          # keep the frame-duration denominator (e.g. 24000)
    num = frames * fd.numerator
    return f"{num}/{fd.denominator}s"


def to_fcpxml(tl: dict) -> str:
    """FCPXML 1.9 (Final Cut Pro 10.5+, DaVinci Resolve, Premiere Pro via import). Media is linked by file name
    next to the XML; editors ask to relink if the clips are elsewhere."""
    rate, clips = tl["rate"], tl["clips"]
    fd = 1 / rate
    frame_dur = f"{fd.numerator}/{fd.denominator}s" if fd.denominator != 1 else f"{fd.numerator}s"
    seq_fmt = "r1"
    formats = {(tl["width"], tl["height"]): seq_fmt}
    res = [f'    <format id="r1" frameDuration="{frame_dur}" width="{tl["width"]}" height="{tl["height"]}"/>']
    for c in clips:
        key = (c["width"], c["height"])
        if key not in formats:
            formats[key] = f"r{len(formats) + 1}"
            res.append(f'    <format id="{formats[key]}" frameDuration="{frame_dur}" width="{c["width"]}" height="{c["height"]}"/>')
    effect = f"r{len(formats) + 1}"
    if any(c["in_kind"] != "cut" for c in clips) or tl["fade_out"]:
        res.append(f'    <effect id="{effect}" name="Cross Dissolve" uid="{CROSS_DISSOLVE_UID}"/>')
    assets: dict[str, str] = {}
    for c in clips:
        if c["name"] in assets:
            continue
        aid = f"a{len(assets) + 1}"
        assets[c["name"]] = aid
        audio = ' hasAudio="1" audioSources="1" audioChannels="2" audioRate="48000"' if c["audio"] else ""
        res.append(f'    <asset id="{aid}" name={quoteattr(c["name"])} start="0s" duration="{_t(c["total"], rate)}" hasVideo="1" '
                   f'format="{formats[(c["width"], c["height"])]}" videoSources="1"{audio}>\n'
                   f'      <media-rep kind="original-media" src={quoteattr("./" + quote(c["name"]))}/>\n    </asset>')

    def transition(offset, dur):
        return (f'          <transition name="Cross Dissolve" offset="{_t(offset, rate)}" duration="{_t(dur, rate)}">\n'
                f'            <filter-video ref="{effect}" name="Cross Dissolve"/>\n          </transition>')
    spine = []
    for c in clips:
        if c["in_kind"] == "fade":
            spine.append(transition(c["rec_in"], c["in_frames"]))
        elif c["in_kind"] == "dissolve":
            spine.append(transition(c["rec_in"] - c["in_frames"] // 2, c["in_frames"]))
        spine.append(f'          <asset-clip ref="{assets[c["name"]]}" name={quoteattr(c["name"])} offset="{_t(c["rec_in"], rate)}" '
                     f'start="{_t(c["src_in"], rate)}" duration="{_t(c["length"], rate)}" format="{formats[(c["width"], c["height"])]}" '
                     f'tcFormat="NDF"/>')
    if tl["fade_out"]:
        spine.append(transition(tl["frames"] - tl["fade_out"], tl["fade_out"]))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n<fcpxml version="1.9">\n  <resources>\n'
            + "\n".join(res) + "\n  </resources>\n"
            f'  <library>\n    <event name={quoteattr(tl["title"])}>\n      <project name={quoteattr(tl["title"])}>\n'
            f'        <sequence format="{seq_fmt}" duration="{_t(tl["frames"], rate)}" tcStart="0s" tcFormat="NDF" '
            f'audioLayout="stereo" audioRate="48k">\n          <spine>\n'
            + "\n".join(spine) + "\n          </spine>\n        </sequence>\n      </project>\n    </event>\n  </library>\n"
            "</fcpxml>\n")
