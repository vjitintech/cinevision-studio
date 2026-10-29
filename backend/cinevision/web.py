"""Request helpers shared by route modules: errors, JSON parsing, authentication context."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import db, security

M = TypeVar("M", bound=BaseModel)
MAX_JSON_BYTES = 256 * 1024


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, headers: dict | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.headers = status, code, message, headers or {}


def not_found() -> ApiError:
    return ApiError(404, "not_found", "Not found.")


def ok(data: Any = None, status: int = 200) -> JSONResponse:
    return JSONResponse(data if data is not None else {"ok": True}, status_code=status,
                        headers={"Cache-Control": "no-store"})


async def parse(request: Request, model: type[M]) -> M:
    body = await request.body()
    if len(body) > MAX_JSON_BYTES:
        raise ApiError(413, "too_large", "Request body too large.")
    try:
        data = json.loads(body or b"{}")
    except json.JSONDecodeError:
        raise ApiError(422, "bad_json", "Invalid JSON.")
    try:
        return model.model_validate(data)
    except ValidationError as e:
        first = e.errors()[0]
        loc = ".".join(str(p) for p in first.get("loc", ()))
        raise ApiError(422, "validation_error", f"{loc}: {first.get('msg')}" if loc else first.get("msg"))


def client_ip(request: Request) -> str:
    from .config import settings
    if settings().trust_proxy:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@dataclass
class Ctx:
    user: dict
    session: dict
    ip: str

    @property
    def uid(self) -> str:
        return self.user["id"]


def current(request: Request, *, require_mfa: bool = True) -> Ctx:
    sess = security.load_session(request.cookies.get(security.SESSION_COOKIE))
    if sess is None:
        raise ApiError(401, "unauthenticated", "Please sign in.")
    if require_mfa and not sess["mfa_passed"]:
        raise ApiError(401, "mfa_required", "Two-factor verification required.")
    user = db.one("SELECT id,email,mfa_enabled,created_at FROM users WHERE id = ?", (sess["user_id"],))
    if user is None:
        raise ApiError(401, "unauthenticated", "Please sign in.")
    return Ctx(dict(user), dict(sess), client_ip(request))


def owned_project(ctx: Ctx, pid: str) -> dict:
    row = db.owned_one("projects", pid, ctx.uid)
    if row is None or row["status"] != "active":
        raise not_found()
    return dict(row)
