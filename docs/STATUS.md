# What is implemented vs. what needs configuration

Honest status of every capability in the brief. "Implemented" means it runs in this repository and is covered by
automated tests (`backend/tests`, `e2e/run_e2e.py`).

## Implemented and working out of the box

| Area | What you get | Notes / limits |
|---|---|---|
| Accounts | Sign-up modes via `CINEVISION_SIGNUP`: open (email + password, default), owner_only (invite codes), closed; owner + admins; loopback-only by default with a start-up warning and Security-page banner when exposed over plain http, scrypt passwords, sessions, CSRF, lockout, rate limits, TOTP MFA with recovery codes, password change, sign out everywhere | |
| Projects | Private projects, director's creative controls, rename, delete (with crypto-shredding) | |
| Upload | Streaming upload with byte cap, extension + magic-byte check, ffprobe allow-list (container, codec, size, duration, frame rate), sandboxed probing | |
| Storage | AES-256-GCM per-file encryption, encrypted DB columns, Range-capable decrypting media stream, 5-minute session-bound media tokens | |
| Processing | DB job queue, separate worker, progress, retries, cancellation, stale-job recovery, idempotency | |
| Ingest | H.264 browser proxy, ~2 fps keyframes (max 120); odd sizes, unusual audio and damaged streams tolerated | Up to 8 GB / 4 hours by default |
| Zero-input flow | Name + upload is all a user does: analysis → concepts → Director's review run automatically | The creative brief stays optional |
| Director's review | Verdict and 8 departments (story, camera, framing, lighting, colour, sound, editing, titles), each finding = measured observation + what to do + why; 3–5 ways to edit; animated 3D animatic of every alternative concept | Rules read measured values; story/performance judgement needs local AI |
| Sound analysis | EBU R128 loudness, loudness range, true peak, clipping, silences, noise floor; speech/music/ambience reading; optional speech to text (faster-whisper, or a loopback local AI server's `/v1/audio/transcriptions`) → words per minute, dialogue buried under music, dead air, fast/slow delivery, unclear lines | Content reading is a labelled heuristic. Speech to text needs `pip install -r backend/requirements-optional.txt` or a local STT server; otherwise "not available" is shown. Transcript is stored encrypted, never logged |
| Titles & credits | Opening titles, end credits and captions detected from sampled frames; title-safe and contrast checks; words read with Tesseract OCR when installed → reading-time check per card | Without Tesseract: "text reading not available — install Tesseract to enable" |
| Faces & people | Bundled YuNet face detector (default) → shot size ECU/CU/MCU/MS/WS from face height, finds close-ups and seated people; HOG body detector with stricter filtering (NMS, pole test, face check) | `CINEVISION_FACE_MODEL` overrides the model path |
| Indoor/outdoor, day/night | Several cues scored together (sky band + gradient, horizon, stars/moon, foliage, wall/ceiling lines, windows, lamps, colour, key); "uncertain" when they disagree; night exteriors recognised | Heuristic; verified on synthetic scenes and the sample clips |
| Long films | FFmpeg scene-cut detection over every frame; frames streamed (bounded memory); detailed shots grow with running time within `CINEVISION_ANALYSIS_BUDGET_S` (default 900 s): evenly spread + longest shots, each with its own short frame burst for camera motion | A 12.5-min, 292-shot synthetic film: 179 detailed shots in ~90 s on the build machine |
| Languages | Review, analysis vocabulary, assembly reasons, lessons and all app labels in 16 languages (English, Hindi, Telugu, Tamil, Kannada, Malayalam, Bengali, Marathi, Spanish, French, German, Portuguese, Arabic, Japanese, Korean, Chinese); RTL for Arabic | Built-in text is translated; AI notes are written by the model in the chosen language. Story matching in Movie assembly understands English cue words unless local AI is on |
| Movie assembly | Up to 300 clips + optional story/screenplay → story order with reasons, trims and transitions; misfits set aside (duplicate take, different aspect/fps/resolution, too short, contradicts story); titles first, credits last | Order and suggestions only — no video is rendered. Indoor/outdoor detection is weak, which limits matching on place |
| Learn directing | Lessons per department from the same reasoning as the review, an exercise per chapter, and what to practise from your own videos | |
| MCP server | `backend/cinevision/mcp_server.py` (stdlib only): review_video, get_review, list_projects, list_concepts, refine_concept, assemble_movie | Talks to your CineVision with your account; returns text only, never footage. See docs/MCP.md |
| Scene analysis | Shot cuts; camera pan/tilt/zoom/roll/handheld via optical flow; zoom-vs-dolly from parallax; subject motion vs camera motion; palette; exposure/contrast/key; colour cast; composition (thirds, symmetry, negative space, leading lines, vanishing point, roll, depth-of-field cue); indoor/outdoor and time-of-day heuristics; practical-light candidates; people (HOG); pacing/mood/purpose heuristics | Each value labelled observed / estimated / heuristic with a confidence. Verified against synthetic clips with known pan, zoom, static and cut. |
| Concepts | 8 archetypes scored against brief + footage, diverse selection (MMR), per-shot size/angle/height/direction/lens/DoF/focus/movement/speed/rig/blocking/lighting/grade/transition, production complexity & kit, continuity risks, differences from original, 3D camera paths | Deterministic and explainable; not a language model |
| Footage verdict | After concepts are generated, compares your original shot-by-shot with each concept (size, angle, movement, lighting key, colour cast) → 0–100 match. Says **"Your footage is good"** when one matches ≥70% with no major technical problems; otherwise flags fixes or presents the concepts as alternatives. Always still lists the other options. Technical checks: blown highlights, crushed blacks, underexposure, slightly crooked horizon, unintended shake | Measures craft, not performance or story |
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
| Speech to text (dialogue checks) | `pip install -r backend/requirements-optional.txt` (faster-whisper; model `CINEVISION_WHISPER_MODEL`, default `small`, downloaded once) or a local AI server on 127.0.0.1 with `/v1/audio/transcriptions` | Review says dialogue was not checked and how to enable it |
| Reading on-screen text | Install Tesseract OCR (found on PATH, `C:\Program Files\Tesseract-OCR`, or `CINEVISION_TESSERACT`) | Text regions, title-safe and contrast still checked; words not read |
| Malware scanning | `docker compose --profile scan up` and `CINEVISION_CLAMD_SOCKET` | Upload page and settings say "not configured" |
| Free local AI (director's notes, AI editor's order) | Settings → **AI & language**: choose colibri (`http://127.0.0.1:8800/v1`), Ollama (`:11434/v1`) or LM Studio, tick *Use local AI*, Save and test. Owner account only. Hardware check recommends models (colibri: Qwen3.6-35B-A3B for text, LFM3-VL-2B or Qwen3.8-27B for vision) | Built-in rules give the full review; AI adds story, performance and style notes in the chosen language, labelled as AI-written. Frames go only to a server on this computer (others need consent) |
| Free local vision model (AI scene notes, legacy Ollama setting) | `CINEVISION_OLLAMA_URL` + `CINEVISION_OLLAMA_VISION_MODEL` (e.g. `qwen2.5vl:7b`, `llama3.2-vision`, `gemma3:12b`); with Docker use `deploy/docker-compose.vision.yml` | Adds "what's happening / lighting / mood / already working / could improve" notes from 4 keyframes and nudges concept ranking. Off by default; never identifies people |
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

## Why no free cloud video AI (e.g. Gemini free tier)
Free cloud tiers are the obvious way to get "AI that watches the video" at no cost, but the Gemini API's unpaid
terms let Google use submitted content to improve its products and let human reviewers read it. That breaks this
app's promise that footage is never used for training, so it isn't offered. A paid tier with no-training terms
could be added later as an external adapter behind the existing consent gate.

## Browser edition (GitHub Pages)
Same UI and the same concept engine (TypeScript port; tables generated from the Python source by
`scripts/gen_engine_data.py`). Analysis is a TypeScript port: shot cuts, camera pan/tilt/zoom/roll/handheld via
tracked points + RANSAC, colour/tone, saliency composition, horizon roll, depth-of-field cue, environment and mood.
**Not in the browser edition:** people/face detection (shot size falls back to a labelled guess), camera tilt,
leading lines, lens perspective, frame-rate readout, accounts, encryption at rest, malware scanning, optional AI
models. PDF export uses the browser's print dialog ("Save as PDF").
