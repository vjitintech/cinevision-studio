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
    except Exception as e:
        log.exception("job %s (%s) crashed", job["id"], job["kind"])
        last = job["attempts"] >= job["max_attempts"]
        # only the exception type is shown (never its text, which could contain paths or data)
        jobs.fail(job, f"Processing failed unexpectedly ({type(e).__name__}). " +
                  ("Use Retry, and if it fails again, copy the error lines from the window running CineVision."
                   if last else "It will be retried automatically."), retryable=True)
    return True


def _loop(wid: str, stop: threading.Event | None, poll_s: float, sweeper: bool) -> None:
    last_sweep = 0.0
    while not (stop and stop.is_set()):
        try:
            if sweeper and time.monotonic() - last_sweep > 30:
                jobs.requeue_stale()
                last_sweep = time.monotonic()
            if not run_one(wid):
                time.sleep(poll_s)
        except Exception:
            log.exception("worker loop error")
            time.sleep(2)
    db.close_thread_connection()


def run_forever(stop: threading.Event | None = None, poll_s: float = 0.5, workers: int | None = None) -> None:
    """Run the job loop. With CINEVISION_WORKERS > 1 (default: half the CPU cores, at most 4) several jobs run at
    once — e.g. many clip analyses of a movie assembly — each thread with its own DB connection and worker id."""
    from .config import settings
    _load_handlers()
    db.init_db()
    n = max(1, workers if workers is not None else getattr(settings(), "workers", 1))
    base = jobs.worker_id()
    helpers = [threading.Thread(target=_loop, args=(f"{base}:t{i}", stop, poll_s, False), daemon=True, name=f"worker-{i}")
               for i in range(1, n)]
    for t in helpers:
        t.start()
    log.info("worker %s started with %d thread(s)", base, n)
    _loop(base, stop, poll_s, True)
    for t in helpers:
        t.join(timeout=5)


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    run_forever()
