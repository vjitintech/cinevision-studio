export type V3 = [number, number, number];

export const add = (a: V3, b: V3): V3 => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
export const sub = (a: V3, b: V3): V3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
export const mul = (a: V3, s: number): V3 => [a[0] * s, a[1] * s, a[2] * s];
export const dot = (a: V3, b: V3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
export const cross = (a: V3, b: V3): V3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
export const len = (a: V3) => Math.hypot(a[0], a[1], a[2]);
export const norm = (a: V3): V3 => { const l = len(a) || 1; return [a[0] / l, a[1] / l, a[2] / l]; };
export const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
export const lerp3 = (a: V3, b: V3, t: number): V3 => [lerp(a[0], b[0], t), lerp(a[1], b[1], t), lerp(a[2], b[2], t)];
export const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));
export const deg = (r: number) => (r * 180) / Math.PI;
export const rad = (d: number) => (d * Math.PI) / 180;
export const smooth = (t: number) => t * t * (3 - 2 * t);

export const SENSORS: Record<string, { w: number; h: number; crop: number; label: string }> = {
  full_frame: { w: 36, h: 20.25, crop: 1, label: "Full frame" },
  super35: { w: 24.89, h: 14, crop: 1.45, label: "Super 35" },
  micro_four_thirds: { w: 17.3, h: 9.73, crop: 2, label: "Micro Four Thirds" },
  phone: { w: 6.4, h: 3.6, crop: 5.6, label: "Phone" },
};

export const SHOT_SIZES = ["extreme close-up", "close-up", "medium close-up", "medium", "medium wide", "full", "wide", "extreme wide"];
export const FRAMING_H: Record<string, number> = { "extreme close-up": 0.14, "close-up": 0.32, "medium close-up": 0.55, medium: 0.9,
  "medium wide": 1.35, full: 2.1, wide: 4, "extreme wide": 11 };
export const SUBJECT_REF_HEIGHT: Record<string, number> = { "extreme close-up": 1.62, "close-up": 1.58, "medium close-up": 1.5, medium: 1.35,
  "medium wide": 1.15, full: 0.95, wide: 0.95, "extreme wide": 0.95 };
export const ANGLES: Record<string, [number, number]> = { "ground level": [0.15, 18], "low angle": [0.75, 12], "eye level": [1.6, 0],
  "high angle": [2.6, -18], overhead: [4.5, -80], "dutch angle": [1.55, 0] };

export function hfovDeg(focalMm: number, sensor: string) {
  return deg(2 * Math.atan(SENSORS[sensor || "super35"].w / (2 * focalMm)));
}

export function distanceFor(size: string, ffMm: number, sensor: string) {
  const s = SENSORS[sensor] || SENSORS.super35;
  const focal = ffMm / s.crop;
  const vfov = 2 * Math.atan(s.h / (2 * focal));
  return FRAMING_H[size] / (2 * Math.tan(vfov / 2));
}

export function lensCategory(ff: number) {
  return ff < 20 ? "ultra-wide" : ff < 35 ? "wide" : ff < 60 ? "standard" : ff < 110 ? "short telephoto" : "telephoto";
}

/** Camera position orbiting a subject, matching backend geometry.orbit_position. */
export function orbit(target: V3, azimuthDeg: number, distance: number, height: number, facingDeg = 180): V3 {
  const a = rad(facingDeg + azimuthDeg);
  const dy = height - target[1];
  const horiz = distance > Math.abs(dy) ? Math.sqrt(Math.max(0.01, distance * distance - dy * dy)) : distance;
  return [target[0] + horiz * Math.sin(a), height, target[2] + horiz * Math.cos(a)];
}

export interface CamState { pos: V3; target: V3; focal: number; roll: number }

function catmull(p0: V3, p1: V3, p2: V3, p3: V3, t: number): V3 {
  const t2 = t * t, t3 = t2 * t;
  const f = (a: number, b: number, c: number, d: number) =>
    0.5 * (2 * b + (-a + c) * t + (2 * a - 5 * b + 4 * c - d) * t2 + (-a + 3 * b - 3 * c + d) * t3);
  return [f(p0[0], p1[0], p2[0], p3[0]), f(p0[1], p1[1], p2[1], p3[1]), f(p0[2], p1[2], p2[2], p3[2])];
}

/** Interpolate a camera path at normalised time t (0..1). Two-key paths ease in and out like a real dolly move. */
export function sampleCamera(keys: { t: number; pos: number[]; target: number[]; focal_mm: number }[], t: number, roll = 0): CamState {
  if (!keys.length) return { pos: [0, 1.6, 0], target: [0, 1.5, 3], focal: 35, roll };
  if (keys.length === 1 || t <= keys[0].t) return { pos: keys[0].pos as V3, target: keys[0].target as V3, focal: keys[0].focal_mm, roll };
  const last = keys[keys.length - 1];
  if (t >= last.t) return { pos: last.pos as V3, target: last.target as V3, focal: last.focal_mm, roll };
  let i = 0;
  while (i < keys.length - 2 && t > keys[i + 1].t) i++;
  const a = keys[i], b = keys[i + 1];
  const local = (t - a.t) / Math.max(1e-6, b.t - a.t);
  if (keys.length === 2) {
    const e = smooth(local);
    return { pos: lerp3(a.pos as V3, b.pos as V3, e), target: lerp3(a.target as V3, b.target as V3, e), focal: lerp(a.focal_mm, b.focal_mm, e), roll };
  }
  const p0 = (keys[i - 1] || a), p3 = (keys[i + 2] || b);
  return {
    pos: catmull(p0.pos as V3, a.pos as V3, b.pos as V3, p3.pos as V3, local),
    target: catmull(p0.target as V3, a.target as V3, b.target as V3, p3.target as V3, local),
    focal: lerp(a.focal_mm, b.focal_mm, local), roll,
  };
}

/** Colour of a black-body source (Tanner Helland approximation), returned 0..1. */
export function cctToRgb(k: number | null | undefined): V3 {
  if (!k) return [1, 1, 1];
  const t = clamp(k, 1000, 40000) / 100;
  let r: number, g: number, b: number;
  if (t <= 66) { r = 255; g = 99.4708025861 * Math.log(t) - 161.1195681661; }
  else { r = 329.698727446 * Math.pow(t - 60, -0.1332047592); g = 288.1221695283 * Math.pow(t - 60, -0.0755148492); }
  if (t >= 66) b = 255; else if (t <= 19) b = 0; else b = 138.5177312231 * Math.log(t - 10) - 305.0447927307;
  return [clamp(r, 0, 255) / 255, clamp(g, 0, 255) / 255, clamp(b, 0, 255) / 255];
}

export const rgbCss = (c: V3, a = 1) => `rgba(${Math.round(clamp(c[0], 0, 1) * 255)},${Math.round(clamp(c[1], 0, 1) * 255)},${Math.round(clamp(c[2], 0, 1) * 255)},${a})`;

export function fmtTime(s: number) {
  const m = Math.floor(s / 60), sec = s - m * 60;
  return `${String(m).padStart(2, "0")}:${sec.toFixed(1).padStart(4, "0")}`;
}

/** Aspect-ratio string ("2.39:1") → number. */
export function aspectOf(ar: string) {
  const [a, b] = ar.split(":").map(Number);
  return a && b ? a / b : 16 / 9;
}
