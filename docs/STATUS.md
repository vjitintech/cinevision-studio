# What is implemented vs. what needs configuration

Honest status of every capability in the brief. "Implemented" means it runs in this repository and is covered by
automated tests (`backend/tests`, `e2e/run_e2e.py`).

## Implemented and working out of the box

| Area | What you get | Notes / limits |
|---|---|---|
| Accounts | Owner registration (closes after first account), scrypt passwords, sessions, CSRF, lockout, rate limits, TOTP MFA with recovery codes, password change, sign out everywhere | |
| Projects | Private projects, director's creative controls, rename, delete (with crypto-shredding) | |
| Upload | Streaming upload with byte cap, extension + magic-byte check, ffprobe allow-list (container, codec, size, duration, frame rate), sandboxed probing | |
| Storage | AES-256-GCM per-file encryption, encrypted DB columns, Range-capable decrypting media stream, 5-minute session-bound media tokens | |
| Processing | DB job queue, separate worker, progress, retries, cancellation, stale-job recovery, idempotency | |
| Ingest | H.264 browser proxy, ~2 fps keyframes (max 120) | |
| Scene analysis | Shot cuts; camera pan/tilt/zoom/roll/handheld via optical flow; zoom-vs-dolly from parallax; subject motion vs camera motion; palette; exposure/contrast/key; colour cast; composition (thirds, symmetry, negative space, leading lines, vanishing point, roll, depth-of-field cue); indoor/outdoor and time-of-day heuristics; practical-light candidates; people (HOG); pacing/mood/purpose heuristics | Each value labelled observed / estimated / heuristic with a confidence. Verified against synthetic clips with known pan, zoom, static and cut. |
| Concepts | 8 archetypes scored against brief + footage, diverse selection (MMR), per-shot size/angle/height/direction/lens/DoF/focus/movement/speed/rig/blocking/lighting/grade/transition, production complexity & kit, continuity risks, differences from original, 3D camera paths | Deterministic and explainable; not a language model |
| Refinement | Rule-based natural-language commands (all examples from the brief + ~15 more), shot-scoped commands, structured patches, revision history, restore | Unrecognised requests are reported, never guessed |
| Camera planner | Size/angle/composition/orbit/height/lens/movement/speed/Dutch controls; live framing render; original vs planned compare; plan view; save; apply to concept | |
| Lighting studio | Draggable lights on a top-down diagram; per-source intensity/CCT/softness/elevation/direction; add/remove roles; haze; face + through-lens previews | Conceptual simulation (Lambert + wrap, no cast shadows) — labelled as such in the UI |
| Storyboard & animatic | Sketch panels rendered from each shot's camera, movement notation, editable notes, cut/dissolve, real-time animatic with scrubbing | |
| 3D previs | Orbitable proxy set with camera frustum, path, lights, shot-camera inset; drag subjects to restage (camera paths follow) | Proxy layout, not a reconstruction |
| Shot list | Snapshots including saved lighting layouts; CSV (formula-injection safe), JSON, PDF; project file export (no media) | |
| Privacy | Consent per external provider, retention settings, audit log, data-flow disclosure per adapter | |
| Deployment | Docker Compose (Caddy TLS on localhost, internal network, network-less worker), plain local runner, encrypted backups with a separate key, CI workflow | Compose file and CI were written for, but not executed in, the sandbox this release was built in |

## Needs configuration (off by default, clearly shown as such in the app)

| Capability | How to enable | Behaviour until then |
|---|---|---|
| Face detection (better shot sizing) | `CINEVISION_FACE_MODEL=/path/face_detection_yunet_2023mar.onnx` | Shot size uses body detection or saliency, with lower confidence |
| Malware scanning | `docker compose --profile scan up` and `CINEVISION_CLAMD_SOCKET` | Upload page and settings say "not configured" |
| Local LLM for free-text refinements | `--profile llm`, `CINEVISION_OLLAMA_URL`, `CINEVISION_OLLAMA_MODEL` | Only the built-in rule parser interprets commands |
| External LLM | `CINEVISION_EXTERNAL_LLM_URL/_MODEL/_KEY` **and** owner consent | Listed as not configured / needs consent; nothing is sent |

## Not implemented in this release

| Capability (brief §8 Mode D, §14) | Status |
|---|---|
| AI-generated concept stills / motion previews | Interface, consent gate and UI exist; **no provider adapter ships**, so the endpoint returns `not_configured` and nothing is generated |
| Photogrammetric / monocular-depth 3D reconstruction | Not implemented; previs uses estimated proxy positions you can restage |
| Physically based lighting with shadows | Not implemented; the lighting preview is conceptual |
| PostgreSQL backend for private-server mode | Schema is portable; driver layer not implemented — SQLite is used in both modes |
| Collaboration / sharing | Deliberately absent (private by design) |
