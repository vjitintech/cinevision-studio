# Data model

Source of truth: [`backend/cinevision/schema.sql`](../backend/cinevision/schema.sql).

## Entities

| Entity (spec) | Table | Notes |
|---|---|---|
| User | `users` | scrypt password hash, optional TOTP secret (encrypted), hashed recovery codes, lockout counters |
| — | `sessions` | server-side sessions; only `sha256(token)` is stored |
| PrivacySettings | `privacy_settings` | external-AI consent per provider, temp/project retention, telemetry flag (always off) |
| PrivateProject | `projects` | title and director brief encrypted |
| — | `blobs` | every file on disk; per-file data key wrapped by the KEK |
| UploadedVideo | `uploaded_videos` | original + browser proxy blob refs, scan status |
| VideoMetadata | `video_metadata` | ffprobe facts (codec, size, fps, duration, rotation) |
| ExtractedFrame | `extracted_frames` | encrypted JPEG keyframes, timestamp, shot index |
| SceneAnalysis | `scene_analyses` | encrypted analysis JSON + engine/version record |
| CinematographyConcept | `cinematography_concepts` | revisions share a `lineage_id`; `parent_id` links history; `is_current` marks head |
| CameraConfiguration | `camera_configurations` | per concept × shot, user-edited planner state |
| LightingConfiguration | `lighting_configurations` | per concept × shot, user-edited lighting plan |
| Storyboard | `storyboards` | panels derived from a concept (framing, arrows, notes) |
| Animatic | `animatics` | timing/transitions for a storyboard |
| ThreeDScene | `three_d_scenes` | proxy positions, camera path, lights (estimates flagged) |
| ShotList | `shot_lists` | immutable snapshots generated from a concept |
| RenderJob | `render_jobs` | queued → processing → completed / failed / cancelled |
| AuditEvent | `audit_events` | security-relevant events; IP stored as keyed hash |

## Ownership and authorisation

* Every project-owned row has `owner_id`. Data-access helpers (`db.owned_one`, `db.owned_all`) always add
  `owner_id = ?`. A row that exists but belongs to someone else returns **404**, identical to "not found",
  so IDs cannot be probed.
* Child rows are checked through their project (`project_id` + `owner_id`) before any read/write.

## Job model

* `idempotency_key` is UNIQUE (e.g. `ingest:<video_id>`, `concepts:<project_id>:<hash(controls)>`), so a
  retried request returns the existing job instead of creating a duplicate.
* Workers claim with a single atomic `UPDATE … WHERE status='queued' … RETURNING`. Heartbeats every few
  seconds; a job whose heartbeat is stale is re-queued until `max_attempts`, then failed.
* Outputs are written in one transaction that first removes earlier outputs for the same input, so a retry
  can never leave half-old/half-new results.
* Cancellation sets `cancel_requested`; workers check between stages. Project deletion cancels jobs first.

## Encrypted columns

`*_enc` = `base64(nonce ‖ AES-256-GCM(ciphertext))`, AAD = `"table:column:row_id"`, so ciphertext copied
into another row or column fails to decrypt.

## Postgres mapping (private-server mode)

TEXT ids → `uuid`, ISO strings → `timestamptz`, `INTEGER` booleans → `boolean`, JSON text → `jsonb` (only
for non-encrypted JSON such as `engine`). The SQL in `db.py` uses `?` placeholders and standard SQL; the
Postgres driver would translate to `%s`. Postgres support is **not implemented yet** (see `STATUS.md`).
