"""Audit events. Never pass secrets, signed URLs, filenames or media content in `details`."""
from __future__ import annotations

import json
import logging

from . import db
from .keys import keyring

log = logging.getLogger("cinevision.audit")

_ALLOWED_DETAIL_KEYS = {"reason", "format", "kind", "count", "provider", "providers", "method", "status", "bytes"}


def record(event: str, *, user_id: str | None, outcome: str = "success", target_type: str | None = None,
           target_id: str | None = None, ip: str | None = None, details: dict | None = None) -> None:
    safe = {k: v for k, v in (details or {}).items() if k in _ALLOWED_DETAIL_KEYS}
    try:
        db.run(
            "INSERT INTO audit_events(id,user_id,event,target_type,target_id,ip_hash,outcome,details,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (db.new_id(), user_id, event, target_type, target_id, keyring().ident_hash(ip), outcome,
             json.dumps(safe) if safe else None, db.now_iso()),
        )
    except Exception:  # auditing must never take the request down, but must be visible
        log.exception("failed to write audit event %s", event)
