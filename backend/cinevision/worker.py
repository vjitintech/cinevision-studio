"""Background worker: claims jobs from render_jobs and runs the matching pipeline stage.

Run standalone with `python -m cinevision.worker`, or embedded in the API process for single-process local
setups (CINEVISION_EMBEDDED_WORKER=true).
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from . import db, jobs

log = logging.getLogger("cinevision.worker")

HANDLERS: dict[str, Callable[[jobs.JobContext, dict], str | None]] = {}


class UserFacingError(Exception):
    """Raised by handlers with a message safe to show the owner. `retryable` controls automatic retries."""

    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


def handler(kind: str):
    def deco(fn):
        HANDLERS[kind] = fn
        return fn
    return deco


def _load_handlers() -> None:
    # importing registers handlers via @handler
    from .pipeline import stages  # noqa: F401


def run_one(wid: str) -> bool:
    job = jobs.claim(wid)
    if job is None:
        return False
    ctx = jobs.JobContext(job, wid)
    fn = HANDLERS.get(job["kind"])
    try:
        if fn is None:
            raise UserFacingError(f"Unknown job type '{job['kind']}'.")
        result_ref = fn(ctx, jobs.params(job))
        jobs.complete(job["id"], result_ref)
    except jobs.Cancelled:
        jobs.mark_cancelled(job["id"])
    except UserFacingError as e:
        jobs.fail(job, str(e), e.retryable)
    except Exception:
        log.exception("job %s (%s) crashed", job["id"], job["kind"])
        jobs.fail(job, "Processing failed unexpectedly. It will be retried automatically.", retryable=True)
    return True


def run_forever(stop: threading.Event | None = None, poll_s: float = 0.5) -> None:
    _load_handlers()
    db.init_db()
    wid = jobs.worker_id()
    last_sweep = 0.0
    log.info("worker %s started", wid)
    while not (stop and stop.is_set()):
        try:
            if time.monotonic() - last_sweep > 30:
                jobs.requeue_stale()
                last_sweep = time.monotonic()
            if not run_one(wid):
                time.sleep(poll_s)
        except Exception:
            log.exception("worker loop error")
            time.sleep(2)
    db.close_thread_connection()


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    run_forever()
