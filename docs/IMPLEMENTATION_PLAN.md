# Phased implementation plan

Each phase ends with automated tests passing before the next starts (`backend/tests`, `e2e/`).

| Phase | Scope | Exit tests |
|---|---|---|
| 0 | Architecture, data model, API spec, threat model, this plan | review |
| 1 | Config + key management, AES-GCM file/field crypto, schema/migrations, scrypt + sessions + CSRF + TOTP + rate limits, audit log, projects CRUD, upload validation (magic bytes, ffprobe allow-list, limits), encrypted blob store, media streaming with Range + short-lived tokens, project deletion | auth, CSRF, lockout, MFA, IDOR, upload rejection cases, encryption round-trip, no plaintext on disk, deletion |
| 2 | DB job queue + worker (claim, heartbeat, retry, cancel, idempotency), ingest (proxy + keyframes), scene analysis (shots, motion, subjects, colour, composition, environment, practicals, pacing, mood) with observed/estimated tags | synthetic clips with known pan/zoom/cut/brightness; job retry/idempotency |
| 3 | Archetype library, scoring + diversity selection, footage adaptation, lens/DoF/lighting/grade/blocking/camera-path generation, refinement command parser + revisions | ≥4 concepts, pairwise distinctness, every required field present, refinements change the right fields only |
| 4 | React workspace: dashboard, project, analysis, concepts compare, camera planner, lighting studio (draggable lights + conceptual preview), storyboard + animatic player, shot list | headless browser e2e: register → upload → analysis → concepts → animatic plays |
| 5 | Interactive 3D proxy scene (move camera/subjects/lights, path playback, camera POV), adapter registry + consent gate for AI previews | 3D scene persists; AI preview returns not_configured / consent_required correctly |
| 6 | Shot-list snapshots, CSV/JSON/PDF export, project export, retention + temp cleanup, encrypted backups, Docker Compose + Caddy, CI security scan, setup docs | export content tests, full e2e, acceptance checklist |
