"""Deletion, retention and temp-file cleanup."""
from __future__ import annotations

import shutil
import time
from datetime import timedelta

from . import audit, db, storage
from .config import settings


def delete_project(project_id: str, owner_id: str, *, reason: str = "user_request", ip: str | None = None) -> bool:
    row = db.owned_one("projects", project_id, owner_id)
    if row is None:
        return False
    # 1) stop new work and cancel queued/running jobs
    db.run("UPDATE projects SET status='deleting' WHERE id=?", (project_id,))
    db.run("UPDATE render_jobs SET cancel_requested=1, status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END"
           " WHERE project_id=? AND status IN ('queued','processing')", (project_id,))
    blob_ids = [r["id"] for r in db.all_("SELECT id FROM blobs WHERE project_id=?", (project_id,))]
    # 2) delete rows (FK cascades remove videos, frames, analyses, concepts, configs, storyboards, jobs, shot lists).
    #    Removing blob rows destroys the wrapped data keys → remaining ciphertext is unrecoverable.
    with db.tx() as c:
        c.execute("DELETE FROM extracted_frames WHERE project_id=?", (project_id,))
        c.execute("DELETE FROM uploaded_videos WHERE project_id=?", (project_id,))
        c.execute("DELETE FROM blobs WHERE project_id=?", (project_id,))
        c.execute("DELETE FROM projects WHERE id=? AND owner_id=?", (project_id, owner_id))
    # 3) remove encrypted files
    for bid in blob_ids:
        storage.destroy_blob_file(bid)
    audit.record("project.delete", user_id=owner_id, target_type="project", target_id=project_id, ip=ip,
                 details={"reason": reason, "count": len(blob_ids)})
    return True


def temp_retention_hours() -> int:
    """Strictest owner setting wins (scratch space is shared by the deployment)."""
    try:
        row = db.one("SELECT MIN(temp_retention_hours) h FROM privacy_settings")
        return int(row["h"]) if row and row["h"] else 24
    except Exception:
        return 24


def cleanup_temp(max_age_hours: int | None = None) -> int:
    """Remove job scratch dirs and partial uploads left behind by crashes. Normal jobs delete their own
    scratch space immediately (storage.private_tempdir); this only catches crash leftovers."""
    if max_age_hours is None:
        max_age_hours = temp_retention_hours()
    root = settings().tmp_dir
    if not root.exists():
        return 0
    cutoff = time.time() - max_age_hours * 3600
    removed = 0
    for p in root.iterdir():
        try:
            if p.stat().st_mtime < cutoff:
                shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
                removed += 1
        except FileNotFoundError:
            pass
    # encrypted files whose key row is gone (deleted while Windows still had them open) are unreadable leftovers
    try:
        known = {r["id"] for r in db.all_("SELECT id FROM blobs")}
        for f in settings().blob_dir.glob("*/*.cve"):
            if f.stem not in known:
                try:
                    f.unlink()
                    removed += 1
                except OSError:
                    pass
    except Exception:
        pass
    for partial in settings().blob_dir.glob("*/*.partial"):
        if partial.stat().st_mtime < cutoff:
            partial.unlink(missing_ok=True)
            removed += 1
    return removed


def apply_project_retention() -> int:
    """Delete projects older than each user's configured project_retention_days (0 = keep)."""
    n = 0
    for ps in db.all_("SELECT user_id, project_retention_days FROM privacy_settings WHERE project_retention_days > 0"):
        cutoff = db.now_iso(-timedelta(days=ps["project_retention_days"]))
        for p in db.all_("SELECT id FROM projects WHERE owner_id=? AND updated_at < ?", (ps["user_id"], cutoff)):
            if delete_project(p["id"], ps["user_id"], reason="retention_policy"):
                n += 1
    return n


def purge_old_audit(days: int = 365) -> None:
    db.run("DELETE FROM audit_events WHERE created_at < ?", (db.now_iso(-timedelta(days=days)),))
