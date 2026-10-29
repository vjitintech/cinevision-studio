"""Runtime configuration, read once from environment variables.

Secrets (master key, backup key) are read from files when *_FILE variables are set, so they never need to
appear in process listings or compose files.
"""
from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str | None = None) -> str | None:
    file_var = os.environ.get(f"{name}_FILE")
    if file_var:
        return Path(file_var).read_text().strip()
    return os.environ.get(name, default)


def _bool(name: str, default: bool) -> bool:
    v = _env(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    v = _env(name)
    return int(v) if v else default


@dataclass
class Settings:
    mode: str = "local"                       # local | private-server
    data_dir: Path = Path("./data")
    master_key: bytes = b""
    cookie_secure: bool = True
    public_origin: str = "https://localhost:8443"
    extra_origins: tuple = ()
    allow_registration: bool = False          # first user may always register
    max_upload_bytes: int = 500 * 1024 * 1024
    max_duration_s: float = 300.0
    max_width: int = 4096
    max_height: int = 4096
    min_dimension: int = 64
    allowed_containers: tuple = ("mov,mp4,m4a,3gp,3g2,mj2", "matroska,webm", "avi")
    allowed_video_codecs: tuple = ("h264", "hevc", "vp8", "vp9", "av1", "prores", "mpeg4", "dnxhd", "mjpeg")
    session_idle_minutes: int = 120
    session_absolute_hours: int = 12
    embedded_worker: bool = False
    rate_limit_multiplier: int = 1             # tests only; keep 1 in real deployments
    trust_proxy: bool = False                 # honour X-Forwarded-For (only behind the bundled Caddy)
    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"
    ffmpeg_timeout_s: int = 600
    clamd_socket: str | None = None
    # External / optional adapters (all disabled unless configured)
    ollama_url: str | None = None
    ollama_model: str | None = None
    external_llm_url: str | None = None
    external_llm_model: str | None = None
    external_llm_key: str | None = None
    image_gen_url: str | None = None
    video_gen_url: str | None = None
    frontend_dist: Path = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    extra: dict = field(default_factory=dict)

    @property
    def allowed_origins(self) -> set:
        return {self.public_origin, *self.extra_origins}

    @property
    def db_path(self) -> Path:
        return self.data_dir / "cinevision.db"

    @property
    def blob_dir(self) -> Path:
        return self.data_dir / "blobs"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"


def load_settings() -> Settings:
    raw_key = _env("CINEVISION_MASTER_KEY")
    if not raw_key:
        raise RuntimeError(
            "CINEVISION_MASTER_KEY (or CINEVISION_MASTER_KEY_FILE) is required. "
            "Generate one with: python scripts/gen_keys.py"
        )
    key = base64.b64decode(raw_key)
    if len(key) != 32:
        raise RuntimeError("CINEVISION_MASTER_KEY must be 32 bytes, base64-encoded")
    s = Settings(
        mode=_env("CINEVISION_MODE", "local"),
        data_dir=Path(_env("CINEVISION_DATA_DIR", "./data")).resolve(),
        master_key=key,
        cookie_secure=_bool("CINEVISION_COOKIE_SECURE", True),
        public_origin=(_env("CINEVISION_PUBLIC_ORIGIN", "https://localhost:8443") or "").rstrip("/"),
        extra_origins=tuple(o.strip().rstrip("/") for o in (_env("CINEVISION_EXTRA_ORIGINS", "") or "").split(",") if o.strip()),
        allow_registration=_bool("CINEVISION_ALLOW_REGISTRATION", False),
        max_upload_bytes=_int("CINEVISION_MAX_UPLOAD_MB", 500) * 1024 * 1024,
        max_duration_s=float(_int("CINEVISION_MAX_DURATION_S", 300)),
        embedded_worker=_bool("CINEVISION_EMBEDDED_WORKER", False),
        rate_limit_multiplier=max(1, _int("CINEVISION_RATE_LIMIT_MULTIPLIER", 1)),
        trust_proxy=_bool("CINEVISION_TRUST_PROXY", False),
        ffmpeg_bin=_env("CINEVISION_FFMPEG", "ffmpeg"),
        ffprobe_bin=_env("CINEVISION_FFPROBE", "ffprobe"),
        clamd_socket=_env("CINEVISION_CLAMD_SOCKET"),
        ollama_url=_env("CINEVISION_OLLAMA_URL"),
        ollama_model=_env("CINEVISION_OLLAMA_MODEL"),
        external_llm_url=_env("CINEVISION_EXTERNAL_LLM_URL"),
        external_llm_model=_env("CINEVISION_EXTERNAL_LLM_MODEL"),
        external_llm_key=_env("CINEVISION_EXTERNAL_LLM_KEY"),
        image_gen_url=_env("CINEVISION_IMAGE_GEN_URL"),
        video_gen_url=_env("CINEVISION_VIDEO_GEN_URL"),
    )
    dist = _env("CINEVISION_FRONTEND_DIST")
    if dist:
        s.frontend_dist = Path(dist)
    if s.mode not in {"local", "private-server"}:
        raise RuntimeError("CINEVISION_MODE must be 'local' or 'private-server'")
    for d in (s.data_dir, s.blob_dir, s.tmp_dir):
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d, 0o700)
    return s


_settings: Settings | None = None


def settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings


def reset_settings(s: Settings | None = None) -> None:
    """Used by tests."""
    global _settings
    _settings = s
