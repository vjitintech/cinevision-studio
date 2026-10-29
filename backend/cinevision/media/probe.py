"""Upload validation: magic bytes + ffprobe facts checked against an allow-list."""
from __future__ import annotations

import json
from pathlib import Path

from ..config import Settings
from . import sandbox

ALLOWED_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}


class ValidationError(Exception):
    def __init__(self, message: str, code: str = "invalid_video"):
        super().__init__(message)
        self.code = code


def sniff_container(head: bytes) -> str | None:
    if len(head) >= 12 and head[4:8] in (b"ftyp", b"moov", b"mdat", b"wide", b"free", b"skip"):
        return "isobmff"
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "matroska"
    if head[:4] == b"RIFF" and head[8:12] == b"AVI ":
        return "avi"
    return None


def probe(path: Path, s: Settings) -> dict:
    cmd = [s.ffprobe_bin, "-v", "error", "-protocol_whitelist", "file", "-print_format", "json",
           "-show_format", "-show_streams", "-count_packets", "-select_streams", "v:0", f"file:{path}"]
    res = sandbox.run(cmd, sandbox.Limits(cpu_s=60, timeout_s=90, mem_bytes=1024 ** 3))
    if res.returncode != 0:
        raise ValidationError("The file could not be read as a video.")
    try:
        info = json.loads(res.stdout.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        raise ValidationError("The file could not be read as a video.")
    # second probe for audio presence (first is restricted to v:0 for packet counting)
    res_a = sandbox.run([s.ffprobe_bin, "-v", "error", "-protocol_whitelist", "file", "-print_format", "json",
                         "-show_streams", "-select_streams", "a:0", f"file:{path}"],
                        sandbox.Limits(cpu_s=30, timeout_s=60, mem_bytes=1024 ** 3))
    audio = []
    if res_a.returncode == 0:
        try:
            audio = json.loads(res_a.stdout.decode()).get("streams", [])
        except json.JSONDecodeError:
            audio = []
    info["_audio"] = audio
    return info


def _fps(rate: str | None) -> float:
    try:
        n, d = (rate or "0/1").split("/")
        return float(n) / float(d) if float(d) else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def validate(path: Path, filename: str, s: Settings) -> dict:
    ext = Path(filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValidationError(f"Unsupported file type '{ext or 'none'}'. Use MP4, MOV, M4V, MKV, WEBM or AVI.",
                              "unsupported_type")
    with open(path, "rb") as f:
        head = f.read(64)
    if sniff_container(head) is None:
        raise ValidationError("File contents don't match a supported video container.", "unsupported_type")

    info = probe(path, s)
    fmt = info.get("format", {})
    streams = [st for st in info.get("streams", []) if st.get("codec_type") == "video"]
    if not streams:
        raise ValidationError("No video stream found.")
    v = streams[0]
    container = fmt.get("format_name", "")
    if container not in s.allowed_containers:
        raise ValidationError(f"Container '{container}' is not allowed.", "unsupported_type")
    codec = v.get("codec_name", "")
    if codec not in s.allowed_video_codecs:
        raise ValidationError(f"Video codec '{codec}' is not supported.", "unsupported_codec")
    w, h = int(v.get("width") or 0), int(v.get("height") or 0)
    if w < s.min_dimension or h < s.min_dimension:
        raise ValidationError(f"Resolution {w}×{h} is too small.", "bad_resolution")
    if w > s.max_width or h > s.max_height:
        raise ValidationError(f"Resolution {w}×{h} exceeds the {s.max_width}×{s.max_height} limit.", "bad_resolution")
    try:
        duration = float(fmt.get("duration") or v.get("duration") or 0)
    except ValueError:
        duration = 0.0
    if duration <= 0.2:
        raise ValidationError("The clip is too short or its duration is unreadable.", "bad_duration")
    if duration > s.max_duration_s:
        raise ValidationError(f"Clip is {duration:.0f}s; the limit is {s.max_duration_s:.0f}s.", "bad_duration")
    fps = _fps(v.get("avg_frame_rate")) or _fps(v.get("r_frame_rate"))
    if not (1 <= fps <= 240):
        raise ValidationError("Frame rate is missing or out of range.", "bad_framerate")

    rotation = 0
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rotation = int(sd["rotation"]) % 360
    rotation = int((v.get("tags") or {}).get("rotate", rotation)) % 360
    sar = v.get("sample_aspect_ratio", "1:1")
    try:
        sn, sd_ = (int(x) for x in sar.split(":"))
        par = sn / sd_ if sn and sd_ else 1.0
    except ValueError:
        par = 1.0
    dw, dh = (h, w) if rotation in (90, 270) else (w, h)
    audio = info.get("_audio") or []
    return {
        "container": container,
        "video_codec": codec,
        "audio_codec": audio[0].get("codec_name") if audio else None,
        "width": w,
        "height": h,
        "display_aspect": round(dw * (par if rotation in (0, 180) else 1) / dh, 4),
        "fps": round(fps, 3),
        "duration_s": round(duration, 3),
        "bitrate": int(fmt["bit_rate"]) if str(fmt.get("bit_rate", "")).isdigit() else None,
        "pix_fmt": v.get("pix_fmt"),
        "rotation": rotation,
        "frame_count": int(v["nb_read_packets"]) if str(v.get("nb_read_packets", "")).isdigit() else None,
    }
