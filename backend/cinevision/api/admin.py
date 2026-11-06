"""Instance administration: sign-up policy and exposure status, admins, invite codes.

* Any signed-in user may read ``/api/admin/security`` (what the Security page banner shows; no other users' data).
* Only the owner may list accounts and make or remove admins.
* The owner and admins may create, list and remove invite codes.
"""
from __future__ import annotations

from pydantic import BaseModel
from starlette.requests import Request
from starlette.routing import Route

from .. import audit, db, roles
from ..web import ApiError, current, ok, parse


class RoleBody(BaseModel):
    admin: bool


def _require_owner(ctx) -> None:
    if not roles.is_owner(ctx.uid):
        raise ApiError(403, "forbidden", "Only the owner can do this.")


def _require_admin(ctx) -> None:
    if not roles.is_admin(ctx.uid):
        raise ApiError(403, "forbidden", "Only the owner or an admin can do this.")


async def security_status(request: Request):
    ctx = current(request)
    return ok({"signup": roles.signup_state(), "exposure": roles.exposure(),
               "me": {"role": roles.role_of(ctx.uid), "is_owner": roles.is_owner(ctx.uid), "is_admin": roles.is_admin(ctx.uid)}})


async def list_users(request: Request):
    ctx = current(request)
    _require_owner(ctx)
    return ok({"users": roles.list_users()})


async def set_role(request: Request):
    ctx = current(request)
    _require_owner(ctx)
    uid = request.path_params["uid"]
    body = await parse(request, RoleBody)
    if not db.is_uuid(uid) or db.one("SELECT 1 FROM users WHERE id = ?", (uid,)) is None:
        raise ApiError(404, "not_found", "Not found.")
    if roles.is_owner(uid):
        raise ApiError(409, "is_owner", "The owner is always an admin.")
    roles.set_admin(uid, body.admin, ctx.uid)
    audit.record("admin.role_change", user_id=ctx.uid, target_type="user", target_id=uid, ip=ctx.ip,
                 details={"role": "admin" if body.admin else "member"})
    return ok({"users": roles.list_users()})


async def list_invites(request: Request):
    ctx = current(request)
    _require_admin(ctx)
    return ok({"invites": roles.list_invites(), "signup": roles.signup_state()})


async def create_invite(request: Request):
    ctx = current(request)
    _require_admin(ctx)
    try:
        inv = roles.create_invite(ctx.uid)
    except ValueError as e:
        raise ApiError(409, "too_many_invites", str(e)) from None
    audit.record("admin.invite_create", user_id=ctx.uid, target_type="invite", target_id=inv["id"], ip=ctx.ip)
    return ok(inv, 201)  # the code is shown this once; only its hash is stored


async def delete_invite(request: Request):
    ctx = current(request)
    _require_admin(ctx)
    iid = request.path_params["iid"]
    if not db.is_uuid(iid) or not roles.delete_invite(iid):
        raise ApiError(404, "not_found", "Not found.")
    audit.record("admin.invite_delete", user_id=ctx.uid, target_type="invite", target_id=iid, ip=ctx.ip)
    return ok()


routes = [
    Route("/api/admin/security", security_status, methods=["GET"]),
    Route("/api/admin/users", list_users, methods=["GET"]),
    Route("/api/admin/users/{uid}/role", set_role, methods=["PUT"]),
    Route("/api/admin/invites", list_invites, methods=["GET"]),
    Route("/api/admin/invites", create_invite, methods=["POST"]),
    Route("/api/admin/invites/{iid}", delete_invite, methods=["DELETE"]),
]
