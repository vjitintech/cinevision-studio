"""ASGI application: routes, security middleware, SPA hosting."""
from __future__ import annotations

import hmac
import os
import logging
import threading
import time
from pathlib import Path

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from . import db, retention, security
from .config import settings
from .web import ApiError

log = logging.getLogger("cinevision")

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
       "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; "
       "frame-ancestors 'none'; worker-src 'self' blob:")
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
CSRF_EXEMPT = {"/api/auth/login", "/api/auth/register"}


class SecurityMiddleware:
    """Pure ASGI: security headers, Origin + CSRF enforcement, coarse API rate limit, query-free access log."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        req = Request(scope)
        path, method = scope["path"], scope["method"]
        started = time.monotonic()

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                h = message.setdefault("headers", [])
                existing = {k.lower() for k, _ in h}
                add = {
                    b"content-security-policy": CSP.encode(),
                    b"x-content-type-options": b"nosniff",
                    b"x-frame-options": b"DENY",
                    b"referrer-policy": b"no-referrer",
                    b"permissions-policy": b"camera=(), microphone=(), geolocation=(), payment=(), usb=()",
                    b"cross-origin-opener-policy": b"same-origin",
                    b"cross-origin-resource-policy": b"same-origin",
                }
                if settings().cookie_secure:
                    add[b"strict-transport-security"] = b"max-age=31536000"
                if path.startswith("/api/") and b"cache-control" not in existing:
                    add[b"cache-control"] = b"no-store"
                h.extend((k, v) for k, v in add.items() if k not in existing)
                # access log without query strings (media URLs carry short-lived tokens)
                log.info("%s %s %s %.0fms", method, path, message["status"], (time.monotonic() - started) * 1000)
            await send(message)

        if path.startswith("/api/"):
            from .web import client_ip
            wait = security.limiter.hit(f"api:{client_ip(req)}", 600, 60)
            if wait:
                return await _error(429, "rate_limited", "Too many requests.", {"Retry-After": str(wait)})(scope, receive, send_wrapper)
            if method in UNSAFE:
                origin = req.headers.get("origin")
                if origin is None or origin.rstrip("/") not in settings().allowed_origins:
                    return await _error(403, "bad_origin", "Cross-origin request refused.")(scope, receive, send_wrapper)
                if path not in CSRF_EXEMPT:
                    sess = security.load_session(req.cookies.get(security.SESSION_COOKIE))
                    if sess is not None:
                        sent = req.headers.get("x-csrf-token", "")
                        if not hmac.compare_digest(sent.encode(), sess["csrf_token"].encode()):
                            return await _error(403, "csrf_failed", "Security token missing or invalid. Reload the page.")(
                                scope, receive, send_wrapper)
        return await self.app(scope, receive, send_wrapper)


def _error(status: int, code: str, message: str, headers: dict | None = None) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status, headers=headers)


async def api_error_handler(request: Request, exc: ApiError):
    return _error(exc.status, exc.code, exc.message, exc.headers)


async def http_error_handler(request: Request, exc: HTTPException):
    code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
    return _error(exc.status_code, code, "Not found." if exc.status_code == 404 else str(exc.detail))


async def server_error_handler(request: Request, exc: Exception):
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    return _error(500, "server_error", "Something went wrong. The error has been logged on the server.")


def _spa_routes() -> list[Route]:
    dist = settings().frontend_dist

    async def spa(request: Request):
        rel = request.path_params.get("path", "")
        if rel.startswith("api/"):
            return _error(404, "not_found", "Not found.")
        if not dist.exists():
            return Response("Frontend not built. Run `npm run build` in frontend/.", media_type="text/plain", status_code=503)
        root = dist.resolve()
        candidate = (root / rel).resolve()
        if rel and root in candidate.parents and candidate.is_file():
            cache = "public, max-age=31536000, immutable" if "/assets/" in f"/{rel}" else "no-cache"
            return FileResponse(candidate, headers={"Cache-Control": cache})
        return FileResponse(root / "index.html", headers={"Cache-Control": "no-cache"})

    return [Route("/", spa), Route("/{path:path}", spa)]


def _maintenance_loop(stop: threading.Event):
    while not stop.wait(1800):
        try:
            security.purge_expired_sessions()
            retention.cleanup_temp()
            retention.apply_project_retention()
            retention.purge_old_audit()
        except Exception:
            log.exception("maintenance failed")


def create_app() -> Starlette:
    from .api import account, auth, concepts, exports, projects, videos

    s = settings()
    db.init_db()
    retention.cleanup_temp()
    routes = [*auth.routes, *projects.routes, *videos.routes, *account.routes, *concepts.routes, *exports.routes,
              *_spa_routes()]
    stop = threading.Event()

    async def lifespan(app):
        threads = [threading.Thread(target=_maintenance_loop, args=(stop,), daemon=True, name="maintenance")]
        if s.embedded_worker:
            from . import worker
            threads.append(threading.Thread(target=worker.run_forever, kwargs={"stop": stop}, daemon=True, name="worker"))
        for t in threads:
            t.start()
        yield
        stop.set()

    app = Starlette(routes=routes, lifespan=lifespan, exception_handlers={
        ApiError: api_error_handler, HTTPException: http_error_handler, Exception: server_error_handler})
    app.add_middleware(SecurityMiddleware)
    return app


def main():  # pragma: no cover
    import uvicorn
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    uvicorn.run("cinevision.app:create_app", factory=True, host=os.environ.get("CINEVISION_HOST", "127.0.0.1"),
                port=int(os.environ.get("CINEVISION_PORT", "8000")), access_log=False,
                proxy_headers=False, server_header=False)


if __name__ == "__main__":  # pragma: no cover
    main()
