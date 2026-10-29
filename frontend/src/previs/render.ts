// Software 3D renderer for previs: flat-shaded proxy geometry, painter's algorithm, near-plane clipping.
// Deliberately simple and dependency-free; every view in the app (storyboard, animatic, camera planner,
// lighting preview, 3D previs) uses this so they always agree with each other.
import { add, cctToRgb, clamp, cross, dot, len, mul, norm, rad, rgbCss, SENSORS, sub, V3, CamState } from "./math";
import type { LightSource, Scene } from "../types";

export interface Face { pts: V3[]; n: V3; base: V3; layer: 0 | 1; tag?: string }
export interface WorldLight { pos: V3; color: V3; intensity: number; softness: number; role: string; haze: boolean }
export interface Line3 { a: V3; b: V3; color: string; width?: number; dash?: number[] }
export interface RenderOpts {
  sensor: string; style?: "shaded" | "sketch"; lights?: WorldLight[]; ambient?: number; lines?: Line3[];
  practicals?: { pos: V3; color: V3 }[]; haze?: boolean; background?: string; highlight?: string | null;
  labels?: { pos: V3; text: string }[]; aspect?: number; negative?: V3[];
}

const SKIN: V3 = [0.78, 0.62, 0.52];
const CLOTH: V3[] = [[0.32, 0.38, 0.5], [0.52, 0.36, 0.3], [0.36, 0.46, 0.36], [0.5, 0.48, 0.42]];

function box(c: V3, size: V3, yaw: number, base: V3, tag?: string): Face[] {
  const [w, h, d] = size.map((v) => v / 2) as V3;
  const cs = Math.cos(yaw), sn = Math.sin(yaw);
  const tr = (p: V3): V3 => [c[0] + p[0] * cs + p[2] * sn, c[1] + p[1], c[2] - p[0] * sn + p[2] * cs];
  const v = [[-w, -h, -d], [w, -h, -d], [w, h, -d], [-w, h, -d], [-w, -h, d], [w, -h, d], [w, h, d], [-w, h, d]].map((p) => tr(p as V3));
  const quads = [[0, 1, 2, 3], [5, 4, 7, 6], [4, 0, 3, 7], [1, 5, 6, 2], [3, 2, 6, 7], [4, 5, 1, 0]];
  return quads.map((q) => {
    const pts = q.map((i) => v[i]);
    let n = norm(cross(sub(pts[1], pts[0]), sub(pts[2], pts[0])));
    const mid = mul(add(add(pts[0], pts[1]), add(pts[2], pts[3])), 0.25);
    if (dot(n, sub(mid, c)) < 0) n = mul(n, -1) as V3; // always outward
    return { pts, n, base, layer: 1 as const, tag };
  });
}

function sphere(c: V3, r: number, base: V3, tag?: string, seg = 10, rings = 7): Face[] {
  const faces: Face[] = [];
  const P = (i: number, j: number): V3 => {
    const th = (i / seg) * Math.PI * 2, ph = (j / rings) * Math.PI;
    return [c[0] + r * Math.sin(ph) * Math.sin(th), c[1] + r * Math.cos(ph), c[2] + r * Math.sin(ph) * Math.cos(th)];
  };
  for (let j = 0; j < rings; j++) for (let i = 0; i < seg; i++) {
    const pts = [P(i, j), P(i + 1, j), P(i + 1, j + 1), P(i, j + 1)];
    const mid = mul(add(add(pts[0], pts[1]), add(pts[2], pts[3])), 0.25);
    faces.push({ pts, n: norm(sub(mid, c)), base, layer: 1, tag });
  }
  return faces;
}

/** Proxy figure for a subject: legs, torso, head and a nose block that shows facing. */
export function figure(s: { id: string; x: number; z: number; height: number; facing_deg: number }, idx: number): Face[] {
  const yaw = rad(s.facing_deg);
  const k = s.height / 1.7;
  const cloth = CLOTH[idx % CLOTH.length];
  const fwd: V3 = [Math.sin(yaw), 0, Math.cos(yaw)];
  const at = (y: number, f = 0): V3 => [s.x + fwd[0] * f, y * k, s.z + fwd[2] * f];
  return [
    ...box(at(0.43), [0.34 * k, 0.86 * k, 0.22 * k], yaw, mul(cloth, 0.6), s.id),
    ...box(at(1.15), [0.46 * k, 0.6 * k, 0.26 * k], yaw, cloth, s.id),
    ...box(at(1.47), [0.11 * k, 0.08 * k, 0.11 * k], yaw, SKIN, s.id),
    ...sphere(at(1.6), 0.115 * k, SKIN, s.id),
    ...box(at(1.6, 0.11 * k), [0.035 * k, 0.05 * k, 0.04 * k], yaw, mul(SKIN, 0.9), s.id),
  ];
}

export function environment(scene: Scene): Face[] {
  const faces: Face[] = [];
  const zMax = Math.max(scene.set.background_z || 8, 4);
  const floor: V3 = scene.set.type === "outdoor" ? [0.26, 0.26, 0.22] : [0.2, 0.19, 0.18];
  for (let x = -10; x < 10; x += 2) for (let z = -8; z < zMax; z += 2) {
    faces.push({ pts: [[x, 0, z], [x, 0, z + 2], [x + 2, 0, z + 2], [x + 2, 0, z]], n: [0, 1, 0], base: floor, layer: 0 });
  }
  const wall: V3 = scene.set.type === "outdoor" ? [0.36, 0.42, 0.5] : [0.27, 0.27, 0.28];
  for (let x = -10; x < 10; x += 2) {
    faces.push({ pts: [[x, 0, zMax], [x, 4.5, zMax], [x + 2, 4.5, zMax], [x + 2, 0, zMax]], n: [0, 0, -1], base: wall, layer: 0 });
  }
  return faces;
}

export function buildScene(scene: Scene): Face[] {
  return [...environment(scene), ...scene.subjects.flatMap((s, i) => figure(s, i))];
}

/** World-space positions for lighting sources defined relative to a subject (see backend concepts/lighting.py). */
export function worldLights(sources: LightSource[], subject: { x: number; z: number; facing_deg: number; height?: number }, haze: boolean): WorldLight[] {
  const head: V3 = [subject.x, 1.6 * ((subject.height || 1.7) / 1.7), subject.z];
  return sources.filter((s) => s.role !== "negative_fill" && s.role !== "ambient").map((s) => {
    const a = rad(subject.facing_deg + s.azimuth_deg), e = rad(s.elevation_deg);
    const d = Math.min(s.distance_m || 2, 25);
    const pos: V3 = [head[0] + d * Math.cos(e) * Math.sin(a), head[1] + d * Math.sin(e), head[2] + d * Math.cos(e) * Math.cos(a)];
    let color = cctToRgb(s.cct_k || 5600);
    if (s.gel?.includes("blue") || s.gel?.includes("cyan")) color = [0.35, 0.6, 1];
    if (s.gel?.includes("magenta")) color = [1, 0.45, 0.75];
    return { pos, color, intensity: s.intensity, softness: s.softness ?? 0.5, role: s.role, haze };
  });
}

export function negativeFillDirs(sources: LightSource[], subject: { facing_deg: number }): V3[] {
  return sources.filter((s) => s.role === "negative_fill").map((s) => {
    const a = rad(subject.facing_deg + s.azimuth_deg);
    return [Math.sin(a), 0, Math.cos(a)] as V3;
  });
}

// ---------------------------------------------------------------- projection
export interface View { pos: V3; r: V3; u: V3; f: V3; fpx: number; W: number; H: number; roll: number }

export function makeView(cam: CamState, W: number, H: number, sensor: string): View {
  const f = norm(sub(cam.target, cam.pos));
  let r = cross([0, 1, 0], f);
  if (len(r) < 1e-4) r = [1, 0, 0];
  r = norm(r);
  const u = cross(f, r);
  const sw = (SENSORS[sensor] || SENSORS.super35).w;
  return { pos: cam.pos, r, u, f, fpx: (cam.focal / sw) * W, W, H, roll: rad(cam.roll || 0) };
}

const NEAR = 0.05;
const toCam = (v: View, p: V3): V3 => { const d = sub(p, v.pos); return [dot(d, v.r), dot(d, v.u), dot(d, v.f)]; };

function toScreen(v: View, c: V3): [number, number] {
  let x = (c[0] / c[2]) * v.fpx, y = (c[1] / c[2]) * v.fpx;
  if (v.roll) { const cs = Math.cos(v.roll), sn = Math.sin(v.roll); [x, y] = [x * cs - y * sn, x * sn + y * cs]; }
  return [v.W / 2 + x, v.H / 2 - y];
}

function clipNear(poly: V3[]): V3[] {
  const out: V3[] = [];
  for (let i = 0; i < poly.length; i++) {
    const a = poly[i], b = poly[(i + 1) % poly.length];
    const ina = a[2] > NEAR, inb = b[2] > NEAR;
    if (ina) out.push(a);
    if (ina !== inb) { const t = (NEAR - a[2]) / (b[2] - a[2]); out.push([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, NEAR]); }
  }
  return out;
}

export function project(v: View, p: V3): [number, number] | null {
  const c = toCam(v, p);
  return c[2] > NEAR ? toScreen(v, c) : null;
}

// ---------------------------------------------------------------- shading
function shade(face: Face, opts: RenderOpts, negDirs: V3[]): V3 {
  const lights = opts.lights || [];
  const c = mul(face.pts.reduce((a, b) => add(a, b), [0, 0, 0] as V3), 1 / face.pts.length);
  let amb = opts.ambient ?? 0.32;
  for (const nd of negDirs) amb *= 1 - 0.55 * clamp(dot(face.n, nd), 0, 1);
  let col: V3 = mul(face.base, amb);
  if (!lights.length) {
    const k = 0.55 + 0.45 * clamp(dot(face.n, norm([-0.4, 0.8, -0.5])), 0, 1);
    return mul(face.base, k);
  }
  for (const L of lights) {
    const dir = norm(sub(L.pos, c));
    const w = clamp(L.softness, 0, 1) * 0.8;
    const lam = clamp((dot(face.n, dir) + w) / (1 + w), 0, 1);
    const d = len(sub(L.pos, c));
    // inverse-square-ish falloff normalised at ~2 m; very distant sources (sun/moon) stay uniform
    const fall = L.role === "practical" ? 1 / (1 + 0.15 * d * d) : d > 18 ? 1 : clamp(1.32 / (1 + 0.08 * d * d), 0, 1.25);
    col = add(col, [face.base[0] * L.color[0] * lam * L.intensity * fall * 0.95,
      face.base[1] * L.color[1] * lam * L.intensity * fall * 0.95,
      face.base[2] * L.color[2] * lam * L.intensity * fall * 0.95]);
  }
  return col;
}

export function render(ctx: CanvasRenderingContext2D, cam: CamState, faces: Face[], opts: RenderOpts) {
  const { width: W, height: H } = ctx.canvas;
  const v = makeView(cam, W, H, opts.sensor);
  const sketch = opts.style === "sketch";
  ctx.save();
  ctx.fillStyle = opts.background || (sketch ? "#ece6da" : "#0e1013");
  ctx.fillRect(0, 0, W, H);
  const negDirs: V3[] = opts.negative || [];
  const items: { scr: [number, number][]; depth: number; col: V3; layer: number; tag?: string }[] = [];
  for (const f of faces) {
    const camPts = clipNear(f.pts.map((p) => toCam(v, p)));
    if (camPts.length < 3) continue;
    const toEye = sub(v.pos, f.pts[0]);
    if (f.layer === 1 && dot(f.n, toEye) < 0) continue; // back-face cull for solids
    const scr = camPts.map((c) => toScreen(v, c));
    const depth = camPts.reduce((a, c) => a + c[2], 0) / camPts.length;
    items.push({ scr, depth, col: shade(f, opts, negDirs), layer: f.layer, tag: f.tag });
  }
  items.sort((a, b) => a.layer - b.layer || b.depth - a.depth);
  for (const it of items) {
    ctx.beginPath();
    it.scr.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
    ctx.closePath();
    if (sketch) {
      const l = 0.3 * it.col[0] + 0.55 * it.col[1] + 0.15 * it.col[2];
      const g = Math.round(clamp(0.45 + l * 0.9, 0.25, 0.97) * 235);
      ctx.fillStyle = it.layer === 0 ? `rgb(${g + 6},${g + 2},${g - 6})` : `rgb(${g - 30},${g - 34},${g - 40})`;
      ctx.fill();
      if (it.layer === 1) { ctx.strokeStyle = "rgba(40,34,28,0.55)"; ctx.lineWidth = 0.8; ctx.stroke(); }
      else { ctx.strokeStyle = "rgba(60,50,40,0.12)"; ctx.lineWidth = 0.6; ctx.stroke(); }
    } else {
      const c = it.tag && opts.highlight === it.tag ? add(it.col, [0.08, 0.06, 0]) : it.col;
      ctx.fillStyle = rgbCss(c);
      ctx.fill();
      ctx.strokeStyle = rgbCss(mul(c, 0.85), it.layer === 0 ? 0.35 : 0.6);
      ctx.lineWidth = 0.6;
      ctx.stroke();
    }
  }
  // practical lights
  for (const p of opts.practicals || []) {
    const s = project(v, p.pos);
    if (!s) continue;
    const r = clamp(v.fpx * 0.06 / Math.max(0.5, toCam(v, p.pos)[2]), 2, 30);
    const g = ctx.createRadialGradient(s[0], s[1], 0, s[0], s[1], r * 4);
    g.addColorStop(0, rgbCss(p.color, 0.95)); g.addColorStop(0.25, rgbCss(p.color, 0.35)); g.addColorStop(1, rgbCss(p.color, 0));
    ctx.fillStyle = g; ctx.beginPath(); ctx.arc(s[0], s[1], r * 4, 0, Math.PI * 2); ctx.fill();
  }
  // haze: backlights become visible glow/beam
  if (opts.haze && !sketch) {
    for (const L of opts.lights || []) {
      if (!["backlight", "rim", "key"].includes(L.role)) continue;
      const s = project(v, L.pos);
      const [sx, sy] = s || [L.pos[0] > v.pos[0] ? W * 1.1 : -W * 0.1, H * 0.1];
      const g = ctx.createRadialGradient(sx, sy, 0, sx, sy, Math.max(W, H) * 0.9);
      g.addColorStop(0, rgbCss(L.color, 0.28 * L.intensity)); g.addColorStop(1, rgbCss(L.color, 0));
      ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
    }
  }
  for (const ln of opts.lines || []) {
    let a = toCam(v, ln.a), b = toCam(v, ln.b);
    if (a[2] <= NEAR && b[2] <= NEAR) continue;
    if (a[2] <= NEAR) { const t = (NEAR - a[2]) / (b[2] - a[2]); a = [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, NEAR]; }
    if (b[2] <= NEAR) { const t = (NEAR - b[2]) / (a[2] - b[2]); b = [b[0] + (a[0] - b[0]) * t, b[1] + (a[1] - b[1]) * t, NEAR]; }
    const sa = toScreen(v, a), sb = toScreen(v, b);
    ctx.beginPath(); ctx.moveTo(sa[0], sa[1]); ctx.lineTo(sb[0], sb[1]);
    ctx.strokeStyle = ln.color; ctx.lineWidth = ln.width || 1.5; ctx.setLineDash(ln.dash || []); ctx.stroke(); ctx.setLineDash([]);
  }
  ctx.font = "12px system-ui, sans-serif";
  for (const lb of opts.labels || []) {
    const s = project(v, lb.pos);
    if (!s) continue;
    ctx.fillStyle = sketch ? "#2a241e" : "rgba(231,226,216,0.9)";
    ctx.fillText(lb.text, s[0] + 6, s[1] - 6);
  }
  ctx.restore();
}

/** Lines outlining a camera body + frustum, for overview renders. */
export function frustumLines(cam: CamState, sensor: string, aspect: number, color: string, depth = 1.2): Line3[] {
  const f = norm(sub(cam.target, cam.pos));
  let r = cross([0, 1, 0], f); r = len(r) < 1e-4 ? [1, 0, 0] : norm(r);
  const u = cross(f, r);
  const sw = (SENSORS[sensor] || SENSORS.super35).w;
  const halfW = (sw / 2 / cam.focal) * depth, halfH = halfW / aspect;
  const c = add(cam.pos, mul(f, depth));
  const corners = [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(([x, y]) => add(add(c, mul(r, x * halfW)), mul(u, y * halfH)));
  const lines: Line3[] = corners.map((k) => ({ a: cam.pos, b: k, color, width: 1.2 }));
  corners.forEach((k, i) => lines.push({ a: k, b: corners[(i + 1) % 4], color, width: 1.2 }));
  lines.push({ a: add(c, mul(u, halfH)), b: add(c, mul(u, halfH * 1.4)), color, width: 2 }); // "up" tick
  return lines;
}
