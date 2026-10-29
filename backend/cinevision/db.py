"""SQLite access layer. All SQL uses bound parameters; table names only ever come from the constant
allow-list below, never from request input."""
from __future__ import annotations

import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import settings

SCHEMA_VERSION = 1
_local = threading.local()

OWNED_TABLES = {
    "projects", "blobs", "uploaded_videos", "extracted_frames", "scene_analyses",
    "cinematography_concepts", "camera_configurations", "lighting_configurations", "storyboards",
    "animatics", "three_d_scenes", "shot_lists", "render_jobs", "video_metadata",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def now_iso(delta: timedelta | None = None) -> str:
    t = utcnow() + (delta or timedelta())
    return t.isoformat(timespec="seconds")


def new_id() -> str:
    return str(uuid.uuid4())


def is_uuid(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value.lower()
    except (ValueError, AttributeError, TypeError):
        return False


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=15, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA secure_delete=ON")
    return conn


def conn() -> sqlite3.Connection:
    path = settings().db_path
    c = getattr(_local, "conn", None)
    if c is None or getattr(_local, "path", None) != path:
        c = _connect(path)
        _local.conn, _local.path = c, path
    return c


def close_thread_connection() -> None:
    c = getattr(_local, "conn", None)
    if c is not None:
        c.close()
        _local.conn = None


def init_db() -> None:
    schema = (Path(__file__).parent / "schema.sql").read_text()
    c = conn()
    c.executescript(schema)
    row = c.execute("SELECT version FROM schema_version").fetchone()
    if row is None:
        c.execute("INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,))
    p = settings().db_path
    if p.exists():
        p.chmod(0o600)


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    c = conn()
    c.execute("BEGIN IMMEDIATE")
    try:
        yield c
        c.execute("COMMIT")
    except BaseException:
        c.execute("ROLLBACK")
        raise


def one(sql: str, params: tuple = ()) -> sqlite3.Row | None:
    return conn().execute(sql, params).fetchone()


def all_(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    return conn().execute(sql, params).fetchall()


def run(sql: str, params: tuple = ()) -> int:
    return conn().execute(sql, params).rowcount


def owned_one(table: str, row_id: str, owner_id: str) -> sqlite3.Row | None:
    """Fetch a row only if it belongs to owner. Anything else (bad id, other owner) → None (→ 404)."""
    if table not in OWNED_TABLES:
        raise ValueError("table not allowed")
    if not is_uuid(row_id):
        return None
    return one(f"SELECT * FROM {table} WHERE id = ? AND owner_id = ?", (row_id, owner_id))


def row_dict(r: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(r) if r is not None else None
