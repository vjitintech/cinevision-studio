"""Job-queue semantics (direct, no HTTP)."""
import os

import pytest

from cinevision import config, db, jobs


@pytest.fixture
def env(tmp_path):
    s = config.Settings(data_dir=tmp_path, master_key=os.urandom(32))
    for d in (s.data_dir, s.blob_dir, s.tmp_dir):
        d.mkdir(parents=True, exist_ok=True)
    config.reset_settings(s)
    db.close_thread_connection()
    db.init_db()
    uid, pid = db.new_id(), db.new_id()
    db.run("INSERT INTO users(id,email,password_hash,created_at) VALUES (?,?,?,?)", (uid, "j@x.test", "x", db.now_iso()))
    db.run("INSERT INTO projects(id,owner_id,title_enc,created_at,updated_at) VALUES (?,?,?,?,?)",
           (pid, uid, "x", db.now_iso(), db.now_iso()))
    yield uid, pid
    db.close_thread_connection()
    config.reset_settings(None)


def test_idempotent_enqueue_and_atomic_claim(env):
    uid, pid = env
    a = jobs.enqueue(owner_id=uid, project_id=pid, kind="k", idem_key="k:1", params={"x": 1})
    b = jobs.enqueue(owner_id=uid, project_id=pid, kind="k", idem_key="k:1", params={"x": 2})
    assert a["id"] == b["id"]
    assert jobs.params(b) == {"x": 1}  # active job is not overwritten
    c1 = jobs.claim("w1")
    assert c1["id"] == a["id"] and c1["status"] == "processing"
    assert jobs.claim("w2") is None  # nothing left to claim


def test_retry_then_fail_after_max_attempts(env):
    uid, pid = env
    jobs.enqueue(owner_id=uid, project_id=pid, kind="k", idem_key="k:2", max_attempts=2)
    j = jobs.claim("w")
    jobs.fail(j, "boom", retryable=True)
    assert db.one("SELECT status FROM render_jobs WHERE id=?", (j["id"],))["status"] == "queued"
    j = jobs.claim("w")
    assert j["attempts"] == 2
    jobs.fail(j, "boom", retryable=True)
    assert db.one("SELECT status, error FROM render_jobs WHERE id=?", (j["id"],))["status"] == "failed"
    assert jobs.retry(j["id"], uid)
    assert not jobs.retry(j["id"], "someone-else")


def test_cancel_and_stale_requeue(env):
    uid, pid = env
    jobs.enqueue(owner_id=uid, project_id=pid, kind="k", idem_key="k:3")
    j = jobs.claim("w")
    ctx = jobs.JobContext(j, "w")
    ctx.progress(0.5, "half")
    assert jobs.request_cancel(j["id"], uid)
    with pytest.raises(jobs.Cancelled):
        ctx.progress(0.6, "more")
    # a processing job whose worker vanished is re-queued
    jobs.enqueue(owner_id=uid, project_id=pid, kind="k", idem_key="k:4")
    j2 = jobs.claim("w-dead")
    db.run("UPDATE render_jobs SET heartbeat_at=? WHERE id=?", ("2000-01-01T00:00:00+00:00", j2["id"]))
    assert jobs.requeue_stale() == 1
    assert db.one("SELECT status FROM render_jobs WHERE id=?", (j2["id"],))["status"] == "queued"
