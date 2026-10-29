-- CineVision Studio schema (SQLite dialect; see docs/DATA_MODEL.md for the Postgres mapping)
-- Conventions:
--   * ids are random UUIDv4 strings (never sequential)
--   * every project-owned row carries owner_id and is only ever queried with owner_id = ?
--   * *_enc columns hold AES-256-GCM ciphertext (base64) bound to table/column/row via AAD
--   * timestamps are UTC ISO-8601 strings

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS users (
    id               TEXT PRIMARY KEY,
    email            TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash    TEXT NOT NULL,
    mfa_enabled      INTEGER NOT NULL DEFAULT 0,
    mfa_secret_enc   TEXT,
    recovery_codes   TEXT,               -- JSON list of scrypt hashes
    failed_logins    INTEGER NOT NULL DEFAULT 0,
    locked_until     TEXT,
    created_at       TEXT NOT NULL,
    last_login_at    TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash       TEXT PRIMARY KEY,   -- sha256 of the cookie token; raw token never stored
    user_id          TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf_token       TEXT NOT NULL,
    mfa_passed       INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL,
    last_seen_at     TEXT NOT NULL,
    expires_at       TEXT NOT NULL,
    ua_hint          TEXT                -- coarse browser family only
);
CREATE INDEX IF NOT EXISTS ix_sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS privacy_settings (
    user_id                  TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    external_ai_consent      INTEGER NOT NULL DEFAULT 0,
    consented_providers      TEXT NOT NULL DEFAULT '[]',   -- JSON list of adapter ids
    consent_updated_at       TEXT,
    temp_retention_hours     INTEGER NOT NULL DEFAULT 24,
    project_retention_days   INTEGER NOT NULL DEFAULT 0,    -- 0 = keep until deleted
    telemetry_enabled        INTEGER NOT NULL DEFAULT 0     -- always 0; there is no telemetry
);

CREATE TABLE IF NOT EXISTS projects (
    id               TEXT PRIMARY KEY,
    owner_id         TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title_enc        TEXT NOT NULL,
    brief_enc        TEXT,               -- director's creative controls + free-text brief (JSON)
    status           TEXT NOT NULL DEFAULT 'active',  -- active | deleting
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_projects_owner ON projects(owner_id);

-- Encrypted files (original video, proxy, frames, exports). Each has its own wrapped data key.
CREATE TABLE IF NOT EXISTS blobs (
    id               TEXT PRIMARY KEY,
    owner_id         TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_id       TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind             TEXT NOT NULL,      -- original | proxy | frame | export
    content_type     TEXT NOT NULL,
    wrapped_dek      TEXT NOT NULL,
    plaintext_size   INTEGER NOT NULL,
    sha256           TEXT NOT NULL,      -- of plaintext, for integrity / dedup
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_blobs_project ON blobs(project_id);

CREATE TABLE IF NOT EXISTS uploaded_videos (
    id                 TEXT PRIMARY KEY,
    owner_id           TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    project_id         TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    filename_enc       TEXT NOT NULL,
    original_blob_id   TEXT NOT NULL REFERENCES blobs(id),
    proxy_blob_id      TEXT REFERENCES blobs(id),
    status             TEXT NOT NULL,    -- validated | ingesting | ready | failed
    scan_status        TEXT NOT NULL,    -- clean | not_configured | infected
    created_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_videos_project ON uploaded_videos(project_id);

CREATE TABLE IF NOT EXISTS video_metadata (
    video_id         TEXT PRIMARY KEY REFERENCES uploaded_videos(id) ON DELETE CASCADE,
    owner_id         TEXT NOT NULL,
    container        TEXT NOT NULL,
    video_codec      TEXT NOT NULL,
    audio_codec      TEXT,
    width            INTEGER NOT NULL,
    height           INTEGER NOT NULL,
    display_aspect   REAL NOT NULL,
    fps              REAL NOT NULL,
    duration_s       REAL NOT NULL,
    bitrate          INTEGER,
    pix_fmt          TEXT,
    rotation         INTEGER NOT NULL DEFAULT 0,
    frame_count      INTEGER
);

CREATE TABLE IF NOT EXISTS extracted_frames (
    id               TEXT PRIMARY KEY,
    owner_id         TEXT NOT NULL,
    project_id       TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    video_id         TEXT NOT NULL REFERENCES uploaded_videos(id) ON DELETE CASCADE,
    blob_id          TEXT NOT NULL REFERENCES blobs(id),
    idx              INTEGER NOT NULL,
    t_s              REAL NOT NULL,
    width            INTEGER NOT NULL,
    height           INTEGER NOT NULL,
    is_keyframe      INTEGER NOT NULL DEFAULT 0,
    shot_index       INTEGER,
    UNIQUE(video_id, idx)
);

CREATE TABLE IF NOT EXISTS scene_analyses (
    id               TEXT PRIMARY KEY,
    owner_id         TEXT NOT NULL,
    project_id       TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    video_id         TEXT NOT NULL REFERENCES uploaded_videos(id) ON DELETE CASCADE,
    data_enc         TEXT NOT NULL,
    engine           TEXT NOT NULL,      -- JSON: adapter id, version, settings (no media)
    created_at       TEXT NOT NULL,
    UNIQUE(video_id)
);

CREATE TABLE IF NOT EXISTS cinematography_concepts (
    id               TEXT PRIMARY KEY,
    owner_id         TEXT NOT NULL,
    project_id       TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    analysis_id      TEXT REFERENCES scene_analyses(id) ON DELETE SET NULL,
    batch_id         TEXT NOT NULL,      -- one generation run
    lineage_id       TEXT NOT NULL,      -- groups all revisions of one concept
    parent_id        TEXT REFERENCES cinematography_concepts(id) ON DELETE SET NULL,
    revision         INTEGER NOT NULL DEFAULT 1,
    is_current       INTEGER NOT NULL DEFAULT 1,
    is_selected      INTEGER NOT NULL DEFAULT 0,
    data_enc         TEXT NOT NULL,
    engine           TEXT NOT NULL,
    change_note_enc  TEXT,
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_concepts_project ON cinematography_concepts(project_id);

CREATE TABLE IF NOT EXISTS camera_configurations (
    id               TEXT PRIMARY KEY,
    owner_id         TEXT NOT NULL,
    project_id       TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    concept_id       TEXT NOT NULL REFERENCES cinematography_concepts(id) ON DELETE CASCADE,
    shot_index       INTEGER NOT NULL,
    data_enc         TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    UNIQUE(concept_id, shot_index)
);

CREATE TABLE IF NOT EXISTS lighting_configurations (
    id               TEXT PRIMARY KEY,
    owner_id         TEXT NOT NULL,
    project_id       TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    concept_id       TEXT NOT NULL REFERENCES cinematography_concepts(id) ON DELETE CASCADE,
    shot_index       INTEGER NOT NULL,
    data_enc         TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    UNIQUE(concept_id, shot_index)
);

CREATE TABLE IF NOT EXISTS storyboards (
    id               TEXT PRIMARY KEY,
    owner_id         TEXT NOT NULL,
    project_id       TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    concept_id       TEXT NOT NULL UNIQUE REFERENCES cinematography_concepts(id) ON DELETE CASCADE,
    data_enc         TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS animatics (
    id               TEXT PRIMARY KEY,
    owner_id         TEXT NOT NULL,
    project_id       TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    storyboard_id    TEXT NOT NULL UNIQUE REFERENCES storyboards(id) ON DELETE CASCADE,
    data_enc         TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS three_d_scenes (
    id               TEXT PRIMARY KEY,
    owner_id         TEXT NOT NULL,
    project_id       TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    concept_id       TEXT NOT NULL UNIQUE REFERENCES cinematography_concepts(id) ON DELETE CASCADE,
    data_enc         TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shot_lists (
    id               TEXT PRIMARY KEY,
    owner_id         TEXT NOT NULL,
    project_id       TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    concept_id       TEXT NOT NULL REFERENCES cinematography_concepts(id) ON DELETE CASCADE,
    data_enc         TEXT NOT NULL,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS render_jobs (
    id               TEXT PRIMARY KEY,
    owner_id         TEXT NOT NULL,
    project_id       TEXT REFERENCES projects(id) ON DELETE CASCADE,
    kind             TEXT NOT NULL,      -- ingest | analyze | concepts
    status           TEXT NOT NULL,      -- queued | processing | completed | failed | cancelled
    idempotency_key  TEXT NOT NULL UNIQUE,
    params_enc       TEXT,
    progress         REAL NOT NULL DEFAULT 0,
    stage            TEXT,
    error            TEXT,               -- user-safe message only
    attempts         INTEGER NOT NULL DEFAULT 0,
    max_attempts     INTEGER NOT NULL DEFAULT 3,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    worker_id        TEXT,
    heartbeat_at     TEXT,
    result_ref       TEXT,               -- id of the produced row(s), never content
    created_at       TEXT NOT NULL,
    started_at       TEXT,
    finished_at      TEXT
);
CREATE INDEX IF NOT EXISTS ix_jobs_status ON render_jobs(status, created_at);
CREATE INDEX IF NOT EXISTS ix_jobs_project ON render_jobs(project_id);

CREATE TABLE IF NOT EXISTS audit_events (
    id               TEXT PRIMARY KEY,
    user_id          TEXT,               -- nullable for failed logins of unknown accounts
    event            TEXT NOT NULL,
    target_type      TEXT,
    target_id        TEXT,
    ip_hash          TEXT,               -- keyed hash, not the raw address
    outcome          TEXT NOT NULL,      -- success | failure | denied
    details          TEXT,               -- small JSON, never secrets/URLs/content
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_audit_user ON audit_events(user_id, created_at);
