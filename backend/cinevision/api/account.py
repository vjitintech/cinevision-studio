"""Jobs, privacy settings, audit log, sessions, system capabilities."""
from __future__ import annotations

import json

from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.routing import Route

from .. import audit, db, jobs
from ..adapters import registry
from ..config import settings
from ..web import ApiError, current, not_found, ok, owned_project, parse


# ---------------------------------------------------------------- jobs
async def list_jobs(request: Request):
    ctx = current(request)
    pid = request.path_params["pid"]
    owned_project(ctx, pid)
    rows = db.all_("SELECT * FROM render_jobs WHERE project_id=? AND owner_id=? ORDER BY created_at DESC", (pid, ctx.uid))
    return ok({"jobs": [jobs.public(dict(r)) for r in rows]})


async def retry_job(request: Request):
    ctx = current(request)
    if not jobs.retry(request.path_params["jid"], ctx.uid):
        raise ApiError(409, "not_retryable", "Only failed or cancelled jobs can be retried.")
    return ok()


async def cancel_job(request: Request):
    ctx = current(request)
    if not jobs.request_cancel(request.path_params["jid"], ctx.uid):
        raise ApiError(409, "not_cancellable", "This job is not running.")
    return ok()


# ---------------------------------------------------------------- privacy
class PrivacyBody(BaseModel):
    external_ai_consent: bool = False
    consented_providers: list[str] = Field(default_factory=list, max_length=10)
    temp_retention_hours: int = Field(default=24, ge=1, le=24 * 30)
    project_retention_days: int = Field(default=0, ge=0, le=3650)


def _privacy(uid: str) -> dict:
    r = db.one("SELECT * FROM privacy_settings WHERE user_id=?", (uid,))
    return {
        "external_ai_consent": bool(r["external_ai_consent"]),
        "consented_providers": json.loads(r["consented_providers"]),
        "consent_updated_at": r["consent_updated_at"],
        "temp_retention_hours": r["temp_retention_hours"],
        "project_retention_days": r["project_retention_days"],
        "telemetry_enabled": False,
        "training_use": "never",
    }


async def get_privacy(request: Request):
    ctx = current(request)
    return ok(_privacy(ctx.uid))


async def put_privacy(request: Request):
    ctx = current(request)
    body = await parse(request, PrivacyBody)
    known = {a["id"] for a in registry.list_adapters(ctx.uid) if a["external"]}
    providers = sorted(set(body.consented_providers) & known) if body.external_ai_consent else []
    before = _privacy(ctx.uid)
    db.run("UPDATE privacy_settings SET external_ai_consent=?, consented_providers=?, consent_updated_at=?,"
           " temp_retention_hours=?, project_retention_days=? WHERE user_id=?",
           (int(bool(providers)), json.dumps(providers),
            db.now_iso() if providers != before["consented_providers"] else before["consent_updated_at"],
            body.temp_retention_hours, body.project_retention_days, ctx.uid))
    if providers != before["consented_providers"]:
        audit.record("privacy.consent_change", user_id=ctx.uid, ip=ctx.ip, details={"providers": providers})
    return ok(_privacy(ctx.uid))


# ---------------------------------------------------------------- audit & sessions
async def get_audit(request: Request):
    ctx = current(request)
    try:
        limit = max(1, min(500, int(request.query_params.get("limit", "100"))))
    except ValueError:
        limit = 100
    rows = db.all_("SELECT event,target_type,target_id,outcome,details,created_at FROM audit_events WHERE user_id=?"
                   " ORDER BY created_at DESC LIMIT ?", (ctx.uid, limit))
    return ok({"events": [dict(r) | {"details": json.loads(r["details"]) if r["details"] else None} for r in rows]})


async def get_sessions(request: Request):
    ctx = current(request)
    rows = db.all_("SELECT token_hash, created_at, last_seen_at, expires_at, ua_hint FROM sessions WHERE user_id=? AND mfa_passed=1"
                   " ORDER BY last_seen_at DESC", (ctx.uid,))
    return ok({"sessions": [{"current": r["token_hash"] == ctx.session["token_hash"], "created_at": r["created_at"],
                             "last_seen_at": r["last_seen_at"], "expires_at": r["expires_at"], "browser": r["ua_hint"]}
                            for r in rows]})


# ---------------------------------------------------------------- system
async def health(request: Request):
    return ok({"status": "ok"})


async def capabilities(request: Request):
    ctx = current(request)
    s = settings()
    return ok({
        "mode": s.mode,
        "limits": {"max_upload_mb": s.max_upload_bytes // (1024 * 1024), "max_duration_s": s.max_duration_s,
                   "max_width": s.max_width, "max_height": s.max_height,
                   "extensions": [".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"],
                   "codecs": list(s.allowed_video_codecs)},
        "malware_scanning": "configured" if s.clamd_socket else "not_configured",
        "adapters": registry.list_adapters(ctx.uid),
        "telemetry": "none",
        "external_calls_default": "disabled",
    })


async def adapters(request: Request):
    ctx = current(request)
    return ok({"adapters": registry.list_adapters(ctx.uid)})


routes = [
    Route("/api/projects/{pid}/jobs", list_jobs, methods=["GET"]),
    Route("/api/jobs/{jid}/retry", retry_job, methods=["POST"]),
    Route("/api/jobs/{jid}/cancel", cancel_job, methods=["POST"]),
    Route("/api/privacy", get_privacy, methods=["GET"]),
    Route("/api/privacy", put_privacy, methods=["PUT"]),
    Route("/api/audit", get_audit, methods=["GET"]),
    Route("/api/sessions", get_sessions, methods=["GET"]),
    Route("/api/health", health, methods=["GET"]),
    Route("/api/system/capabilities", capabilities, methods=["GET"]),
    Route("/api/adapters", adapters, methods=["GET"]),
]
