// Browser edition "server": answers the same /api/... calls as the Python backend, using IndexedDB and in-page
// processing. The UI code is shared between editions; only api.ts decides where requests go.
import * as S from "./store";
import * as E from "./engine/concepts";
import { run as analyse, ENGINE as AN_ENGINE } from "./analysis/engine";
import { extract, LIMITS, probe, VideoError } from "./video";
import { ApiError } from "../api";
import type { RGBA } from "./analysis/img";

type A = any;
const now = () => new Date().toISOString().replace(/\.\d+Z$/, "+00:00");
const uid = () => crypto.randomUUID();
const nf = () => new ApiError(404, "not_found", "Not found.");
const DEFAULT_CONTROLS = { genre: "", scene_purpose: "", desired_emotion: "", visual_style: "", treatment: "balanced", movement_intensity: 2,
  lighting_complexity: 2, budget: "low", available_equipment: [], aspect_ratio: "16:9", frame_rate: 24, target_duration_s: null, concept_count: 4,
  preservation: "medium", intent: "practical_shoot", sensor: "super35", brief: "" };

// ---------------------------------------------------------------- object URLs for stored blobs
const urls = new Map<string, string>();
async function blobUrl(id: string) {
  if (urls.has(id)) return urls.get(id)!;
  const b = await S.get("blobs", id);
  if (!b) throw nf();
  const u = URL.createObjectURL(b.blob);
  urls.set(id, u);
  return u;
}
async function putBlob(project_id: string, kind: string, blob: Blob) {
  const id = uid();
  await S.put("blobs", { id, project_id, kind, blob, size: blob.size, created_at: now() });
  return id;
}
async function dropBlob(id: string) {
  const u = urls.get(id);
  if (u) { URL.revokeObjectURL(u); urls.delete(id); }
  await S.del("blobs", id);
}

// ---------------------------------------------------------------- jobs (run in this tab)
const running = new Map<string, { cancel: boolean }>();
const publicJob = (j: A) => ({ id: j.id, kind: j.kind, status: j.status, progress: j.progress, stage: j.stage, error: j.error, attempts: j.attempts,
  max_attempts: 1, created_at: j.created_at, started_at: j.started_at, finished_at: j.finished_at, result_ref: j.result_ref || null });

async function enqueue(project_id: string, kind: string, params: A) {
  const j = { id: uid(), project_id, kind, params, status: "queued", progress: 0, stage: null, error: null, attempts: 0, created_at: now(), started_at: null, finished_at: null };
  await S.put("jobs", j);
  setTimeout(() => runJob(j.id), 0);
  return j;
}

async function runJob(id: string) {
  const j = await S.get("jobs", id);
  if (!j || j.status !== "queued") return;
  const flag = { cancel: false };
  running.set(id, flag);
  Object.assign(j, { status: "processing", attempts: j.attempts + 1, started_at: now(), error: null });
  await S.put("jobs", j);
  let lastSave = 0;
  const progress = (p: number, stage: string) => {
    j.progress = Math.round(Math.max(0, Math.min(1, p)) * 1000) / 1000; j.stage = stage;
    if (Date.now() - lastSave > 300) { lastSave = Date.now(); S.put("jobs", { ...j }); }
  };
  try {
    const ref = await HANDLERS[j.kind](j, progress, () => flag.cancel);
    Object.assign(j, { status: "completed", progress: 1, stage: "done", finished_at: now(), result_ref: ref || null });
  } catch (e: A) {
    if (flag.cancel || e?.message === "cancelled") Object.assign(j, { status: "cancelled", stage: "cancelled", finished_at: now() });
    else Object.assign(j, { status: "failed", finished_at: now(), error: e instanceof VideoError || e instanceof ApiError ? e.message : `Processing failed: ${e?.message || e}` });
  } finally {
    running.delete(id);
    if (await S.get("projects", j.project_id)) await S.put("jobs", j);
  }
}

/** Jobs that were running when the tab closed can't continue — mark them so the user can retry. */
let recovered = false;
async function recover() {
  if (recovered) return;
  recovered = true;
  for (const j of await S.all("jobs")) if ((j.status === "processing" || j.status === "queued") && !running.has(j.id))
    await S.put("jobs", { ...j, status: "failed", error: "Stopped because the page was closed or reloaded. Retry to continue.", finished_at: now() });
}

const HANDLERS: Record<string, (j: A, p: (x: number, s: string) => void, c: () => boolean) => Promise<string | void>> = {
  async ingest(j, progress, cancelled) {
    const v = await S.get("videos", j.params.video_id);
    if (!v) throw new ApiError(404, "gone", "The clip no longer exists.");
    const orig = await S.get("blobs", v.original_blob_id);
    progress(0.02, "Decoding frames in your browser");
    const { samples, keyframes } = await extract(orig.blob, v.metadata.duration_s, (f) => progress(0.02 + 0.9 * f, "Decoding frames in your browser"), cancelled);
    for (const f of await S.where("frames", (f: A) => f.video_id === v.id)) { await dropBlob(f.blob_id); await S.del("frames", f.id); }
    for (const [i, k] of keyframes.entries()) {
      const bid = await putBlob(v.project_id, "frame", k.jpeg);
      await S.put("frames", { id: uid(), project_id: v.project_id, video_id: v.id, blob_id: bid, idx: i, t_s: +k.t.toFixed(3), width: k.img.w,
        height: k.img.h, is_keyframe: 0, shot_index: null });
    }
    cache.set(v.id, { samples, keyframes: keyframes.map((k) => ({ t: k.t, img: k.img })) });
    v.status = "ready";
    await S.put("videos", v);
    await enqueue(v.project_id, "analyze", { video_id: v.id });
  },
  async analyze(j, progress, cancelled) {
    const v = await S.get("videos", j.params.video_id);
    if (!v) throw new ApiError(404, "gone", "The clip no longer exists.");
    let data = cache.get(v.id);
    if (!data) {
      const orig = await S.get("blobs", v.original_blob_id);
      const ex = await extract(orig.blob, v.metadata.duration_s, (f) => progress(0.3 * f, "Decoding frames in your browser"), cancelled);
      data = { samples: ex.samples, keyframes: ex.keyframes.map((k) => ({ t: k.t, img: k.img })) };
    }
    const result = await analyse(data.samples, data.keyframes, v.metadata, (p, s) => progress(0.3 + 0.68 * p, s), cancelled);
    cache.delete(v.id);
    const old = await S.where("analyses", (a: A) => a.video_id === v.id);
    for (const a of old) await S.del("analyses", a.id);
    const id = uid();
    await S.put("analyses", { id, project_id: v.project_id, video_id: v.id, data: result, engine: AN_ENGINE, created_at: now() });
    const frames = await S.where("frames", (f: A) => f.video_id === v.id);
    for (const f of frames) {
      const sh = result.shots.find((s: A) => f.t_s >= s.start_s && (f.t_s < s.end_s || s === result.shots[result.shots.length - 1]));
      f.shot_index = sh ? sh.index : null;
      f.is_keyframe = 0;
    }
    for (const s of result.shots) {
      const best = frames.reduce((b: A, f: A) => (!b || Math.abs(f.t_s - s.representative_t) < Math.abs(b.t_s - s.representative_t) ? f : b), null);
      if (best) best.is_keyframe = 1;
    }
    for (const f of frames) await S.put("frames", f);
    await touch(v.project_id);
    return id;
  },
  async concepts(j, progress) {
    const a = await analysisFor(j.project_id);
    if (!a) throw new ApiError(409, "analysis_not_ready", "Analyse the clip before generating concepts.");
    progress(0.3, "Scoring creative approaches");
    const list = E.generate(a.data, j.params.controls);
    const batch = uid();
    for (const [i, con] of list.entries()) {
      const id = uid();
      const settings = Object.fromEntries(["movement_intensity", "lighting_complexity", "budget", "preservation", "treatment", "sensor", "concept_count",
        "intent", "aspect_ratio", "frame_rate"].map((k) => [k, j.params.controls[k]]));
      await S.put("concepts", { id, project_id: j.project_id, analysis_id: a.id, batch_id: batch, lineage_id: id, parent_id: null, revision: 1,
        is_current: 1, is_selected: 0, seq: Date.now() * 10 + i, data: con, change_note: null, created_at: now(),
        engine: { ...E.ENGINE, archetype: con.archetype, score: con.score, settings } });
    }
    await touch(j.project_id);
    return batch;
  },
};
const cache = new Map<string, { samples: { t: number; img: RGBA }[]; keyframes: { t: number; img: RGBA }[] }>();

// ---------------------------------------------------------------- helpers
async function project(pid: string) { const p = await S.get("projects", pid); if (!p) throw nf(); return p; }
async function touch(pid: string) { const p = await S.get("projects", pid); if (p) { p.updated_at = now(); await S.put("projects", p); } }
async function analysisFor(pid: string) { return (await S.where("analyses", (a: A) => a.project_id === pid))[0] || null; }
async function concept(cid: string) { const c = await S.get("concepts", cid); if (!c) throw nf(); return c; }
const conceptOut = (c: A) => ({ id: c.id, lineage_id: c.lineage_id, parent_id: c.parent_id, revision: c.revision, is_current: !!c.is_current,
  is_selected: !!c.is_selected, batch_id: c.batch_id, created_at: c.created_at, engine: c.engine, change_note: c.change_note, data: c.data });

async function detail(pid: string) {
  const p = await project(pid);
  const v = (await S.where("videos", (x: A) => x.project_id === pid))[0];
  const jobs = (await S.where("jobs", (j: A) => j.project_id === pid)).sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 20);
  const a = await analysisFor(pid);
  const cc = (await S.where("concepts", (c: A) => c.project_id === pid && c.is_current)).length;
  return { id: p.id, title: p.title, brief: p.brief, created_at: p.created_at, updated_at: p.updated_at,
    video: v ? { id: v.id, status: v.status, scan_status: "not_available_in_browser", filename: v.filename, original_blob_id: v.original_blob_id,
      proxy_blob_id: v.original_blob_id, metadata: v.metadata } : null,
    jobs: jobs.map(publicJob), analysis_id: a?.id || null, concept_count: cc };
}

async function latestBatch(pid: string) {
  const cs = await S.where("concepts", (c: A) => c.project_id === pid && c.revision === 1);
  return cs.sort((a, b) => b.seq - a.seq)[0]?.batch_id || null;
}

async function currentConcepts(pid: string, allBatches = false) {
  let rows = await S.where("concepts", (c: A) => c.project_id === pid && c.is_current);
  if (!allBatches && rows.length) { const lb = await latestBatch(pid); rows = rows.filter((r) => r.batch_id === lb || r.is_selected); }
  return rows.sort((a, b) => (b.engine.score ?? -1e9) - (a.engine.score ?? -1e9));
}

async function newRevision(row: A, data: A, note: string, extraEngine: A = {}) {
  const lineage = await S.where("concepts", (c: A) => c.lineage_id === row.lineage_id);
  const head = lineage.find((c) => c.is_current) || row;
  for (const c of lineage) if (c.is_current) await S.put("concepts", { ...c, is_current: 0 });
  const id = uid();
  const nc = { id, project_id: row.project_id, analysis_id: row.analysis_id, batch_id: row.batch_id, lineage_id: row.lineage_id, parent_id: head.id,
    revision: Math.max(...lineage.map((c) => c.revision)) + 1, is_current: 1, is_selected: head.is_selected, seq: row.seq, data, change_note: note,
    created_at: now(), engine: { ...row.engine, ...extraEngine } };
  await S.put("concepts", nc);
  for (const pl of await S.where("planners", (x: A) => x.concept_id === head.id)) await S.put("planners", { ...pl, id: pl.id.replace(head.id, id), concept_id: id });
  return nc;
}

async function deleteProject(pid: string) {
  for (const r of running.values()) r.cancel = true;
  for (const s of ["frames", "videos", "analyses", "concepts", "planners", "shotlists", "jobs"] as const)
    for (const x of await S.where(s, (y: A) => y.project_id === pid)) await S.del(s, x.id);
  for (const b of await S.where("blobs", (y: A) => y.project_id === pid)) await dropBlob(b.id);
  await S.del("projects", pid);
}

// ---------------------------------------------------------------- routes
type H = (m: RegExpMatchArray, body: A, q: URLSearchParams) => Promise<A>;
const routes: [string, RegExp, H][] = [];
const r = (method: string, path: string, h: H) => routes.push([method, new RegExp("^" + path.replace(/\{(\w+)\}/g, "([^/]+)") + "$"), h]);

r("GET", "/api/auth/session", async () => ({ authenticated: true, mfa_required: false, registration_open: false, mode: "browser", csrf_token: "browser",
  user: { id: "browser", email: "this browser", mfa_enabled: false } }));
r("POST", "/api/auth/logout", async () => ({ ok: true }));
r("GET", "/api/health", async () => ({ status: "ok" }));
const ADAPTERS = [
  ["browser-decode", "ingest", "Video ingestion & transcoding", "Browser video decoder", "Frames are decoded by your browser; the file never leaves this device."],
  ["browser-scene", "scene_analysis", "Scene & frame analysis", "In-browser scene analysis", "Colour, exposure, composition, environment heuristics."],
  ["browser-motion", "motion_tracking", "Subject & motion tracking", "In-browser motion tracking", "Camera motion from tracked points. People detection isn't available in this edition."],
  ["rules-cinematography", "cinematography", "Cinematography recommendation", "Archetype concept engine", "Same deterministic engine as the self-hosted edition."],
  ["procedural-storyboard", "storyboard", "Storyboard generation", "Procedural storyboard & animatic", "Panels drawn from the 3D proxy projection."],
  ["proxy-previs", "scene3d", "3D scene & camera-path generation", "Proxy 3D previs", "Proxy layout, not a reconstruction."],
  ["report-shotlist", "reports", "Report & shot-list generation", "Shot list & CSV/JSON/print export", "Files are created in your browser."],
].map(([id, service, service_label, name, note]) => ({ id, service, service_label, name, version: "1", external: false, status: "available",
  data_flow: "Runs in this browser tab. No data is sent anywhere.", note }));
const UNAVAILABLE = [["vision", "scene_analysis", "Scene & frame analysis", "Local vision model (Ollama)"], ["image-gen", "image_generation", "Image generation", "AI concept stills"],
  ["video-gen", "video_generation", "Video generation", "AI concept motion preview"]].map(([id, service, service_label, name]) => ({ id, service, service_label, name,
  version: "0", external: false, status: "not_configured", data_flow: "Not available in the browser edition.", note: "Use the self-hosted edition to add optional AI models." }));
r("GET", "/api/system/capabilities", async () => ({ mode: "browser", limits: { ...LIMITS, codecs: ["whatever your browser can play (MP4/H.264 and WebM are safest)"] },
  malware_scanning: "not_configured", adapters: [...ADAPTERS, ...UNAVAILABLE], telemetry: "none", external_calls_default: "none" }));
r("GET", "/api/adapters", async () => ({ adapters: [...ADAPTERS, ...UNAVAILABLE] }));

r("GET", "/api/projects", async () => {
  await recover();
  const ps = (await S.all("projects")).sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  return { projects: await Promise.all(ps.map(async (p) => {
    const v = (await S.where("videos", (x: A) => x.project_id === p.id))[0];
    return { id: p.id, title: p.title, brief: p.brief, created_at: p.created_at, updated_at: p.updated_at, video_status: v?.status || null,
      concept_count: (await S.where("concepts", (c: A) => c.project_id === p.id && c.is_current)).length };
  })) };
});
r("POST", "/api/projects", async (_m, b) => {
  const title = String(b?.title || "").trim().slice(0, 160);
  if (!title) throw new ApiError(422, "validation_error", "title: required");
  const p = { id: uid(), title, brief: { ...DEFAULT_CONTROLS, ...(b.brief || {}) }, created_at: now(), updated_at: now() };
  await S.put("projects", p);
  return detail(p.id);
});
r("GET", "/api/projects/{pid}", async (m) => { await recover(); return detail(m[1]); });
r("PATCH", "/api/projects/{pid}", async (m, b) => {
  const p = await project(m[1]);
  if (b.title) p.title = String(b.title).trim().slice(0, 160);
  if (b.brief) p.brief = { ...DEFAULT_CONTROLS, ...b.brief };
  p.updated_at = now();
  await S.put("projects", p);
  return detail(p.id);
});
r("DELETE", "/api/projects/{pid}", async (m) => { await project(m[1]); await deleteProject(m[1]); });

r("DELETE", "/api/projects/{pid}/video", async (m) => {
  const pid = m[1];
  for (const s of ["frames", "videos", "analyses", "concepts", "planners", "shotlists", "jobs"] as const)
    for (const x of await S.where(s, (y: A) => y.project_id === pid)) await S.del(s, x.id);
  for (const b of await S.where("blobs", (y: A) => y.project_id === pid)) await dropBlob(b.id);
});
r("POST", "/api/projects/{pid}/media-token", async (m, b) => {
  const blob = await S.get("blobs", b.blob_id);
  if (!blob || blob.project_id !== m[1]) throw nf();
  return { url: await blobUrl(b.blob_id), expires_in: 3600 };
});
r("GET", "/api/projects/{pid}/frames", async (m) => {
  await project(m[1]);
  const fs = (await S.where("frames", (f: A) => f.project_id === m[1])).sort((a, b) => a.idx - b.idx);
  return { frames: await Promise.all(fs.map(async (f) => ({ ...f, url: await blobUrl(f.blob_id) }))), expires_in: 3600 };
});
r("GET", "/api/projects/{pid}/jobs", async (m) => ({ jobs: (await S.where("jobs", (j: A) => j.project_id === m[1]))
  .sort((a, b) => b.created_at.localeCompare(a.created_at)).map(publicJob) }));
r("POST", "/api/jobs/{jid}/retry", async (m) => {
  const j = await S.get("jobs", m[1]);
  if (!j || !["failed", "cancelled"].includes(j.status)) throw new ApiError(409, "not_retryable", "Only failed or cancelled jobs can be retried.");
  Object.assign(j, { status: "queued", error: null, progress: 0, stage: null, finished_at: null });
  await S.put("jobs", j);
  setTimeout(() => runJob(j.id), 0);
  return { ok: true };
});
r("POST", "/api/jobs/{jid}/cancel", async (m) => {
  const j = await S.get("jobs", m[1]);
  if (!j || !["queued", "processing"].includes(j.status)) throw new ApiError(409, "not_cancellable", "This job is not running.");
  const f = running.get(j.id);
  if (f) f.cancel = true; else await S.put("jobs", { ...j, status: "cancelled", finished_at: now() });
  return { ok: true };
});

r("GET", "/api/projects/{pid}/analysis", async (m) => {
  await project(m[1]);
  const a = await analysisFor(m[1]);
  if (!a) throw new ApiError(404, "analysis_not_ready", "No analysis yet. It appears here once processing completes.");
  return { id: a.id, created_at: a.created_at, engine: a.engine, data: a.data };
});
r("POST", "/api/projects/{pid}/analysis/rerun", async (m) => {
  const v = (await S.where("videos", (x: A) => x.project_id === m[1]))[0];
  if (!v || v.status !== "ready") throw new ApiError(409, "video_not_ready", "Upload a clip and wait for preparation to finish first.");
  return { job: publicJob(await enqueue(m[1], "analyze", { video_id: v.id })) };
});

r("POST", "/api/projects/{pid}/concepts/generate", async (m, b) => {
  const p = await project(m[1]);
  if (!(await analysisFor(m[1]))) throw new ApiError(409, "analysis_not_ready", "Concepts need a finished scene analysis first.");
  const controls = { ...DEFAULT_CONTROLS, ...(b?.controls || p.brief) };
  if (b?.controls) { p.brief = controls; await S.put("projects", p); }
  return { job: publicJob(await enqueue(m[1], "concepts", { controls })) };
});
r("GET", "/api/projects/{pid}/concepts", async (m, _b, q) => ({ concepts: (await currentConcepts(m[1], q.get("all") === "1")).map(conceptOut) }));
r("GET", "/api/concepts/{cid}", async (m) => conceptOut(await concept(m[1])));
r("GET", "/api/concepts/{cid}/history", async (m) => {
  const c = await concept(m[1]);
  return { revisions: (await S.where("concepts", (x: A) => x.lineage_id === c.lineage_id)).sort((a, b) => a.revision - b.revision).map(conceptOut) };
});
r("POST", "/api/concepts/{cid}/refine", async (m, b) => {
  const c = await concept(m[1]);
  if (!c.is_current) throw new ApiError(409, "not_current", "Refine the latest revision of this concept.");
  if (!b?.command && !b?.patches?.length) throw new ApiError(422, "empty_refinement", "Describe a change or send patches.");
  const a = await analysisFor(c.project_id);
  let data = c.data, applied: string[] = [], unknown: string[] = [];
  try {
    if (b.patches?.length) { const res = E.applyPatch(data, b.patches, a?.data || null); data = res.concept; applied = res.applied; }
    if (b.command) { const res = E.applyCommand(data, String(b.command).slice(0, 1000), a?.data || null); data = res.concept; applied.push(...res.applied); unknown = res.unknown; }
  } catch (e) { if (e instanceof E.PatchError) throw new ApiError(422, "invalid_patch", e.message); throw e; }
  if (!applied.length) return { concept: conceptOut(c), applied: [], unrecognized: unknown,
    help: "Try: 'make the lighting more dramatic', 'move the camera lower', 'slower dolly-in', 'more background separation', 'keep the blocking but change the angle', 'three alternative endings', 'small budget', 'shot 2 tighter', 'warmer', 'add haze'." };
  const nc = await newRevision(c, data, applied.join(" "), { refined_with: "rule-parser" });
  return { concept: conceptOut(nc), applied, unrecognized: unknown };
});
r("POST", "/api/concepts/{cid}/revert", async (m) => {
  const c = await concept(m[1]);
  if (c.is_current) return { concept: conceptOut(c) };
  return { concept: conceptOut(await newRevision(c, c.data, `Reverted to revision ${c.revision}`)) };
});
r("POST", "/api/concepts/{cid}/select", async (m) => {
  const c = await concept(m[1]);
  for (const x of await S.where("concepts", (y: A) => y.project_id === c.project_id)) await S.put("concepts", { ...x, is_selected: x.lineage_id === c.lineage_id ? 1 : 0 });
  return { ok: true };
});

const shotOf = (c: A, idx: number) => { const s = c.data.shots.find((x: A) => x.index === idx); if (!s) throw nf(); return s; };
const defaults: Record<string, (c: A, s: A) => A> = {
  camera: (_c, s) => ({ size: s.size, angle: s.angle, composition: s.composition, camera_height_m: s.camera_height_m, azimuth_deg: s.azimuth_deg,
    distance_m: s.distance_m, dutch_roll_deg: s.dutch_roll_deg || 0, lens_ff_mm: s.lens.ff_equiv_mm, movement: s.movement.type, speed_m_s: s.movement.speed_m_s,
    focus: s.dof.focus, camera_path: s.camera_path, derived: true }),
  lighting: (_c, s) => ({ ...s.lighting, derived: true }),
};
for (const kind of ["camera", "lighting"]) {
  r("GET", `/api/concepts/{cid}/${kind}/{shot}`, async (m) => {
    const c = await concept(m[1]), s = shotOf(c, +m[2]);
    const row = await S.get("planners", `${kind}:${c.id}:${s.index}`);
    return row ? { ...row.data, derived: false } : defaults[kind](c, s);
  });
  r("PUT", `/api/concepts/{cid}/${kind}/{shot}`, async (m, b) => {
    const c = await concept(m[1]), s = shotOf(c, +m[2]);
    if (!b || typeof b !== "object") throw new ApiError(422, "invalid_state", "Expected an object.");
    const data = { ...b }; delete data.derived;
    await S.put("planners", { id: `${kind}:${c.id}:${s.index}`, project_id: c.project_id, concept_id: c.id, kind, shot: s.index, data });
    return { ...data, derived: false };
  });
  r("DELETE", `/api/concepts/{cid}/${kind}/{shot}`, async (m) => {
    const c = await concept(m[1]), s = shotOf(c, +m[2]);
    await S.del("planners", `${kind}:${c.id}:${s.index}`);
    return defaults[kind](c, s);
  });
}
r("GET", "/api/concepts/{cid}/storyboard", async (m) => {
  const c = await concept(m[1]);
  const row = await S.get("planners", `storyboard:${c.id}`);
  if (row) return { ...row.data, derived: false };
  return { panels: c.data.shots.map((s: A) => ({ shot: s.index, duration_s: s.duration_s, title: `${s.size[0].toUpperCase()}${s.size.slice(1)} · ${s.angle}`,
    note: `${s.purpose[0].toUpperCase()}${s.purpose.slice(1)}. ${s.movement.type[0].toUpperCase()}${s.movement.type.slice(1)}.`, transition: s.transition_out,
    transition_s: s.transition_out.includes("dissolve") ? 0.6 : 0 })), animatic: { playback_rate: 1, show_paths: true }, derived: true };
});
r("PUT", "/api/concepts/{cid}/storyboard", async (m, b) => {
  const c = await concept(m[1]);
  if (!Array.isArray(b?.panels)) throw new ApiError(422, "invalid_state", "panels must be a list.");
  const data = { ...b }; delete data.derived;
  await S.put("planners", { id: `storyboard:${c.id}`, project_id: c.project_id, concept_id: c.id, kind: "storyboard", data });
  return { ...data, derived: false };
});
r("GET", "/api/concepts/{cid}/scene3d", async (m) => {
  const c = await concept(m[1]);
  const row = await S.get("planners", `scene3d:${c.id}`);
  return row ? { ...row.data, derived: false } : { scene: c.data.scene, overrides: {}, derived: true };
});
r("PUT", "/api/concepts/{cid}/scene3d", async (m, b) => {
  const c = await concept(m[1]);
  const data = { ...b }; delete data.derived;
  await S.put("planners", { id: `scene3d:${c.id}`, project_id: c.project_id, concept_id: c.id, kind: "scene3d", data });
  return { ...data, derived: false };
});
r("POST", "/api/concepts/{cid}/shotlist", async (m) => {
  const c = await concept(m[1]);
  const overrides: Record<number, A> = {};
  for (const p of await S.where("planners", (x: A) => x.concept_id === c.id && x.kind === "lighting"))
    if (Array.isArray(p.data.sources) && p.data.setup) overrides[p.shot] = p.data;
  const data = { ...E.buildShotList(c.data, overrides), concept_revision: c.revision };
  const id = uid();
  await S.put("shotlists", { id, project_id: c.project_id, concept_id: c.id, lineage_id: c.lineage_id, data, created_at: now() });
  return { id, created_at: now(), data };
});
r("GET", "/api/concepts/{cid}/shotlists", async (m) => {
  const c = await concept(m[1]);
  return { shotlists: (await S.where("shotlists", (s: A) => s.lineage_id === c.lineage_id)).sort((a, b) => b.created_at.localeCompare(a.created_at))
    .map((s) => ({ id: s.id, created_at: s.created_at, data: s.data })) };
});
r("POST", "/api/concepts/{cid}/ai-preview", async () => {
  throw new ApiError(409, "not_configured", "AI concept previews aren't available in the browser edition, so nothing was generated and no data left your device.");
});
r("GET", "/api/projects/{pid}/assessment", async (m) => {
  const p = await project(m[1]);
  const a = await analysisFor(m[1]);
  if (!a) throw new ApiError(404, "analysis_not_ready", "No analysis yet.");
  return E.assess(a.data, (await currentConcepts(m[1])).map(conceptOut), p.brief);
});

// privacy page in browser edition
r("GET", "/api/privacy", async () => ({ external_ai_consent: false, consented_providers: [], temp_retention_hours: 0, project_retention_days: 0,
  telemetry_enabled: false, training_use: "never" }));

// ---------------------------------------------------------------- entry points used by api.ts
export async function handle(method: string, url: string, body: A) {
  const u = new URL(url, location.href);
  for (const [mth, re, h] of routes) {
    if (mth !== method) continue;
    const m = u.pathname.match(re);
    if (m) return h(m, body, u.searchParams);
  }
  throw new ApiError(404, "not_found", "Not available in the browser edition.");
}

export async function upload(pid: string, file: File, onProgress: (f: number) => void) {
  await project(pid);
  if ((await S.where("videos", (v: A) => v.project_id === pid)).length) throw new ApiError(409, "video_exists", "This project already has a clip. Remove it first to replace it.");
  onProgress(0.2);
  let meta;
  try { meta = await probe(file); } catch (e) { if (e instanceof VideoError) throw new ApiError(422, e.code, e.message); throw e; }
  onProgress(0.6);
  let bid;
  try { bid = await putBlob(pid, "original", file); }
  catch { throw new ApiError(507, "storage_full", "Your browser refused to store this clip — it may be out of storage space. Try a shorter or smaller clip."); }
  const vid = uid();
  await S.put("videos", { id: vid, project_id: pid, filename: file.name, original_blob_id: bid, status: "validated", metadata: meta, created_at: now() });
  await touch(pid);
  onProgress(1);
  const job = await enqueue(pid, "ingest", { video_id: vid });
  return { video_id: vid, metadata: meta, scan_status: "not_available_in_browser", job: publicJob(job) };
}

export async function download(path: string, body: A, fallback: string) {
  const save = (blob: Blob, name: string) => {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = name; document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  };
  const safe = (s: string, ext: string) => (s.replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^[-.]+|[-.]+$/g, "").slice(0, 60) || "shot-list") + "." + ext;
  let m = path.match(/^\/api\/shotlists\/([^/]+)\/export$/);
  if (m) {
    const sl = await S.get("shotlists", m[1]);
    if (!sl) throw nf();
    const p = await project(sl.project_id);
    const { shotlistCsv, shotlistJson, printShotlist } = await import("./exports");
    const name = safe(`${p.title}-${sl.data.concept_name}-shot-list`, body.format);
    if (body.format === "csv") return save(new Blob([shotlistCsv(sl.data)], { type: "text/csv" }), name);
    if (body.format === "json") return save(new Blob([shotlistJson(sl.data, p.title)], { type: "application/json" }), name);
    return printShotlist(sl.data, p.title);
  }
  m = path.match(/^\/api\/projects\/([^/]+)\/export$/);
  if (m) {
    const pid = m[1], p = await project(pid), a = await analysisFor(pid);
    const concepts = await Promise.all((await S.where("concepts", (c: A) => c.project_id === pid && c.is_current)).map(async (c) => {
      const pl = await S.where("planners", (x: A) => x.concept_id === c.id);
      return { id: c.id, revision: c.revision, selected: !!c.is_selected, engine: c.engine, concept: c.data,
        camera_plans: Object.fromEntries(pl.filter((x) => x.kind === "camera").map((x) => [String(x.shot), x.data])),
        lighting_plans: Object.fromEntries(pl.filter((x) => x.kind === "lighting").map((x) => [String(x.shot), x.data])),
        scene3d: pl.find((x) => x.kind === "scene3d")?.data || null };
    }));
    const out = { format: "cinevision.project/1", edition: "browser", exported_at: now(), title: p.title, brief: p.brief, analysis: a?.data || null, concepts,
      shot_lists: (await S.where("shotlists", (s: A) => s.project_id === pid)).map((s) => s.data), media_included: false };
    return save(new Blob([JSON.stringify(out, null, 2)], { type: "application/json" }), safe(p.title + ".cinevision", "json"));
  }
  throw new ApiError(404, "not_found", fallback);
}

export async function eraseEverything() {
  for (const r2 of running.values()) r2.cancel = true;
  for (const u of urls.values()) URL.revokeObjectURL(u);
  urls.clear();
  await S.wipe();
}
