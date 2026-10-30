// Shot boundaries and global camera motion (port of backend/cinevision/analysis/motion.py).
// Tracking: corners (min-eigenvalue) → two-level SAD block matching → RANSAC similarity transform.
import { Gray, hsvPixel, median, rng, RGBA, boxBlur } from "./img";
import { tag } from "./tag";

export interface Sample { t: number; gray: Gray; hist: Float32Array; rgba: RGBA }
export interface PairMotion { t: number; dt: number; tx: number; ty: number; scale: number; rot_deg: number; inlier_ratio: number; n_points: number;
  subject_motion: number; parallax: number }

export function histOf(img: RGBA): Float32Array {
  const h = new Float32Array(16 * 6 * 6);
  const d = img.data;
  for (let i = 0; i < d.length; i += 4) {
    const [H, S, V] = hsvPixel(d[i], d[i + 1], d[i + 2]);
    const a = Math.min(15, Math.floor((H / 180) * 16)), b = Math.min(5, Math.floor((S / 256) * 6)), c = Math.min(5, Math.floor((V / 256) * 6));
    h[a * 36 + b * 6 + c] += 1;
  }
  let n = 0;
  for (const x of h) n += x * x;
  n = Math.sqrt(n) || 1;
  for (let i = 0; i < h.length; i++) h[i] /= n;
  return h;
}

/** Same formula as OpenCV HISTCMP_BHATTACHARYYA. */
export function bhattacharyya(h1: Float32Array, h2: Float32Array) {
  let s1 = 0, s2 = 0, s = 0;
  for (let i = 0; i < h1.length; i++) { s1 += h1[i]; s2 += h2[i]; s += Math.sqrt(h1[i] * h2[i]); }
  const N = h1.length;
  const k = 1 / Math.sqrt((s1 / N) * (s2 / N) * N * N || 1);
  return Math.sqrt(Math.max(0, 1 - s * k));
}

export function detectShots(samples: Sample[], duration: number, minLen = 0.35) {
  const d: number[] = [];
  for (let i = 1; i < samples.length; i++) d.push(bhattacharyya(samples[i - 1].hist, samples[i].hist));
  const cuts: number[] = [];
  if (d.length) {
    const med = median(d), mad = median(d.map((x) => Math.abs(x - med))) + 1e-6;
    const thr = Math.max(0.35, med + 8 * mad);
    d.forEach((val, j) => {
      const i = j + 1;
      if (val > thr && (!cuts.length || samples[i].t - cuts[cuts.length - 1] >= minLen)) cuts.push(samples[i].t);
    });
  }
  const b = [0, ...cuts, duration];
  return b.slice(0, -1).map((s, i) => ({ index: i, start_s: +s.toFixed(3), end_s: +b[i + 1].toFixed(3) }));
}

function corners(g: Gray, max = 300, minDist = 7): [number, number][] {
  const { w, h, data } = g;
  const Ixx = new Float32Array(w * h), Iyy = new Float32Array(w * h), Ixy = new Float32Array(w * h);
  for (let y = 1; y < h - 1; y++) for (let x = 1; x < w - 1; x++) {
    const i = y * w + x, gx = (data[i + 1] - data[i - 1]) / 2, gy = (data[i + w] - data[i - w]) / 2;
    Ixx[i] = gx * gx; Iyy[i] = gy * gy; Ixy[i] = gx * gy;
  }
  const A = boxBlur({ w, h, data: Ixx }, 2).data, B = boxBlur({ w, h, data: Iyy }, 2).data, C = boxBlur({ w, h, data: Ixy }, 2).data;
  const resp = new Float32Array(w * h);
  let mx = 0;
  const m = 12;
  for (let y = m; y < h - m; y++) for (let x = m; x < w - m; x++) {
    const i = y * w + x, tr = A[i] + B[i], det = A[i] * B[i] - C[i] * C[i];
    const lmin = tr / 2 - Math.sqrt(Math.max(0, (tr * tr) / 4 - det));
    resp[i] = lmin;
    if (lmin > mx) mx = lmin;
  }
  const thr = mx * 0.01;
  const cand: [number, number, number][] = [];
  for (let y = m; y < h - m; y++) for (let x = m; x < w - m; x++) { const v = resp[y * w + x]; if (v > thr) cand.push([v, x, y]); }
  cand.sort((p, q) => q[0] - p[0]);
  const out: [number, number][] = [];
  const cell = minDist, gw = Math.ceil(w / cell), taken = new Uint8Array(gw * Math.ceil(h / cell));
  for (const [, x, y] of cand) {
    const cx = Math.floor(x / cell), cy = Math.floor(y / cell);
    let free = true;
    for (let dy = -1; dy <= 1 && free; dy++) for (let dx = -1; dx <= 1; dx++) {
      const k = (cy + dy) * gw + (cx + dx);
      if (k >= 0 && k < taken.length && taken[k]) { free = false; break; }
    }
    if (!free) continue;
    taken[cy * gw + cx] = 1;
    out.push([x, y]);
    if (out.length >= max) break;
  }
  return out;
}

function half(g: Gray): Gray {
  const w = g.w >> 1, h = g.h >> 1, out = new Float32Array(w * h);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const i = 2 * y * g.w + 2 * x;
    out[y * w + x] = (g.data[i] + g.data[i + 1] + g.data[i + g.w] + g.data[i + g.w + 1]) / 4;
  }
  return { w, h, data: out };
}

function sad(a: Gray, b: Gray, ax: number, ay: number, bx: number, by: number, r: number) {
  let s = 0;
  for (let dy = -r; dy <= r; dy++) {
    const ra = (ay + dy) * a.w, rb = (by + dy) * b.w;
    for (let dx = -r; dx <= r; dx++) s += Math.abs(a.data[ra + ax + dx] - b.data[rb + bx + dx]);
  }
  return s;
}

function track(a: Gray, b: Gray, a2: Gray, b2: Gray, x: number, y: number): [number, number] | null {
  // coarse search at half resolution
  const cx = Math.round(x / 2), cy = Math.round(y / 2), R = 9, P = 3;
  if (cx - R - P < 0 || cy - R - P < 0 || cx + R + P >= a2.w || cy + R + P >= a2.h) return null;
  let best = Infinity, bdx = 0, bdy = 0, second = Infinity;
  for (let dy = -R; dy <= R; dy++) for (let dx = -R; dx <= R; dx++) {
    const s = sad(a2, b2, cx, cy, cx + dx, cy + dy, P);
    if (s < best) { second = best; best = s; bdx = dx; bdy = dy; } else if (s < second) second = s;
  }
  // refine at full resolution
  const fx = Math.round(x), fy = Math.round(y), r = 2, Pf = 4;
  let bx = fx + 2 * bdx, by = fy + 2 * bdy;
  let fb = Infinity, rx = 0, ry = 0;
  const grid: number[][] = [];
  for (let dy = -r; dy <= r; dy++) {
    const row: number[] = [];
    for (let dx = -r; dx <= r; dx++) {
      const X = bx + dx, Y = by + dy;
      if (X - Pf < 0 || Y - Pf < 0 || X + Pf >= b.w || Y + Pf >= b.h || fx - Pf < 0 || fy - Pf < 0 || fx + Pf >= a.w || fy + Pf >= a.h) { row.push(Infinity); continue; }
      const s = sad(a, b, fx, fy, X, Y, Pf);
      row.push(s);
      if (s < fb) { fb = s; rx = dx; ry = dy; }
    }
    grid.push(row);
  }
  if (!Number.isFinite(fb)) return null;
  // reject flat/ambiguous matches
  const patchEnergy = (2 * Pf + 1) ** 2;
  if (fb / patchEnergy > 18) return null;
  bx += rx; by += ry;
  // sub-pixel parabola
  const gx = rx + r, gy = ry + r;
  let sx = 0, sy = 0;
  if (gx > 0 && gx < 2 * r) { const l = grid[gy][gx - 1], c = grid[gy][gx], rr = grid[gy][gx + 1]; const den = l - 2 * c + rr; if (den > 0 && Number.isFinite(den)) sx = (0.5 * (l - rr)) / den; }
  if (gy > 0 && gy < 2 * r) { const u = grid[gy - 1][gx], c = grid[gy][gx], d = grid[gy + 1][gx]; const den = u - 2 * c + d; if (den > 0 && Number.isFinite(den)) sy = (0.5 * (u - d)) / den; }
  return [bx + sx + (x - fx), by + sy + (y - fy)];
}

function fitSimilarity(p0: [number, number][], p1: [number, number][], idx: number[]) {
  let mx = 0, my = 0, nx = 0, ny = 0;
  for (const i of idx) { mx += p0[i][0]; my += p0[i][1]; nx += p1[i][0]; ny += p1[i][1]; }
  const n = idx.length; mx /= n; my /= n; nx /= n; ny /= n;
  let num1 = 0, num2 = 0, den = 0;
  for (const i of idx) {
    const xc = p0[i][0] - mx, yc = p0[i][1] - my, xp = p1[i][0] - nx, yp = p1[i][1] - ny;
    num1 += xc * xp + yc * yp; num2 += xc * yp - yc * xp; den += xc * xc + yc * yc;
  }
  if (den < 1e-9) return null;
  const a = num1 / den, b = num2 / den;
  return { a, b, tx: nx - (a * mx - b * my), ty: ny - (b * mx + a * my) };
}

export function pairMotion(A: Sample, B: Sample): PairMotion | null {
  const a = A.gray, b = B.gray, a2 = half(a), b2 = half(b);
  const pts = corners(a);
  if (pts.length < 12) return null;
  const p0: [number, number][] = [], p1: [number, number][] = [];
  for (const [x, y] of pts) { const m = track(a, b, a2, b2, x, y); if (m) { p0.push([x, y]); p1.push(m); } }
  if (p0.length < 12) return null;
  const rand = rng(7);
  let best: number[] = [];
  const all = p0.map((_, i) => i);
  const resid = (M: { a: number; b: number; tx: number; ty: number }, i: number) => {
    const [x, y] = p0[i];
    return Math.hypot(M.a * x - M.b * y + M.tx - p1[i][0], M.b * x + M.a * y + M.ty - p1[i][1]);
  };
  for (let it = 0; it < 400; it++) {
    const i = Math.floor(rand() * p0.length), j = Math.floor(rand() * p0.length);
    if (i === j) continue;
    const M = fitSimilarity(p0, p1, [i, j]);
    if (!M) continue;
    const inl = all.filter((k) => resid(M, k) < 2);
    if (inl.length > best.length) best = inl;
  }
  if (best.length < 6) return null;
  const M = fitSimilarity(p0, p1, best)!;
  const inlSet = new Set(best);
  const w = a.w, h = a.h, cx = w / 2, cy = h / 2;
  const tx = M.a * cx - M.b * cy + M.tx - cx, ty = M.b * cx + M.a * cy + M.ty - cy;
  const res = all.map((k) => resid(M, k));
  const outl = all.filter((k) => !inlSet.has(k)).map((k) => res[k]);
  const dt = Math.max(1e-3, B.t - A.t);
  const inlRes = best.map((k) => res[k]);
  return {
    t: A.t, dt, tx: tx / w, ty: ty / h, scale: Math.hypot(M.a, M.b), rot_deg: (Math.atan2(M.b, M.a) * 180) / Math.PI,
    inlier_ratio: best.length / p0.length, n_points: p0.length,
    subject_motion: outl.length >= 5 ? median(outl) / w / dt : 0,
    parallax: Math.sqrt(inlRes.reduce((s, x) => s + x * x, 0) / inlRes.length) / w,
  };
}

export function classify(pairs: PairMotion[]) {
  if (pairs.length < 2) return { movement: tag("undetermined", 0.2, "estimated", "Too little texture or too few frames to track."), camera_vs_subject: null, metrics: null };
  const T = pairs.reduce((s, p) => s + p.dt, 0);
  const pan = pairs.map((p) => -p.tx), tilt = pairs.map((p) => -p.ty), zoom = pairs.map((p) => Math.log(p.scale)), roll = pairs.map((p) => p.rot_deg);
  const sum = (a: number[]) => a.reduce((s, x) => s + x, 0);
  const panR = sum(pan) / T, tiltR = sum(tilt) / T, zoomR = sum(zoom) / T, rollR = sum(roll) / T;
  const jitter = (x: number[]) => {
    if (x.length < 4) return 0;
    const hp = x.map((v, i) => v - ((i > 0 ? x[i - 1] : 0) + v + (i < x.length - 1 ? x[i + 1] : 0)) / 3).slice(1, -1);
    const m = sum(hp) / hp.length;
    return Math.sqrt(sum(hp.map((v) => (v - m) ** 2)) / hp.length);
  };
  const jit = Math.max(jitter(pan), jitter(tilt));
  const subj = median(pairs.map((p) => p.subject_motion)), parallax = median(pairs.map((p) => p.parallax));
  const inlier = sum(pairs.map((p) => p.inlier_ratio)) / pairs.length;
  const comps: [string, number][] = [["pan", Math.abs(panR)], ["tilt", Math.abs(tiltR) * 0.9], ["push", Math.abs(zoomR) * 1.6], ["roll", Math.abs(rollR) / 40]];
  const [main, mag] = comps.reduce((x, y) => (y[1] > x[1] ? y : x));
  const handheld = jit > 0.0035;
  let label: string;
  if (mag < 0.012 && !handheld) label = "static (locked-off)";
  else if (mag < 0.012) label = "handheld (no dominant move)";
  else {
    if (main === "pan") label = `pan ${panR > 0 ? "right" : "left"}`;
    else if (main === "tilt") label = `tilt ${tiltR > 0 ? "up" : "down"}`;
    else if (main === "push") { const d = zoomR > 0 ? "in" : "out"; label = parallax < 0.0025 ? `zoom ${d}` : `dolly / push ${d}`; }
    else label = `roll ${rollR > 0 ? "clockwise" : "counter-clockwise"}`;
    if (main === "pan" && subj < 0.02 && inlier < 0.75) label += " (possibly tracking a subject)";
    if (handheld) label += ", handheld";
  }
  const speed = mag < 0.05 ? "slow" : mag < 0.18 ? "moderate" : "fast";
  const conf = 0.45 + 0.4 * Math.min(1, inlier) - (pairs.length < 5 ? 0.15 : 0);
  return {
    movement: tag(label, conf, "estimated", "Motion-tracking estimate. Zoom vs dolly is inferred from parallax and can be confused on flat scenes."),
    speed: tag(speed, conf * 0.9, "estimated"), handheld: tag(handheld, 0.55, "estimated"),
    camera_vs_subject: tag({ camera_motion: +mag.toFixed(4), subject_motion: +subj.toFixed(4), dominant: mag > subj ? "camera" : subj > 0.01 ? "subject" : "neither" },
      0.5, "estimated", "Units: frame-widths per second."),
    metrics: { pan_rate: +panR.toFixed(4), tilt_rate: +tiltR.toFixed(4), zoom_rate: +zoomR.toFixed(4), roll_rate_deg_s: +rollR.toFixed(3), jitter: +jit.toFixed(5),
      parallax: +parallax.toFixed(5), inlier_ratio: +inlier.toFixed(3), pairs: pairs.length },
  };
}
