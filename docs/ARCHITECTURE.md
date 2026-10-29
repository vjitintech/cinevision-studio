# CineVision Studio — Architecture

CineVision Studio is a private, single-owner previsualization tool. A director uploads a short clip; the
system analyses it locally and proposes several distinct cinematography concepts, each with camera, lens,
lighting, blocking, grade, storyboard/animatic, camera-path and 3D-proxy previsualization, plus an exportable
shot list.

## 1. Guiding decisions

| Decision | Choice | Why |
|---|---|---|
| Default deployment | **Private/local mode** — runs on the owner's machine (Docker Compose or plain Python + Node) | Footage is confidential IP; nothing leaves the machine by default |
| API framework | **Starlette + Pydantic** (the ASGI core that FastAPI is built on), served by Uvicorn | Same async model and validation as FastAPI with fewer moving parts; every route is hand-authorised. Swapping to FastAPI later is mechanical (routes are plain async handlers + Pydantic models). |
| Database | **SQLite (WAL)** in local mode | Single-owner app — a separate Postgres server is an unnecessary service. All SQL is parameterised and portable; `docs/DATA_MODEL.md` notes the Postgres mapping for private-server mode. |
| Job queue | **Database-backed queue** (`render_jobs` table) + separate worker process | Avoids Redis/Celery. Atomic claim, heartbeats, retries, idempotency keys, cancellation. |
| Media processing | **FFmpeg/ffprobe** (subprocess, resource-limited, no network) + **OpenCV/NumPy** | Deterministic, offline, auditable |
| AI | **Replaceable adapters** (`backend/cinevision/adapters`). Local deterministic engines are the default; external/LLM/generative adapters are disabled until configured *and* consented | "Never present an unimplemented AI capability as functional" |
| Frontend | **React 19 + TypeScript**, bundled with **esbuild**; served by the API from the same origin | Same-origin → strict CSP, SameSite=Strict cookies, no CORS surface |
| 3D / previs | Custom TypeScript projection engine rendering to Canvas 2D (flat-shaded proxies, frusta, paths) | Works with no GPU/WebGL dependency, identical math powers storyboard, animatic, camera planner and 3D view |
| Encryption at rest | AES-256-GCM. Per-file data keys wrapped by a key-encryption key; sensitive DB columns encrypted with AAD bound to table/column/row | Crypto-shredding on delete; DB file theft reveals no titles, briefs, analysis or concepts |

## 2. Module map

```
frontend/ (React SPA)                     backend/cinevision/
  src/api.ts      — CSRF-aware client       app.py          ASGI app, middleware, static SPA
  src/previs/     — projection engine,      config.py       env settings
                    storyboard, animatic,    db.py           SQLite access, migrations, owner-scoped helpers
                    3D view, lighting sim    crypto.py       KEK/DEK, AES-GCM chunked files, field crypto, tokens
  src/pages/      — dashboard, workspace,    security.py     scrypt hashing, sessions, CSRF, TOTP, rate limiting
                    security & privacy       audit.py        audit events
                                             storage.py      encrypted blob store (path-safe)
                                             api/            route modules (auth, projects, videos, jobs, concepts…)
                                             media/          ffprobe validation, sandboxed ffmpeg, frame extraction
                                             analysis/       scene, colour, composition, subjects, motion, narrative
                                             concepts/       archetypes, generator, refinement, lighting, shot list
                                             adapters/       adapter interfaces + registry (local, ollama, external)
                                             worker.py       job runner
                                             exports.py      CSV / JSON / PDF
                                             retention.py    temp cleanup, retention, deletion
```

## 3. Runtime topology (local mode)

```
 Browser ──HTTPS──► Caddy (tls internal, localhost only)
                       │
                       ▼
                  API (uvicorn)  ── SQLite (WAL) ── encrypted blob store (/data/blobs)
                       ▲                │
                       │ (no network)   ▼
                  Worker process ◄── render_jobs  ── ffmpeg/ffprobe (rlimited) + OpenCV
```

* API and worker share only the data volume. In Compose the worker runs with `network_mode: none`.
* Telemetry, analytics and external AI are off. No component makes outbound requests unless an external
  adapter is configured **and** the owner has granted consent for that provider.

## 4. Processing pipeline

1. **Upload** (API): stream to a private temp file with a byte cap → magic-byte check → `ffprobe` in a
   sandbox (timeout, rlimits, `-protocol_whitelist file`) → container/codec/duration/resolution allow-list →
   optional ClamAV scan → encrypt with a fresh DEK → delete plaintext → enqueue `ingest`.
2. **ingest** (worker): decrypt to a job-private temp dir → H.264 720p browser proxy → keyframes
   (≈2 fps, ≤120) → encrypt each artefact → enqueue `analyze`.
3. **analyze**: shot-boundary detection, global camera motion (LK flow + RANSAC similarity model), subject
   detection (Haar faces + HOG people), colour palette (Lab k-means), exposure/contrast/key, composition
   (thirds, symmetry, spectral-residual saliency → negative space, Hough leading lines, depth bands),
   environment/time-of-day heuristics, practical-light candidates, pacing → narrative/mood heuristic.
   Every field is tagged `observed | estimated | heuristic` with a confidence.
4. **concepts** (on request, with director controls): archetype scoring + diversity selection → adaptation to
   the footage → per-shot camera/lens/DoF/movement/blocking/lighting/grade/transition + 3D camera path.
5. **Refinement**: commands ("move the camera lower") create a new concept revision; originals untouched.
6. **Derived outputs**: storyboard, animatic, camera configurations, lighting plans, 3D scene, shot list.
7. **Exports**: user-initiated CSV / JSON / PDF download — never pushed anywhere.

## 5. Implemented vs. requires configuration

See `docs/STATUS.md` — kept current with the code.
