# API specification

Base path `/api`. JSON in/out unless noted. All IDs are UUIDv4.

**Auth model** — session cookie `cv_session` (HttpOnly, SameSite=Strict, Secure when TLS). Every
state-changing request (POST/PUT/PATCH/DELETE) must send `X-CSRF-Token` matching the session's token and
an `Origin` equal to the app origin. Responses carry `Cache-Control: no-store`.

**Errors** — `{"error": {"code": "...", "message": "..."}}`. Objects that do not exist *or* belong to
another user → `404 not_found`. Validation → `422`. Auth → `401`. MFA step pending → `401 mfa_required`.
Rate limit → `429` with `Retry-After`.

## System
| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | liveness (no data) |
| GET | `/api/system/capabilities` | deployment mode, limits, adapter status (`available`, `not_configured`, `disabled`, `needs_consent`) |

## Auth
| Method | Path | Body | Description |
|---|---|---|---|
| GET | `/api/auth/session` | – | `{authenticated, mfa_required, user, csrf_token, registration_open}` |
| POST | `/api/auth/register` | `{email, password}` | only while registration is open (first user, or `ALLOW_REGISTRATION=true`) |
| POST | `/api/auth/login` | `{email, password}` | sets cookie; `mfa_required:true` if TOTP enabled |
| POST | `/api/auth/mfa/verify` | `{code}` | TOTP or recovery code completes login |
| POST | `/api/auth/logout` | – | revokes session |
| POST | `/api/auth/logout-all` | – | revokes every session of the user |
| POST | `/api/auth/password` | `{current_password, new_password}` | revokes other sessions |
| POST | `/api/auth/mfa/setup` | `{password}` | returns `{secret, otpauth_uri}` (not yet enabled) |
| POST | `/api/auth/mfa/enable` | `{code}` | enables MFA, returns one-time recovery codes |
| POST | `/api/auth/mfa/disable` | `{password, code}` | |

## Privacy & audit
| Method | Path | Description |
|---|---|---|
| GET/PUT | `/api/privacy` | consent per provider, retention periods |
| GET | `/api/audit?limit=` | caller's own audit events |
| GET | `/api/sessions` | active sessions (coarse UA hint, timestamps) |

## Projects
| Method | Path | Description |
|---|---|---|
| GET | `/api/projects` | list own projects |
| POST | `/api/projects` | `{title, brief?}` |
| GET | `/api/projects/{pid}` | project + video + metadata + latest jobs |
| PATCH | `/api/projects/{pid}` | `{title?, brief?}` (brief = director's creative controls) |
| DELETE | `/api/projects/{pid}` | cancels jobs, deletes rows, destroys keys and files; `204` |

## Video
| Method | Path | Description |
|---|---|---|
| POST | `/api/projects/{pid}/video` | `multipart/form-data` field `file`; validates, encrypts, enqueues ingest. One clip per project (replace = delete + upload). |
| DELETE | `/api/projects/{pid}/video` | remove the clip and everything derived from it (to replace it) |
| POST | `/api/projects/{pid}/media-token` | `{blob_id}` → `{url, expires_in}` short-lived (≤5 min) token bound to user+session+blob |
| GET | `/api/media/{blob_id}?t=…` | decrypting stream with HTTP Range support; requires session **and** valid token |
| GET | `/api/projects/{pid}/frames` | keyframe list (ids, timestamps, shot index) |

## Jobs
| Method | Path | Description |
|---|---|---|
| GET | `/api/projects/{pid}/jobs` | jobs with status/progress/stage/error |
| POST | `/api/jobs/{jid}/retry` | re-queue a failed/cancelled job |
| POST | `/api/jobs/{jid}/cancel` | request cancellation |

## Analysis
| Method | Path | Description |
|---|---|---|
| GET | `/api/projects/{pid}/analysis` | analysis JSON (404 until produced; never fabricated) |
| POST | `/api/projects/{pid}/analysis/rerun` | enqueue `analyze` again |

## Concepts
| Method | Path | Description |
|---|---|---|
| POST | `/api/projects/{pid}/concepts/generate` | `{controls}` → job; idempotent for identical controls |
| GET | `/api/projects/{pid}/concepts` | current revisions of the latest batch (+`?all=1` for every batch) |
| GET | `/api/concepts/{cid}` | full concept |
| GET | `/api/concepts/{cid}/history` | revisions in the lineage |
| POST | `/api/concepts/{cid}/refine` | `{command}` or `{patch}` → new revision; returns `{concept, applied, unrecognized}` |
| POST | `/api/concepts/{cid}/select` | mark as chosen concept |
| POST | `/api/concepts/{cid}/revert` | restore an older revision (as a new revision) |
| GET/PUT/DELETE | `/api/concepts/{cid}/camera/{shot}` | camera planner state (DELETE = back to concept default) |
| GET/PUT/DELETE | `/api/concepts/{cid}/lighting/{shot}` | lighting plan |
| GET/PUT | `/api/concepts/{cid}/storyboard` | storyboard + animatic timing |
| GET/PUT | `/api/concepts/{cid}/scene3d` | 3D proxy scene |
| POST | `/api/concepts/{cid}/shotlist` | snapshot a shot list |
| GET | `/api/concepts/{cid}/shotlists` | list snapshots |

## Exports (explicit, user-initiated downloads)
| Method | Path | Description |
|---|---|---|
| POST | `/api/shotlists/{sid}/export` | `{format: csv|json|pdf}` → file download (`Content-Disposition: attachment`) |
| POST | `/api/projects/{pid}/export` | structured project file (JSON, no media) |

## AI adapters
| Method | Path | Description |
|---|---|---|
| GET | `/api/adapters` | registry with status and data-flow disclosure per adapter |
| POST | `/api/concepts/{cid}/ai-preview` | Mode D. Returns `409 not_configured` or `403 consent_required` unless configured and consented |
