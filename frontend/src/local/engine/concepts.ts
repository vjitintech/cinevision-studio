// TypeScript port of backend/cinevision/concepts/{geometry,lighting,builder,refine,shotlist,assessment}.py
// for the browser edition. Keep behaviour in step with the Python engine; shared tables live in ./data.ts (generated).
/* eslint-disable @typescript-eslint/no-explicit-any */
import { ANGLES, ARCHETYPES, BUDGET_ORDER, FIXTURES, FRAMING_HEIGHT_M, RIGS, SENSORS, SHOT_SIZES, SUBJECT_REF_HEIGHT } from "./data";

type A = any;
export const ENGINE = { id: "rules-cinematography", version: "1.0", edition: "browser" };

// ---------------------------------------------------------------- small helpers
const v = (d: A, dflt: A = null) => (d && typeof d === "object" && "value" in d ? d.value ?? dflt : dflt);
const r1 = (x: number, n = 0) => { const p = 10 ** n; return Math.round(x * p) / p; };
const deg = (r: number) => (r * 180) / Math.PI;
const rad = (d: number) => (d * Math.PI) / 180;
const copysign = (m: number, s: number) => (s < 0 || Object.is(s, -0) ? -Math.abs(m) : Math.abs(m));
const g = (x: number) => String(Number(x.toPrecision(6)));   // python '%g'-ish
const clone = <T>(o: T): T => JSON.parse(JSON.stringify(o));

// ---------------------------------------------------------------- geometry
const fovDeg = (focal: number, sensor: string): [number, number] => {
  const s = SENSORS[sensor];
  return [deg(2 * Math.atan(s.w / (2 * focal))), deg(2 * Math.atan(s.h / (2 * focal)))];
};
export const toSensorFocal = (ff: number, sensor: string) => ff / SENSORS[sensor].crop;
export const lensCategory = (ff: number) => (ff < 20 ? "ultra-wide" : ff < 35 ? "wide" : ff < 60 ? "standard" : ff < 110 ? "short telephoto" : "telephoto");
export function perspectiveNote(ff: number) {
  if (ff < 24) return "Exaggerated depth and size differences; close camera feels immersive, edges stretch.";
  if (ff < 35) return "Expansive space with natural-looking distortion when kept at subject distance.";
  if (ff < 60) return "Close to natural human perspective; neutral and honest.";
  if (ff < 110) return "Flattering compression of faces; background starts to fall away.";
  return "Strong compression stacks foreground and background; observational distance.";
}
export function distanceFor(size: string, ff: number, sensor: string) {
  const [, vfov] = fovDeg(toSensorFocal(ff, sensor), sensor);
  return r1(FRAMING_HEIGHT_M[size] / (2 * Math.tan(rad(vfov) / 2)), 2);
}
export function stepSize(size: string, steps: number) {
  const i = SHOT_SIZES.includes(size) ? SHOT_SIZES.indexOf(size) : 3;
  return SHOT_SIZES[Math.max(0, Math.min(SHOT_SIZES.length - 1, i + steps))];
}
export function normaliseSize(label: string | null | undefined) {
  if (!label) return "medium";
  const l = label.toLowerCase();
  const map: [string, string][] = [["extreme close", "extreme close-up"], ["medium close", "medium close-up"], ["close", "close-up"],
    ["extreme wide", "extreme wide"], ["cowboy", "medium wide"], ["medium-wide", "medium wide"], ["medium wide", "medium wide"],
    ["full", "full"], ["establishing", "wide"], ["wide", "wide"], ["medium", "medium"]];
  for (const [k, val] of map) if (l.includes(k)) return val;
  return "medium";
}
export function orbitPosition(t: number[], az: number, distance: number, height: number, facing = 180) {
  const a = rad(facing + az);
  const dy = height - t[1];
  const horiz = distance > Math.abs(dy) ? Math.sqrt(Math.max(0.01, distance ** 2 - dy ** 2)) : distance;
  return [r1(t[0] + horiz * Math.sin(a), 3), r1(height, 3), r1(t[2] + horiz * Math.cos(a), 3)];
}

// ---------------------------------------------------------------- colour
function rgbToHls(r: number, gg: number, b: number): [number, number, number] {
  const mx = Math.max(r, gg, b), mn = Math.min(r, gg, b), l = (mx + mn) / 2;
  if (mx === mn) return [0, l, 0];
  const d = mx - mn;
  const s = l <= 0.5 ? d / (mx + mn) : d / (2 - mx - mn);
  const rc = (mx - r) / d, gc = (mx - gg) / d, bc = (mx - b) / d;
  let h = r === mx ? bc - gc : gg === mx ? 2 + rc - bc : 4 + gc - rc;
  h = ((h / 6) % 1 + 1) % 1;
  return [h, l, s];
}
function hlsToRgb(h: number, l: number, s: number): [number, number, number] {
  if (s === 0) return [l, l, l];
  const m2 = l <= 0.5 ? l * (1 + s) : l + s - l * s, m1 = 2 * l - m2;
  const vv = (hue: number) => {
    hue = ((hue % 1) + 1) % 1;
    if (hue < 1 / 6) return m1 + (m2 - m1) * hue * 6;
    if (hue < 0.5) return m2;
    if (hue < 2 / 3) return m1 + (m2 - m1) * (2 / 3 - hue) * 6;
    return m1;
  };
  return [vv(h + 1 / 3), vv(h), vv(h - 1 / 3)];
}
export function hexShift(hex: string, sat: number, light: number, hueShift: number) {
  const [r, gg, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
  let [h, l, s] = rgbToHls(r, gg, b);
  h = (((h + hueShift / 360) % 1) + 1) % 1;
  l = Math.max(0.03, Math.min(0.97, l * light));
  s = Math.max(0, Math.min(1, s * sat));
  const out = hlsToRgb(h, l, s).map((x) => Math.floor(x * 255).toString(16).padStart(2, "0"));
  return "#" + out.join("");
}

// ---------------------------------------------------------------- lighting
function cctFor(env: string, tod: string, role: string, style: string) {
  if (role === "practical") return 2700;
  if (style.includes("moon") || tod.includes("night")) return role === "key" || role === "back" ? 4300 : 3200;
  if (env === "outdoor" && tod.includes("day")) return 5600;
  if (tod.includes("dusk") || tod.includes("dawn")) return role !== "back" ? 3400 : 3000;
  return role === "fill" ? 4300 : 3800;
}

export function buildLighting(arch: A, o: { env: string; tod: string; practicals: A[]; budget: string; complexity: number; keySide: number; shift?: number }) {
  const L = arch.light, fx = FIXTURES[o.budget], style: string = L.style, shift = o.shift || 0;
  const outdoorDay = o.env === "outdoor" && o.tod.includes("day");
  const sources: A[] = [];
  let keyFixture = outdoorDay ? fx.sun : L.softness < 0.5 ? fx.key_hard : fx.key_soft;
  if (!outdoorDay && o.practicals.length && !style.includes("practical") && o.complexity <= 1) keyFixture = fx.window;
  sources.push({ role: "key", fixture: keyFixture, azimuth_deg: L.key_az * o.keySide, elevation_deg: L.key_el,
    distance_m: outdoorDay ? 150 : 2.2, intensity: 1, cct_k: cctFor(o.env, o.tod, "key", style) + shift, softness: r1(L.softness, 2),
    motivation: outdoorDay ? "sun" : o.practicals.length || style.includes("motivated") ? "window / existing source" : "off-screen source" });
  if (L.negative_fill) sources.push({ role: "negative_fill", fixture: fx.neg, azimuth_deg: -60 * o.keySide, elevation_deg: 0, distance_m: 1,
    intensity: 0, cct_k: null, softness: null, motivation: "removes ambient bounce on the shadow side" });
  if (L.ratio < 12 && o.complexity >= 1) sources.push({ role: "fill", fixture: fx.fill, azimuth_deg: -35 * o.keySide, elevation_deg: 10,
    distance_m: 2, intensity: r1(1 / L.ratio, 3), cct_k: cctFor(o.env, o.tod, "fill", style) + shift, softness: 0.95, motivation: "bounce from the room" });
  if (L.back > 0.25 && o.complexity >= 1) {
    const role = L.back > 0.7 ? "backlight" : "rim";
    const fixture = outdoorDay && style.includes("silhouette") ? fx.sun : style.includes("moon") ? fx.moon : fx.back;
    sources.push({ role, fixture, azimuth_deg: role === "rim" ? 145 * -o.keySide : 170 * -o.keySide, elevation_deg: 40, distance_m: 3,
      intensity: r1(L.back, 2), cct_k: cctFor(o.env, o.tod, "back", style), softness: 0.35, motivation: "separates subject from background" });
  }
  if (o.practicals.length || style.includes("practical") || (o.tod.includes("night") && o.env !== "outdoor"))
    sources.push({ role: "practical", fixture: fx.practical, azimuth_deg: 120 * o.keySide, elevation_deg: 5, distance_m: 2.5, intensity: 0.3,
      cct_k: 2700, softness: 0.6, motivation: "visible in frame; motivates the key" });
  if (style.includes("coloured") || style.includes("gel")) for (const s of sources) {
    if (s.role === "backlight" || s.role === "rim") s.gel = "deep blue / cyan";
    if (s.role === "key") s.gel = "magenta / amber (complementary)";
  }
  let setup = style;
  if (L.ratio >= 6 && L.key_az >= 50 && L.key_az <= 80) setup += L.key_az < 70 ? " — Rembrandt" : " — split";
  else if (L.ratio <= 2 && L.key_az < 20) setup += " — high-key butterfly / frontal";
  return {
    setup, key_to_fill_ratio: `${g(L.ratio)}:1 (~${r1(Math.log2(L.ratio), 1)} stops)`, ratio: L.ratio, sources,
    ambient: { role: "ambient", level: L.ratio >= 6 ? "low" : L.ratio >= 3 ? "moderate" : "high" }, haze: L.haze,
    exposure: L.ratio >= 6 ? "Expose for the highlight side of the face; let shadows fall 3+ stops under." :
      L.ratio <= 2.5 ? "Expose skin around +⅓ to +⅔ over middle grey; protect window/sky highlights." :
      "Key side at normal exposure; shadow side ~1.5–2.5 stops under.",
    simulation: "conceptual",
  };
}

// ---------------------------------------------------------------- builder
const words = (c: A) => ["genre", "scene_purpose", "desired_emotion", "visual_style", "brief"].map((k) => String(c[k] || "")).join(" ").toLowerCase();

export function signals(an: A) {
  const shots = an.shots || [], env = an.environment || {}, color = an.color || {};
  const motions = shots.map((s: A) => s.movement?.metrics || {});
  const energy = an.narrative ? (v(an.narrative.pacing, {}) || {}).motion_energy || 0 : 0;
  return {
    env: v(env.setting, "uncertain") as string, tod: v(env.time_of_day, "uncertain") as string, practicals: (v(env.practical_lights, []) || []) as A[],
    mean_lum: v(color.mean_luminance, 0.45) as number, contrast: (v(color.contrast, {}) || {}).range_p5_p95 ?? 0.6,
    cast: (v(color.color_temperature, {}) || {}).cast || "neutral",
    palette: ((v(color.palette, []) || []).map((p: A) => p.hex) as string[]).length ? (v(color.palette, []) as A[]).map((p) => p.hex) : ["#7a7a7a", "#404040", "#b0a090"],
    people: v(an.subjects?.count, 0) || 0, energy,
    handheld: shots.some((s: A) => s.movement && v(s.movement.handheld)),
    moving_shots: motions.filter((m: A) => m && Math.abs(m.pan_rate || 0) + Math.abs(m.zoom_rate || 0) > 0.02).length,
  };
}

export function scoreArchetypes(an: A, c: A): [string, number, string[]][] {
  const w = words(c);
  const notes = v(an.ai_notes?.notes, {}) || {};
  const aiw = [...(notes.mood || []), ...(notes.genre_hints || [])].join(" ").toLowerCase();
  const sig = signals(an);
  const pres = c.preservation || "medium";
  const bi = BUDGET_ORDER.indexOf(c.budget || "low");
  const T: Record<string, number> = { realistic: 0, balanced: 1, stylized: 2 };
  const out: [string, number, string[]][] = [];
  for (const [key, a] of Object.entries(ARCHETYPES)) {
    const why: string[] = [];
    let s = 0;
    const hits = (a.keywords as string[]).filter((k) => w.includes(k));
    if (hits.length) { s += 1.4 * Math.min(3, hits.length); why.push(`matches your brief (${hits.slice(0, 3).join(", ")})`); }
    const aih = (a.keywords as string[]).filter((k) => aiw.includes(k) && !hits.includes(k));
    if (aih.length) { s += 0.5 * Math.min(2, aih.length); why.push(`the vision model read the scene as ${aih.slice(0, 2).join(", ")}`); }
    s -= 0.6 * Math.abs(a.movement - (c.movement_intensity ?? 2));
    s -= 0.5 * Math.abs(a.lighting - (c.lighting_complexity ?? 2));
    s -= 0.7 * Math.abs(T[a.treatment] - T[c.treatment || "balanced"]);
    if (BUDGET_ORDER.indexOf(a.min_budget) > bi) { s -= 1.5; why.push("stretches the budget"); }
    if (key === "suspense" && (sig.tod.includes("night") || sig.mean_lum < 0.3)) { s += 1; why.push("footage is already dark"); }
    if ((key === "naturalistic" || key === "atmospheric") && sig.env === "outdoor" && sig.tod.includes("day")) { s += 0.6; why.push("daylight exterior suits it"); }
    if (key === "kinetic" && (sig.energy > 0.2 || sig.handheld)) { s += 0.9; why.push("the clip already has energy"); }
    if (key === "observational" && sig.people >= 1 && sig.energy < 0.1) { s += 0.5; why.push("still, character-focused footage"); }
    if (key === "chiaroscuro" && sig.contrast > 0.7) s += 0.5;
    if (key === "formal" && (sig.people === 1 || sig.people === 2)) s += 0.2;
    if (pres === "high" && (key === "naturalistic" || key === "observational")) { s += 0.8; why.push("stays close to the original"); }
    if (pres === "low" && ["stylized", "kinetic", "formal"].includes(key)) s += 0.6;
    if (c.intent === "animation" && ["stylized", "formal", "kinetic"].includes(key)) s += 0.5;
    out.push([key, r1(s, 3), why]);
  }
  return out.sort((x, y) => y[1] - x[1]);
}

const feature = (a: A) => [Math.log2(a.light.ratio) / 4, a.movement / 4, (a.lens_ff[0] + a.lens_ff[1]) / 300, a.light.softness,
  (a.size_step + 2) / 4, a.light.haze ? 1 : 0, a.grade.sat - 1];

function select(scored: [string, number, string[]][], n: number) {
  const chosen: typeof scored = [];
  const pool = [...scored];
  while (pool.length && chosen.length < n) {
    const mmr = (it: (typeof scored)[0]) => {
      if (!chosen.length) return it[1];
      const f = feature(ARCHETYPES[it[0]]);
      const sim = Math.max(...chosen.map((c) => { const h = feature(ARCHETYPES[c[0]]); return 1 / (1 + Math.hypot(...f.map((x, i) => x - h[i]))); }));
      return it[1] - 3 * sim;
    };
    let best = pool[0];
    for (const it of pool) if (mmr(it) > mmr(best)) best = it;
    chosen.push(best);
    pool.splice(pool.indexOf(best), 1);
  }
  return chosen;
}

export function sceneModel(an: A) {
  let geo: A[] = [];
  for (const s of an.shots || []) { const gg = v(s.scene_geometry, {}) || {}; if (gg.subjects_m?.length) { geo = gg.subjects_m; break; } }
  const subjects: A[] = geo.length
    ? geo.slice(0, 4).map((p, i) => ({ id: `S${i + 1}`, label: `Subject ${i + 1}`, x: +p.x, z: Math.max(1, +p.z), height: 1.7, facing_deg: 180 }))
    : [{ id: "S1", label: "Subject / point of interest", x: 0, z: 3, height: 1.7, facing_deg: 180 }];
  if (subjects.length >= 2) { subjects[0].facing_deg = 135; subjects[subjects.length - 1].facing_deg = 225; }
  const sig = signals(an);
  return {
    units: "metres", origin: "original camera position on the floor; +z points where the original camera looked", subjects,
    set: { type: sig.env, time_of_day: sig.tod,
      practicals: sig.practicals.slice(0, 3).map((p) => ({ x: r1((p.x - 0.5) * 4, 2), z: r1(subjects[0].z + 1.5, 2), height: 1.4, tint: p.tint || "warm" })),
      background_z: r1(Math.max(...subjects.map((s) => s.z)) + 3, 2) },
    original_camera: { position: [0, 1.5, 0], target: [subjects[0].x, 1.4, subjects[0].z] }, estimated: true,
    note: "Subject positions are rough estimates from the frame, assuming average human height and a ~55° lens.",
  };
}

function target(scene: A, idx: number, size = "medium"): [number[], A] {
  const sub = scene.subjects[idx % scene.subjects.length];
  return [[sub.x, r1((SUBJECT_REF_HEIGHT[size] ?? 1.35) * (sub.height || 1.7) / 1.7, 3), sub.z], sub];
}

export function cameraPath(move: string, p: number[], t: number[], focal: number, speed: number, dur: number) {
  const m = move.toLowerCase(), D = speed * dur;
  const unit = (a: number[], b: number[]) => { const d = [0, 1, 2].map((i) => b[i] - a[i]); const n = Math.hypot(...d) || 1; return d.map((x) => x / n); };
  const K = (tt: number, pos: number[], tg: number[] = t) => ({ t: tt, pos, target: tg, focal_mm: focal });
  let k: A[];
  if (/push|dolly-in|dolly in|creeping/.test(m)) { const u = unit(p, t); k = [K(0, p.map((x, i) => x - u[i] * D / 2)), K(1, p.map((x, i) => x + u[i] * D / 2))]; }
  else if (/pull|dolly out/.test(m)) { const u = unit(p, t); k = [K(0, p.map((x, i) => x + u[i] * D / 2)), K(1, p.map((x, i) => x - u[i] * D / 2))]; }
  else if (/lateral|slide|drift/.test(m) || (m.includes("track") && !m.includes("handheld"))) {
    const u = unit(p, t), side = [u[2], 0, -u[0]];
    k = [K(0, p.map((x, i) => x - side[i] * D / 2), t.map((x, i) => x - side[i] * D / 4)), K(1, p.map((x, i) => x + side[i] * D / 2), t.map((x, i) => x + side[i] * D / 4))];
  } else if (/arc|orbit/.test(m)) {
    const r = Math.hypot(p[0] - t[0], p[2] - t[2]), a0 = Math.atan2(p[0] - t[0], p[2] - t[2]);
    const sw = Math.min(rad(60), D / Math.max(r, 0.5));
    k = [0, 1, 2, 3, 4].map((i) => K(i / 4, [t[0] + r * Math.sin(a0 - sw / 2 + sw * i / 4), p[1], t[2] + r * Math.cos(a0 - sw / 2 + sw * i / 4)]));
  } else if (/crane|rise/.test(m)) k = [K(0, [p[0], Math.max(0.4, p[1] - 0.8), p[2]]), K(1, [p[0], p[1] + 1.2, p[2]])];
  else if (/whip|pan/.test(m)) { const off = m.includes("whip") ? 1.2 : 0.5; k = [K(0, p, [t[0] - off, t[1], t[2]]), K(1, p)]; }
  else if (/handheld|follow|breathing/.test(m)) {
    const amp = m.includes("breathing") ? 0.03 : 0.08, fwd = unit(p, t), mv = /follow|tracking/.test(m);
    k = [0, 1, 2, 3, 4, 5, 6].map((i) => K(i / 6, [p[0] + amp * Math.sin(i * 2.1) + (mv ? fwd[0] * D * (i / 6 - 0.5) : 0), p[1] + amp * 0.6 * Math.cos(i * 1.7),
      p[2] + amp * Math.cos(i * 2.9) + (mv ? fwd[2] * D * (i / 6 - 0.5) : 0)], [t[0] + amp * Math.sin(i * 1.3), t[1], t[2]]));
  } else k = [K(0, p), K(1, p)];
  for (const x of k) { x.pos = x.pos.map((y: number) => r1(y, 3)); x.target = x.target.map((y: number) => r1(y, 3)); x.focal_mm = r1(x.focal_mm, 1); }
  return { keyframes: k, estimated: true };
}

export function rig(move: string, budget: string) {
  const r = RIGS[budget], m = move.toLowerCase();
  const order: [string, string | null][] = [["dolly", "dolly"], ["push", "dolly"], ["creeping", "dolly"], ["slide", "slider"], ["lateral", "dolly"],
    ["drift", "gimbal"], ["arc", "gimbal"], ["crane", "crane"], ["follow", "gimbal"], ["tracking", "track"], ["whip", "tripod"], ["pan", "tripod"],
    ["static", "tripod"], ["handheld", null]];
  for (const [k, rg] of order) if (m.includes(k)) return rg ? r[rg] : "handheld (shoulder rig if available)";
  return r.tripod;
}

export function viewLabel(az: number) {
  const a = Math.abs(az) % 360, side = az > 0 ? "camera-left" : "camera-right";
  if (a < 12) return "frontal";
  if (a < 55) return `three-quarter front (${side})`;
  if (a < 110) return `profile (${side})`;
  if (a < 160) return `three-quarter back / over-the-shoulder (${side})`;
  return "from behind";
}

const BLOCK1: Record<string, string> = {
  naturalistic: "Let the actor find natural marks; camera adjusts rather than the actor.",
  chiaroscuro: "Actor turns into the key light on the decisive line, half the face falling into shadow.",
  atmospheric: "Actor moves slowly toward the backlight so the halo grows through the shot.",
  kinetic: "Actor keeps moving through the frame; camera chases rather than leads.",
  suspense: "Actor holds still with their back to unlit space; something off-screen draws their look.",
  stylized: "Actor hits an exact mark in profile against the bright background.",
  observational: "Actor is unaware of the camera; partial occlusion by foreground objects is welcome.",
  formal: "Actor stands dead centre and turns square to the lens on the punchline." };
const BLOCK2: Record<string, string> = {
  naturalistic: "Two-person staging with overlapping action; keep both eyelines on the same side of the line.",
  chiaroscuro: "Separate the characters into light and shadow; the dominant one stands in the key.",
  atmospheric: "Characters drift closer; the gap between them is lit by the backlight.",
  kinetic: "Characters cross and re-cross; camera picks up whoever moves.",
  suspense: "One character stays in the foreground, partly blocking the other.",
  stylized: "Characters face each other in profile silhouettes, framed symmetrically.",
  observational: "Shoot through a doorway or window; let one character partly hide the other.",
  formal: "Characters stand side by side facing the lens, evenly spaced across the centre line." };
const GRADE: Record<string, string> = {
  naturalistic: "Match skin tones to reality; keep the existing palette, only balance shots to each other.",
  chiaroscuro: "Pull midtones down, deepen blacks, keep a single warm accent in the highlights.",
  atmospheric: "Lift and tint blacks, soften contrast, add gentle halation around highlights.",
  kinetic: "Push saturation and local contrast; let practical colours bloom.",
  suspense: "Cool the shadows, keep practicals warm, hold detail just above black.",
  stylized: "Commit to two complementary hues; everything else drops toward black.",
  observational: "Filmic, slightly warm, low-saturation midtones.",
  formal: "Clean, bright primaries; everything colour-coordinated to the set." };

export function production(con: A) {
  const a = ARCHETYPES[con.archetype];
  const eq = new Set<string>(), rigs = new Set<string>();
  for (const s of con.shots) {
    rigs.add(s.movement.rig);
    for (const src of s.lighting.sources) if (src.fixture && src.fixture !== "Sun") eq.add(src.fixture);
    if (s.lighting.haze) eq.add(FIXTURES[con.budget].haze);
  }
  const lenses = [...new Set<number>(con.shots.map((s: A) => s.lens.focal_mm))].sort((x, y) => x - y);
  let cx = a.complexity + (con.shots.some((s: A) => s.lighting.haze) ? 1 : 0);
  cx += [...rigs].filter((r) => !["tripod", "fluid-head tripod", "geared head"].includes(r)).length > 1 ? 1 : 0;
  cx = Math.max(1, Math.min(5, cx - (con.budget === "micro" ? 1 : 0)));
  return { complexity: cx, complexity_label: ["", "very simple", "simple", "moderate", "involved", "demanding"][cx],
    camera_support: [...rigs].sort(), lenses: lenses.map((l) => `${l} mm (${con.sensor.replace(/_/g, " ")})`), lighting_equipment: [...eq].sort(),
    crew_hint: ({ 1: "1–2 people", 2: "2–3 people", 3: "3–5 people", 4: "5–8 people", 5: "8+ people" } as A)[cx], setups: con.shots.length };
}

export function continuity(con: A, sig: A) {
  const risks: string[] = [], shots = con.shots;
  if (con.scene.subjects.length > 1) {
    const sides = new Set(shots.filter((s: A) => Math.abs(s.azimuth_deg) > 5).map((s: A) => Math.sign(s.azimuth_deg)));
    if (sides.size > 1) risks.push("Camera crosses between sides of the subjects — check the 180° line and eyeline directions.");
  }
  if (shots.some((s: A) => s.lighting.haze)) risks.push("Haze density drifts between takes; meter it and re-haze before every take.");
  if (sig.env === "outdoor" || JSON.stringify(shots.map((s: A) => s.lighting.sources)).toLowerCase().includes("window"))
    risks.push("Daylight changes over the shoot day; schedule the wide and closer shots in a short window or flag the sun.");
  if (shots.some((s: A) => s.movement.type.includes("handheld")) && shots.some((s: A) => s.movement.type.startsWith("static")))
    risks.push("Mixing handheld and locked-off shots: keep the shift deliberate so it reads as a choice.");
  if (shots.some((s: A) => s.lighting.sources.some((x: A) => x.role === "practical"))) risks.push("Practicals in frame must stay on, at the same level, in every angle.");
  if (new Set(shots.map((s: A) => s.lens.focal_mm)).size > 2) risks.push("Several focal lengths: match perspective on matching singles (same lens for the reverse).");
  if (!risks.length) risks.push("Low continuity risk; keep props and actor positions consistent between set-ups.");
  return risks;
}

export function differences(con: A, an: A) {
  const out: string[] = [];
  const orig = new Map<number, A>((an.shots || []).map((s: A) => [s.index, s]));
  for (const s of con.shots) {
    const o = orig.get(s.source_shot_index);
    if (!o) continue;
    const label = `Shot ${s.index + 1}`;
    const osz = normaliseSize(v(o.shot_size));
    if (osz !== s.size) out.push(`${label}: ${s.size} instead of ${osz} — ${SHOT_SIZES.indexOf(s.size) < SHOT_SIZES.indexOf(osz) ? "tighter, more intimate" : "wider, more context"}.`);
    const omv = o.movement ? v(o.movement.movement) || "undetermined" : "undetermined";
    if (!s.movement.type.includes(omv.split(" ")[0])) out.push(`${label}: ${s.movement.type} replaces the original ${omv}.`);
    const oang = (v(o.camera_angle) || "").split(" (")[0];
    if (oang && oang !== s.angle && !oang.includes("undetermined")) out.push(`${label}: ${s.angle} rather than ${oang}.`);
  }
  const tk = v(an.color?.tonal_key, "");
  const ratio = con.shots.length ? con.shots[0].lighting.ratio : 2;
  const nk = ratio >= 6 ? "low-key" : ratio <= 2 ? "high-key" : "mid-key";
  if (tk && tk !== nk) out.push(`Overall lighting becomes ${nk} (original reads as ${tk}).`);
  const cast = (v(an.color?.color_temperature, {}) || {}).cast;
  out.push(`Grade: ${con.color.direction}` + (cast ? ` (original cast: ${cast}).` : ""));
  return out;
}

function buildConcept(key: string, why: string[], an: A, c: A, variant: number) {
  const a = ARCHETYPES[key], sig = signals(an);
  const sensor = c.sensor || "super35", budget = c.budget || "low", cxCtl = c.lighting_complexity ?? 2, mv = c.movement_intensity ?? 2;
  const pres = c.preservation || "medium", scene = sceneModel(an);
  const orig = an.shots?.length ? an.shots : [{ index: 0, start_s: 0, end_s: v(an.clip?.duration_s, 5) }];
  const total = orig.reduce((x: number, s: A) => x + s.end_s - s.start_s, 0) || 5;
  const scaleT = (c.target_duration_s || total) / total;
  let keySide = sig.practicals.length ? (sig.practicals[0].x < 0.5 ? 1 : -1) : 1;
  if (variant % 2) keySide *= -1;
  const [lo, hi] = a.lens_ff;
  const n = orig.length;
  const shots: A[] = [];
  let tc = 0;
  orig.forEach((os: A, i: number) => {
    const dur = r1((os.end_s - os.start_s) * scaleT, 2);
    let step = a.size_step;
    if (pres === "high") step = Math.max(-1, Math.min(1, step));
    if (pres === "low" && n > 1) step += i % 2 ? -1 : 1;
    if (i === n - 1 && ["chiaroscuro", "suspense", "atmospheric", "kinetic"].includes(key) && n > 1) step -= 1;
    const size = stepSize(normaliseSize(v(os.shot_size)), step);
    const ff = lo + (hi - lo) * (n > 1 ? i / Math.max(1, n - 1) : 0.5);
    const focal = toSensorFocal(ff, sensor);
    let dist = distanceFor(size, ff, sensor);
    if (key === "kinetic") dist = Math.max(0.6, dist);
    let angle: string = a.angle;
    if (pres === "high") { angle = (v(os.camera_angle) || "eye level").split(" (")[0]; if (!(angle in ANGLES)) angle = "eye level"; }
    if (key === "suspense" && i === n - 1) angle = "eye level";
    const [height, tilt] = ANGLES[angle] || ANGLES["eye level"];
    const [tg, sub] = target(scene, scene.subjects.length > 1 ? i : 0, size);
    const comp: string = a.composition;
    const azMap: [string, number][] = [["three-quarter", 35], ["profile", 80], ["frontal", 0], ["over-the-shoulder", 150], ["centred", 0], ["through", 60]];
    let azimuth = (azMap.find(([k]) => comp.includes(k))?.[1] ?? 30) * ((i + variant) % 2 === 0 ? 1 : -1);
    if (comp.includes("over-the-shoulder") && scene.subjects.length > 1) {
      const other = scene.subjects[(scene.subjects.indexOf(sub) + 1) % scene.subjects.length];
      azimuth = deg(Math.atan2(other.x - sub.x, other.z - sub.z)) - sub.facing_deg + 12;
    }
    if (key === "formal") azimuth = 0;
    const pos = orbitPosition(tg, azimuth, dist, height, sub.facing_deg);
    const moves: string[] = mv > 0 ? a.moves : ["static"];
    let move = moves[(i + variant) % moves.length];
    if (mv >= 3 && move.startsWith("static") && moves.length > 1) move = moves[(i + variant + 1) % moves.length];
    const speed = move.startsWith("static") ? 0 : r1(a.speed * (0.5 + mv / 4), 3);
    let purpose = i === 0 && n > 1 ? "establish geography and mood" : i === n - 1 ? "deliver the emotional beat" : "develop the moment and relationship";
    if (n === 1) purpose = "carry the whole moment in a single designed shot";
    const two = scene.subjects.length > 1;
    const focus = two && a.dof.includes("shallow") ? "rack focus between subjects on the key line" : speed > 0 ? "hold focus on the eyes; pull with the move" : "focus on the near eye";
    let blocking = (scene.subjects.length === 1 ? BLOCK1 : BLOCK2)[key];
    if (move.includes("push") || move.includes("creeping")) blocking += " Time the push so it lands on the emotional turn.";
    shots.push({
      index: i, source_shot_index: os.index ?? i, start_s: r1(tc, 2), duration_s: dur, purpose, size, angle,
      dutch_roll_deg: angle === "dutch angle" ? 12 : 0, composition: comp, camera_height_m: height, camera_tilt_deg: tilt,
      viewing_direction: viewLabel(azimuth), subject_target: sub.id, azimuth_deg: r1(azimuth, 1), distance_m: dist,
      lens: { category: lensCategory(ff), focal_mm: Math.round(focal), ff_equiv_mm: Math.round(ff),
        range_mm: [Math.round(toSensorFocal(lo, sensor)), Math.round(toSensorFocal(hi, sensor))], sensor, perspective: perspectiveNote(ff) },
      dof: { aperture: a.aperture, strategy: a.dof, focus },
      movement: { type: move, speed: speed === 0 ? "none" : speed < 0.2 ? "slow" : speed < 0.6 ? "moderate" : "fast", speed_m_s: speed, rig: rig(move, budget) },
      blocking, lighting: buildLighting(a, { env: sig.env, tod: sig.tod, practicals: sig.practicals, budget, complexity: Math.min(cxCtl, 4), keySide }),
      transition_out: i < n - 1 ? a.transition.split(",")[0] : "end of scene", notes: "",
      camera_path: cameraPath(move, pos, tg, focal, speed, dur),
    });
    tc += dur;
  });
  const palette = sig.palette.slice(0, 5);
  const gr = a.grade;
  let graded = palette.map((h) => hexShift(h, gr.sat, gr.light, gr.shift));
  if (key === "stylized") graded = [graded[0], hexShift(graded[0], 1.3, 1, 180), "#0b0b12", graded[1] || "#202030"];
  const parts = [a.objective];
  if (why.length) parts.push("Chosen because it " + [...new Set(why)].join("; ") + ".");
  parts.push(`Lighting: ${a.light.style} at about ${g(a.light.ratio)}:1, so the image reads as ${a.emotion}.`);
  if (c.scene_purpose) parts.push(`It serves the stated purpose — ${c.scene_purpose}.`);
  const con: A = {
    archetype: key, name: a.name, objective: a.objective, intended_emotion: c.desired_emotion || a.emotion, rationale: parts.join(" "),
    aspect_ratio: c.aspect_ratio || "16:9", frame_rate: c.frame_rate || 24, sensor, budget,
    color: { palette: graded, source_palette: palette, grade: gr.contrast, direction: GRADE[key] },
    editing: { rhythm: a.rhythm, transitions: a.transition }, shots, scene, alternative_endings: [],
  };
  con.production = production(con);
  con.continuity_risks = continuity(con, sig);
  con.differs_from_original = differences(con, an);
  return con;
}

export function generate(an: A, controls: A) {
  const n = Math.max(1, Math.min(6, Number(controls.concept_count ?? 4)));
  return select(scoreArchetypes(an, controls), n).map(([key, score, why], i) => ({ ...buildConcept(key, why, an, controls, i), score }));
}

// ---------------------------------------------------------------- refinement
function rebuildShot(con: A, s: A) {
  const ff = s.lens.ff_equiv_mm;
  Object.assign(s.lens, { focal_mm: Math.round(toSensorFocal(ff, con.sensor)), category: lensCategory(ff), perspective: perspectiveNote(ff) });
  s.distance_m = distanceFor(s.size, ff, con.sensor);
  const sub = con.scene.subjects.find((x: A) => x.id === s.subject_target) || con.scene.subjects[0];
  const tg = [sub.x, r1((SUBJECT_REF_HEIGHT[s.size] ?? 1.35) * (sub.height || 1.7) / 1.7, 3), sub.z];
  const pos = orbitPosition(tg, s.azimuth_deg, s.distance_m, s.camera_height_m, sub.facing_deg);
  s.viewing_direction = viewLabel(s.azimuth_deg);
  const sp = s.movement.speed_m_s;
  s.movement.speed = sp === 0 ? "none" : sp < 0.2 ? "slow" : sp < 0.6 ? "moderate" : "fast";
  s.movement.rig = rig(s.movement.type, con.budget);
  s.camera_path = cameraPath(s.movement.type, pos, tg, s.lens.focal_mm, sp, s.duration_s);
  const L = s.lighting;
  L.key_to_fill_ratio = `${g(L.ratio)}:1 (~${r1(Math.log2(L.ratio), 1)} stops)`;
  for (const src of L.sources) if (src.role === "fill") src.intensity = r1(1 / L.ratio, 3);
}

export function refresh(con: A, an: A | null) {
  let t = 0;
  for (const s of con.shots) { s.start_s = r1(t, 2); t += s.duration_s; rebuildShot(con, s); }
  con.production = production(con);
  con.continuity_risks = continuity(con, signals(an || {}));
  if (an) con.differs_from_original = differences(con, an);
  return con;
}

type Op = (c: A, idx: number | null, n: number) => string;
const sel = (c: A, idx: number | null) => c.shots.filter((s: A) => idx === null || s.index === idx);
const ops: Record<string, Op> = {
  op_dramatic: (c, idx) => {
    for (const s of sel(c, idx)) {
      const L = s.lighting;
      L.ratio = Math.min(16, L.ratio * 2);
      for (const src of L.sources) if (src.role === "key") {
        src.softness = r1(Math.max(0.15, (src.softness || 0.5) - 0.3), 2);
        src.elevation_deg = Math.max(src.elevation_deg, 40);
        src.azimuth_deg = copysign(Math.max(60, Math.abs(src.azimuth_deg)), src.azimuth_deg || 1);
      }
      if (!L.sources.some((x: A) => x.role === "negative_fill")) {
        const key = L.sources.find((x: A) => x.role === "key");
        L.sources.push({ role: "negative_fill", fixture: "Floppy / black flag", azimuth_deg: -copysign(60, key.azimuth_deg || 1), elevation_deg: 0,
          distance_m: 1, intensity: 0, cct_k: null, softness: null, motivation: "deepens the shadow side" });
      }
      L.setup = L.setup.split(" — ")[0] + " — harder, higher contrast";
      L.exposure = "Expose for the highlight side of the face; let shadows fall 3+ stops under.";
    }
    c.color.grade = "deeper blacks, more contrast; " + c.color.grade;
    return "Made the lighting more dramatic (higher key-to-fill ratio, harder key, negative fill).";
  },
  op_softer: (c, idx) => {
    for (const s of sel(c, idx)) {
      const L = s.lighting;
      L.ratio = Math.max(1.5, L.ratio / 2);
      for (const src of L.sources) if (src.role === "key") src.softness = r1(Math.min(1, (src.softness || 0.5) + 0.3), 2);
      L.sources = L.sources.filter((x: A) => x.role !== "negative_fill");
      L.setup = L.setup.split(" — ")[0] + " — softer, lower contrast";
    }
    return "Softened the lighting (lower ratio, larger source, no negative fill).";
  },
  op_lower: (c, idx) => {
    for (const s of sel(c, idx)) {
      s.camera_height_m = r1(Math.max(0.15, s.camera_height_m - 0.5), 2);
      s.camera_tilt_deg = Math.min(25, s.camera_tilt_deg + 8);
      s.angle = s.camera_height_m < 0.4 ? "ground level" : s.camera_height_m < 1.3 ? "low angle" : s.angle;
    }
    return "Moved the camera lower (≈0.5 m) and tilted up.";
  },
  op_higher: (c, idx) => {
    for (const s of sel(c, idx)) {
      s.camera_height_m = r1(Math.min(5, s.camera_height_m + 0.6), 2);
      s.camera_tilt_deg = Math.max(-85, s.camera_tilt_deg - 10);
      s.angle = s.camera_height_m > 4 ? "overhead" : s.camera_height_m > 1.9 ? "high angle" : s.angle;
    }
    return "Raised the camera (≈0.6 m) and tilted down.";
  },
  op_slower: (c, idx) => { for (const s of sel(c, idx)) s.movement.speed_m_s = r1(s.movement.speed_m_s * 0.6, 3); return "Slowed camera movement by 40%."; },
  op_faster: (c, idx) => { for (const s of sel(c, idx)) s.movement.speed_m_s = r1(Math.max(0.2, s.movement.speed_m_s * 1.5), 3); return "Sped up camera movement by 50%."; },
  op_dolly_in: (c, idx) => {
    for (const s of sel(c, idx)) { s.movement.type = "slow push-in (dolly)"; if (s.movement.speed_m_s === 0) s.movement.speed_m_s = 0.12; }
    return "Changed movement to a dolly push-in.";
  },
  op_static: (c, idx) => { for (const s of sel(c, idx)) Object.assign(s.movement, { type: "static", speed_m_s: 0 }); return "Locked the camera off (static)."; },
  op_handheld: (c, idx) => {
    for (const s of sel(c, idx)) { s.movement.type = "handheld tracking"; s.movement.speed_m_s = Math.max(0.3, s.movement.speed_m_s); }
    return "Switched to handheld tracking.";
  },
  op_separation: (c, idx) => {
    for (const s of sel(c, idx)) {
      const L = s.lighting;
      const back = L.sources.find((x: A) => x.role === "backlight" || x.role === "rim");
      if (back) back.intensity = r1(Math.min(1.2, back.intensity + 0.3), 2);
      else {
        const key = L.sources.find((x: A) => x.role === "key");
        L.sources.push({ role: "rim", fixture: "LED with barn doors", azimuth_deg: -copysign(145, key.azimuth_deg || 1), elevation_deg: 40, distance_m: 3,
          intensity: 0.7, cct_k: 4300, softness: 0.35, motivation: "separates subject from background" });
      }
      s.lens.ff_equiv_mm = Math.round(Math.min(200, s.lens.ff_equiv_mm * 1.25));
      s.dof.aperture = "T1.8–T2.8 (one stop wider)";
      s.dof.strategy = "shallower — background softened; add distance between subject and background";
    }
    return "Increased background separation (stronger rim/backlight, longer lens, wider aperture).";
  },
  op_new_angle: (c, idx) => {
    for (const s of sel(c, idx)) s.azimuth_deg = r1(Math.max(-170, Math.min(170, s.azimuth_deg + (s.azimuth_deg <= 0 ? 35 : -35))), 1);
    return "Kept the blocking and moved the camera ≈35° around the subject to a new angle.";
  },
  op_endings: (c, _idx, n) => {
    const last = c.shots[c.shots.length - 1];
    const opts = [
      { name: "Push to extreme close-up", movement: "slow push-in", size: "extreme close-up", note: "Ends inside the character's reaction; withholds the wider context." },
      { name: "Pull-back reveal", movement: "dolly out + crane up", size: "wide", note: "Reveals the space or another character, reframing what we just saw." },
      { name: "Rack focus to background", movement: "static", size: last.size, note: "Focus shifts off the subject to a detail behind them — the scene's real subject." },
      { name: "Hold and let them exit", movement: "static", size: last.size, note: "The subject leaves frame; the empty frame lingers as the final image." },
      { name: "Cut to point of view", movement: "handheld (breathing)", size: "medium", note: "Ends on what the subject sees, putting the audience in their place." },
    ];
    c.alternative_endings = opts.slice(0, Math.max(1, Math.min(5, n)));
    return `Created ${c.alternative_endings.length} alternative endings for the final shot.`;
  },
  op_budget: (c) => {
    c.budget = "micro";
    const fx = FIXTURES.micro;
    for (const s of c.shots) {
      for (const src of s.lighting.sources) {
        if (src.fixture === "Sun") continue;
        const m: Record<string, string> = { key: (src.softness || 0) >= 0.5 ? fx.key_soft : fx.key_hard, fill: fx.fill, backlight: fx.back, rim: fx.back,
          negative_fill: fx.neg, practical: fx.practical };
        src.fixture = m[src.role] ?? src.fixture;
      }
      if (["crane", "arc"].some((k) => s.movement.type.includes(k))) s.movement.type = "slow push-in (handheld/slider)";
      s.lighting.sources = s.lighting.sources.filter((x: A) => !(x.role === "fill" && s.lighting.ratio >= 8));
    }
    return "Rescoped for a small budget: household/LED fixtures, reflectors, slider/handheld instead of cranes.";
  },
  op_wider: (c, idx) => { for (const s of sel(c, idx)) s.size = stepSize(s.size, 1); return "Framed wider by one shot size."; },
  op_tighter: (c, idx) => { for (const s of sel(c, idx)) s.size = stepSize(s.size, -1); return "Framed tighter by one shot size."; },
  op_longer_lens: (c, idx) => { for (const s of sel(c, idx)) s.lens.ff_equiv_mm = Math.round(Math.min(300, s.lens.ff_equiv_mm * 1.5)); return "Moved to a longer lens (more compression, camera further back)."; },
  op_wider_lens: (c, idx) => { for (const s of sel(c, idx)) s.lens.ff_equiv_mm = Math.round(Math.max(12, s.lens.ff_equiv_mm / 1.5)); return "Moved to a wider lens (more depth, camera closer)."; },
  op_warmer: (c, idx) => {
    for (const s of sel(c, idx)) for (const src of s.lighting.sources) if (src.cct_k && src.role !== "practical") src.cct_k = Math.max(2000, src.cct_k - 800);
    c.color.palette = c.color.palette.map((h: string) => hexShift(h, 1.05, 1, -8));
    return "Warmed the light (≈−800 K) and the grade.";
  },
  op_cooler: (c, idx) => {
    for (const s of sel(c, idx)) for (const src of s.lighting.sources) if (src.cct_k && src.role !== "practical") src.cct_k = Math.min(10000, src.cct_k + 800);
    c.color.palette = c.color.palette.map((h: string) => hexShift(h, 1, 1, 10));
    return "Cooled the light (≈+800 K) and the grade.";
  },
  op_haze: (c, idx) => { for (const s of sel(c, idx)) s.lighting.haze = true; return "Added atmospheric haze (backlight will read as visible beams)."; },
  op_no_haze: (c, idx) => { for (const s of sel(c, idx)) s.lighting.haze = false; return "Removed haze."; },
  op_dutch: (c, idx) => { for (const s of sel(c, idx)) { s.dutch_roll_deg = 12; s.angle = "dutch angle"; } return "Added a 12° Dutch tilt."; },
};

const RULES: [RegExp, string][] = [
  [/\b(more|extra)\s+(dramatic|moody|contrast\w*)|\bdarker\b|\bmoodier\b|\bmore\s+shadow/, "op_dramatic"],
  [/\b(softer|flatter|less\s+contrast|less\s+dramatic|gentler)\b/, "op_softer"],
  [/\b(camera\s+)?(lower|low(er)?\s+angle|from\s+below)\b/, "op_lower"],
  [/\b(camera\s+)?(higher|high(er)?\s+angle|from\s+above)\b/, "op_higher"],
  [/\bdolly[\s-]*in\b|\bpush[\s-]*in\b/, "op_dolly_in"],
  [/\bslow(er)?\b/, "op_slower"],
  [/\bfaster\b|\bquicker\b|\bmore\s+energ/, "op_faster"],
  [/\b(lock(ed)?[\s-]*off|static|no\s+movement|tripod)\b/, "op_static"],
  [/\bhandheld\b/, "op_handheld"],
  [/\bseparat|\bbackground\s+(pop|fall\s+away)|\bisolate\s+the\s+subject/, "op_separation"],
  [/\b(change|different|new)\s+(the\s+)?(camera\s+)?angle\b|\bkeep\b.*\bblocking\b/, "op_new_angle"],
  [/\b(alternative|alternate|different)\s+endings?\b/, "op_endings"],
  [/\b(small|low|tiny|micro|cheap|limited)\s+(production\s+)?budget\b|\bcheaper\b|\bon\s+a\s+budget\b/, "op_budget"],
  [/\bwider\s+(shot|frame|framing)\b|\bframe\s+wider\b|\bpull\s+back\b/, "op_wider"],
  [/\btighter\b|\bcloser\s+(shot|framing)\b|\bmove\s+in\s+closer\b/, "op_tighter"],
  [/\blonger\s+lens\b|\btelephoto\b|\bmore\s+compression\b/, "op_longer_lens"],
  [/\bwider\s+lens\b|\bwide[\s-]angle\b/, "op_wider_lens"],
  [/\bwarmer\b/, "op_warmer"],
  [/\bcooler\b|\bcolder\b/, "op_cooler"],
  [/\bno\s+haze\b|\bremove\s+(the\s+)?haze\b/, "op_no_haze"],
  [/\b(add\s+)?haze\b|\batmospher\w*\b|\bsmok\w*\b/, "op_haze"],
  [/\bdutch\b|\bcanted\b/, "op_dutch"],
];
const NUM: Record<string, number> = { one: 1, two: 2, three: 3, four: 4, five: 5 };

export function parseCommand(cmd: string) {
  const found: [string, number | null, number][] = [], unknown: string[] = [];
  const clauses = cmd.split(/[.;\n,]|\band\b|\bthen\b/i).map((x) => x.trim()).filter(Boolean);
  for (const cl of clauses) {
    const low = cl.toLowerCase();
    const m = /\bshot\s+(\d+)\b/.exec(low);
    const idx = m ? +m[1] - 1 : null;
    const nm = /\b(\d|one|two|three|four|five)\b\s+(alternative|alternate|different)/.exec(low);
    const n = nm ? NUM[nm[1]] ?? +nm[1] : 3;
    const used = new Set<string>();
    for (const [re, name] of RULES) {
      if (used.has(name) || !re.test(low)) continue;
      if (name === "op_haze" && used.has("op_no_haze")) continue;
      if (name === "op_lower" && used.has("op_new_angle")) continue;
      found.push([name, idx, n]);
      used.add(name);
    }
    if (!used.size) unknown.push(cl);
  }
  return { ops: found, unknown };
}

export function applyCommand(con: A, cmd: string, an: A | null) {
  const next = clone(con);
  const { ops: found, unknown } = parseCommand(cmd);
  const applied: string[] = [];
  for (const [name, idx, n] of found) {
    if (idx !== null && !next.shots.some((s: A) => s.index === idx)) { unknown.push(`shot ${idx + 1} does not exist`); continue; }
    applied.push(ops[name](next, idx, n));
  }
  if (applied.length) refresh(next, an);
  return { concept: next, applied, unknown };
}

const isNum = (x: A) => typeof x === "number" && Number.isFinite(x);
const EDITABLE: Record<string, (x: A) => boolean> = {
  size: (x) => SHOT_SIZES.includes(x), angle: (x) => x in ANGLES,
  camera_height_m: (x) => isNum(x) && x >= 0.05 && x <= 12, camera_tilt_deg: (x) => isNum(x) && x >= -90 && x <= 90,
  azimuth_deg: (x) => isNum(x) && x >= -180 && x <= 180, dutch_roll_deg: (x) => isNum(x) && x >= -45 && x <= 45,
  duration_s: (x) => isNum(x) && x >= 0.3 && x <= 600, "lens.ff_equiv_mm": (x) => isNum(x) && x >= 8 && x <= 600,
  "dof.aperture": (x) => typeof x === "string" && x.length <= 40, "dof.focus": (x) => typeof x === "string" && x.length <= 300,
  "movement.type": (x) => typeof x === "string" && x.length >= 2 && x.length <= 60, "movement.speed_m_s": (x) => isNum(x) && x >= 0 && x <= 10,
  "lighting.ratio": (x) => isNum(x) && x >= 1 && x <= 32, "lighting.haze": (x) => typeof x === "boolean",
  blocking: (x) => typeof x === "string" && x.length <= 1000, notes: (x) => typeof x === "string" && x.length <= 2000,
  transition_out: (x) => typeof x === "string" && x.length <= 80, composition: (x) => typeof x === "string" && x.length <= 120,
};

export class PatchError extends Error {}
export function applyPatch(con: A, patches: A[], an: A | null) {
  const next = clone(con), applied: string[] = [];
  for (const p of patches) {
    const { field, value, shot } = p;
    if (!(field in EDITABLE)) throw new PatchError(`'${field}' cannot be edited`);
    if (!EDITABLE[field](value)) throw new PatchError(`invalid value for '${field}'`);
    const targets = shot === null || shot === undefined ? next.shots : next.shots.filter((s: A) => s.index === shot);
    if (!targets.length) throw new PatchError(`shot ${shot} does not exist`);
    for (const s of targets) {
      if (field.includes(".")) { const [h, k] = field.split("."); s[h][k] = value; } else s[field] = value;
    }
    applied.push(`Set ${field} = ${value}` + (shot !== null && shot !== undefined ? ` on shot ${shot + 1}` : " on all shots"));
  }
  refresh(next, an);
  return { concept: next, applied };
}

// ---------------------------------------------------------------- shot list
function fmtLight(src: A) {
  const parts = [src.role.replace(/_/g, " ").replace(/\b\w/g, (m: string) => m.toUpperCase())];
  if (src.fixture) parts.push(src.fixture);
  if (src.role !== "negative_fill") {
    const side = src.azimuth_deg > 0 ? "camera-left" : src.azimuth_deg < 0 ? "camera-right" : "on-axis";
    parts.push(`${Math.abs(Math.round(src.azimuth_deg))}° ${side}, ${Math.round(src.elevation_deg)}° up`);
    if (src.cct_k) parts.push(`${src.cct_k} K`);
    parts.push(`${Math.round(src.intensity * 100)}% rel.`);
  } else parts.push(`shadow side (${src.azimuth_deg < 0 ? "camera-right" : "camera-left"})`);
  if (src.gel) parts.push(`gel: ${src.gel}`);
  return parts.join(" · ");
}

export function buildShotList(con: A, overrides: Record<number, A> = {}) {
  const rows = con.shots.map((s: A) => {
    const L = overrides[s.index] || s.lighting;
    const equipment = [s.movement.rig, `${s.lens.focal_mm} mm lens`, ...L.sources.filter((x: A) => x.fixture && x.fixture !== "Sun").map((x: A) => x.fixture)];
    if (L.haze) equipment.push("hazer");
    return {
      shot: s.index + 1, duration_s: s.duration_s, purpose: s.purpose, size: s.size,
      angle: s.angle + (s.dutch_roll_deg ? ` (Dutch ${s.dutch_roll_deg}°)` : ""),
      camera_position: `${s.viewing_direction}, ~${s.distance_m} m from subject, lens height ${s.camera_height_m} m`,
      lens: `${s.lens.focal_mm} mm ${s.lens.category} (${s.lens.ff_equiv_mm} mm FF-equiv., ${s.lens.sensor.replace(/_/g, " ")})`,
      movement: `${s.movement.type} — ${s.movement.speed}` + (s.movement.speed_m_s ? ` (~${s.movement.speed_m_s} m/s)` : "") + `, ${s.movement.rig}`,
      focus_dof: `${s.dof.aperture}; ${s.dof.strategy}; ${s.dof.focus}`, blocking: s.blocking,
      lighting: `${L.setup} — ${L.key_to_fill_ratio}` + (L.haze ? "; haze" : ""), lighting_detail: L.sources.map(fmtLight),
      equipment: [...new Set(equipment)], continuity: con.continuity_risks.slice(0, 2).join("; "), transition: s.transition_out, notes: s.notes || "",
    };
  });
  return { concept_name: con.name, aspect_ratio: con.aspect_ratio, frame_rate: con.frame_rate,
    total_duration_s: r1(rows.reduce((x: number, r: A) => x + r.duration_s, 0), 2), grade: con.color.direction, palette: con.color.palette,
    rows, alternative_endings: con.alternative_endings || [], production: con.production };
}

// ---------------------------------------------------------------- assessment ("is my footage already good?")
const GOOD = 70;
function moveFamily(label: string) {
  const l = (label || "").toLowerCase();
  const fams: [string, string[]][] = [["static", ["static", "locked", "tripod"]], ["push", ["push", "dolly-in", "dolly in", "creeping", "zoom in"]],
    ["pull", ["pull", "dolly out", "zoom out"]], ["pan", ["pan", "whip"]], ["tilt", ["tilt", "crane", "rise"]], ["lateral", ["lateral", "slide", "drift", "track"]],
    ["arc", ["arc", "orbit"]], ["handheld", ["handheld", "follow", "breathing", "gimbal"]]];
  for (const [f, ks] of fams) if (ks.some((k) => l.includes(k))) return f;
  return "other";
}

export function technicalChecks(an: A, c: A) {
  const out: A[] = [];
  const clip = v(an.color?.clipping, {}) || {}, mean = v(an.color?.mean_luminance, 0.45) ?? 0.45, key = v(an.color?.tonal_key, "") || "";
  const pct = (x: number) => `${Math.round(x * 100)}%`;
  if ((clip.highlights || 0) > 0.03) out.push({ severity: clip.highlights > 0.08 ? "major" : "minor", issue: `${pct(clip.highlights)} of the frame is blown-out white.`,
    fix: "Expose ⅔–1 stop lower or flag/diffuse the brightest source; protect windows and skies." });
  if ((clip.shadows || 0) > 0.12 && !key.includes("low-key")) out.push({ severity: "minor", issue: `${pct(clip.shadows)} of the frame is crushed to black.`,
    fix: "Add fill or lift exposure unless the darkness is intended." });
  if (mean < 0.12) out.push({ severity: "major", issue: "The footage is very dark overall.", fix: "Add light or open up 1–2 stops; dark footage gets noisy when brightened later." });
  for (const s of an.shots || []) {
    const n = s.index + 1, roll = v(s.composition?.horizon_roll_deg);
    if (roll !== null && roll !== undefined && Math.abs(roll) > 2.5 && Math.abs(roll) < 7)
      out.push({ severity: "minor", issue: `Shot ${n}: horizon is tilted about ${Math.round(Math.abs(roll))}° — reads as a mistake rather than a Dutch angle.`,
        fix: "Level the camera, or commit to 10°+ if a Dutch angle is intended." });
    const jit = s.movement?.metrics?.jitter || 0;
    if (v(s.movement?.handheld) && (c.movement_intensity ?? 2) <= 1 && jit > 0.006)
      out.push({ severity: "minor", issue: `Shot ${n}: visible handheld shake, but your brief asks for calm movement.`, fix: "Use a tripod, gimbal or stabilise in post." });
  }
  return out;
}

function matchConcept(con: A, an: A) {
  const orig = new Map<number, A>((an.shots || []).map((s: A) => [s.index, s]));
  let pts = 0, total = 0;
  const same: string[] = [], diff: string[] = [];
  const add = (w: number, ok: number, msg: string, good: boolean) => { pts += w * ok; total += w; (good ? same : diff).push(msg); };
  for (const s of con.shots) {
    const o = orig.get(s.source_shot_index);
    if (!o) continue;
    const n = s.index + 1, raw = v(o.shot_size);
    if (raw && raw !== "undetermined" && o.shot_size?.basis !== "heuristic") {
      const osz = normaliseSize(raw), d = Math.abs(SHOT_SIZES.indexOf(osz) - SHOT_SIZES.indexOf(s.size));
      add(3, d === 0 ? 1 : d === 1 ? 0.5 : 0, `Shot ${n} size: ${osz} vs ${s.size}`, d <= 1);
    }
    const oang = (v(o.camera_angle) || "").split(" (")[0];
    if (oang && !oang.includes("undetermined")) add(2, +(oang === s.angle), `Shot ${n} angle: ${oang} vs ${s.angle}`, oang === s.angle);
    const omv = v(o.movement?.movement) || "";
    if (omv && !omv.includes("undetermined")) { const ok = moveFamily(omv) === moveFamily(s.movement.type); add(3, +ok, `Shot ${n} movement: ${omv} vs ${s.movement.type}`, ok); }
  }
  const tk = v(an.color?.tonal_key, "") || "";
  if (tk && con.shots.length) {
    const r = con.shots[0].lighting.ratio, ck = r >= 6 ? "low-key" : r <= 2 ? "high-key" : "mid-key";
    const ok = tk.includes(ck) || (ck === "low-key" && tk.includes("dark"));
    add(3, +ok, `Lighting: ${tk} vs ${ck}`, ok);
  }
  const cast = (v(an.color?.color_temperature, {}) || {}).cast || "";
  const dir = con.color.direction.toLowerCase(), want = dir.includes("cool") ? "cool" : dir.includes("warm") ? "warm" : "neutral";
  if (cast) { const ok = (want === "neutral" && ["neutral", "warm", "cool"].includes(cast)) || cast === want; add(2, +ok, `Colour: ${cast} cast vs ${want} grade`, ok); }
  return { score: total ? Math.round((100 * pts) / total) : 0, confidence: total < 8 ? "low" : total < 13 ? "medium" : "high", already_matches: same, differs: diff };
}

export function assess(an: A, concepts: A[], controls: A) {
  const issues = technicalChecks(an, controls);
  const matches = concepts.map((c) => ({ concept_id: c.id, name: c.data.name, ...matchConcept(c.data, an) })).sort((x, y) => y.score - x.score);
  const best = matches[0] || null, major = issues.filter((i) => i.severity === "major");
  let verdict, headline, detail;
  if (best && best.score >= GOOD && !major.length) {
    verdict = "good"; headline = `Your footage is good — it already works as “${best.name}”.`;
    detail = "You could shoot it this way and keep it. The other concepts are there if you want to explore a different feel." + (issues.length ? " A few small fixes would make it stronger." : "");
  } else if (best && best.score >= GOOD) {
    verdict = "good_with_fixes"; headline = `Your approach already matches “${best.name}”, but there are technical problems.`;
    detail = "Keep the approach and fix the issues listed, or try one of the other concepts.";
  } else {
    verdict = "alternatives"; headline = "Your footage takes a different approach from all of these concepts.";
    detail = "That isn't a judgement that it's wrong — each concept is an alternative way to shoot the scene. Compare them with your original and keep what works."
      + (issues.length ? "" : " Technically, the footage looks sound.");
  }
  return { verdict, headline, detail, technical_issues: issues, best_match: best, matches, threshold: GOOD,
    note: "Matching compares measured shot size, angle, movement, lighting key and colour cast. It can't judge performance, story or taste — you decide what's good." };
}
