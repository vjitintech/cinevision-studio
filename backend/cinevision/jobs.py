"""Database-backed job queue: enqueue (idempotent), atomic claim, heartbeat, retry, cancel."""
from __future__ import annotations

import socket
import os
from datetime import timedelta

from . import db
from .keys import keyring

STALE_AFTER = timedelta(seconds=90)
ACTIVE = ("queued", "processing")


def enqueue(*, owner_id: str, project_id: str, kind: str, idem_key: str, params: dict | None = None,
            max_attempts: int = 3) -> dict:
    """Create a job unless one with the same idempotency key exists. Finished jobs with that key are
    re-armed (so a deliberate re-run works), active ones are returned as-is."""
    with db.tx() as c:
        row = c.execute("SELECT * FROM render_jobs WHERE idempotency_key = ?", (idem_key,)).fetchone()
        if row is not None:
            if row["status"] in ACTIVE:
                return dict(row)
            c.execute(
                "UPDATE render_jobs SET status='queued', progress=0, stage=NULL, error=NULL, attempts=0,"
                " cancel_requested=0, worker_id=NULL, heartbeat_at=NULL, started_at=NULL, finished_at=NULL,"
                " result_ref=NULL, params_enc=?, created_at=? WHERE id = ?",
                (keyring().enc_json(params or {}, f"render_jobs:params:{row['id']}"), db.now_iso(), row["id"]))
            return dict(c.execute("SELECT * FROM render_jobs WHERE id = ?", (row["id"],)).fetchone())
        jid = db.new_id()
        c.execute(
            "INSERT INTO render_jobs(id,owner_id,project_id,kind,status,idempotency_key,params_enc,max_attempts,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (jid, owner_id, project_id, kind, "queued", idem_key,
             keyring().enc_json(params or {}, f"render_jobs:params:{jid}"), max_attempts, db.now_iso()))
        return dict(c.execute("SELECT * FROM render_jobs WHERE id = ?", (jid,)).fetchone())


def params(job: dict) -> dict:
    return keyring().dec_json(job["params_enc"], f"render_jobs:params:{job['id']}") or {}


def worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def requeue_stale() -> int:
    cutoff = db.now_iso(-STALE_AFTER)
    n = db.run("UPDATE render_jobs SET status='queued', worker_id=NULL, stage='requeued after worker loss'"
               " WHERE status='processing' AND (heartbeat_at IS NULL OR heartbeat_at < ?) AND attempts < max_attempts",
               (cutoff,))
    db.run("UPDATE render_jobs SET status='failed', finished_at=?, error='The job stopped responding too many times.'"
           " WHERE status='processing' AND (heartbeat_at IS NULL OR heartbeat_at < ?) AND attempts >= max_attempts",
           (db.now_iso(), cutoff))
    return n


def claim(wid: str) -> dict | None:
    now = db.now_iso()
    row = db.one(
        "UPDATE render_jobs SET status='processing', worker_id=?, heartbeat_at=?, started_at=?,"
        " attempts=attempts+1, progress=0, error=NULL"
        " WHERE id = (SELECT id FROM render_jobs WHERE status='queued' AND cancel_requested=0"
        "             ORDER BY created_at LIMIT 1) AND status='queued' RETURNING *",
        (wid, now, now))
    return dict(row) if row else None


class Cancelled(Exception):
    pass


class JobContext:
    def __init__(self, job: dict, wid: str):
        self.job, self.wid = job, wid

    @property
    def id(self) -> str:
        return self.job["id"]

    def progress(self, value: float, stage: str) -> None:
        row = db.one("UPDATE render_jobs SET progress=?, stage=?, heartbeat_at=? WHERE id=? AND worker_id=?"
                     " RETURNING cancel_requested", (round(max(0, min(1, value)), 3), stage, db.now_iso(), self.id, self.wid))
        if row is None or row["cancel_requested"]:
            raise Cancelled()

    def check_cancel(self) -> None:
        row = db.one("SELECT cancel_requested, status FROM render_jobs WHERE id=?", (self.id,))
        if row is None or row["cancel_requested"] or row["status"] != "processing":
            raise Cancelled()


def complete(job_id: str, result_ref: str | None = None) -> None:
    db.run("UPDATE render_jobs SET status='completed', progress=1, stage='done', finished_at=?, result_ref=?"
           " WHERE id=? AND status='processing'", (db.now_iso(), result_ref, job_id))


def fail(job: dict, message: str, retryable: bool) -> None:
    if retryable and job["attempts"] < job["max_attempts"]:
        db.run("UPDATE render_jobs SET status='queued', worker_id=NULL, stage='retrying', error=? WHERE id=?",
               (message, job["id"]))
    else:
        db.run("UPDATE render_jobs SET status='failed', finished_at=?, error=? WHERE id=?",
               (db.now_iso(), message, job["id"]))


def mark_cancelled(job_id: str) -> None:
    db.run("UPDATE render_jobs SET status='cancelled', finished_at=?, stage='cancelled' WHERE id=?",
           (db.now_iso(), job_id))


def request_cancel(job_id: str, owner_id: str) -> bool:
    n = db.run("UPDATE render_jobs SET cancel_requested=1 WHERE id=? AND owner_id=? AND status IN ('queued','processing')",
               (job_id, owner_id))
    db.run("UPDATE render_jobs SET status='cancelled', finished_at=? WHERE id=? AND owner_id=? AND status='queued'",
           (db.now_iso(), job_id, owner_id))
    return n > 0


def retry(job_id: str, owner_id: str) -> bool:
    return db.run("UPDATE render_jobs SET status='queued', attempts=0, cancel_requested=0, error=NULL, progress=0,"
                  " stage=NULL, finished_at=NULL, created_at=? WHERE id=? AND owner_id=? AND status IN ('failed','cancelled')",
                  (db.now_iso(), job_id, owner_id)) > 0


def public(job: dict) -> dict:
    return {k: job[k] for k in ("id", "kind", "status", "progress", "stage", "error", "attempts", "max_attempts",
                                "created_at", "started_at", "finished_at", "result_ref")}
