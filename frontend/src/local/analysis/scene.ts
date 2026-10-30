// Colour, composition, environment and narrative analysis (port of backend/cinevision/analysis/*.py).
import { Gray, gaussian, hsvPixel, labPixel, labToRgb, laplacianAbs, median, percentile, resizeGray, rng, RGBA, sobel, toGray } from "./img";
import { tag } from "./tag";

const hex = (r: number, g: number, b: number) => "#" + [r, g, b].map((x) => Math.round(x).toString(16).padStart(2, "0")).join("");

/** Downscale an RGBA frame by nearest sampling (enough for statistics). */
export function shrink(img: RGBA, w: number): RGBA {
  const h = Math.max(1, Math.round((w * img.h) / img.w)), out = new Uint8ClampedArray(w * h * 4);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const sx = Math.floor((x * img.w) / w), sy = Math.floor((y * img.h) / h), si = (sy * img.w + sx) * 4, di = (y * w + x) * 4;
    out[di] = img.data[si]; out[di + 1] = img.data[si + 1]; out[di + 2] = img.data[si + 2]; out[di + 3] = 255;
  }
  return { w, h, data: out };
}

// ---------------------------------------------------------------- colour
export function palette(frames: RGBA[], k = 5) {
  const px: number[][] = [];
  for (const f of frames) { const s = shrink(f, 96); for (let i = 0; i < s.data.length; i += 4) px.push(labPixel(s.data[i], s.data[i + 1], s.data[i + 2])); }
  const rand = rng(7);
  // k-means++ init
  const centers: number[][] = [px[Math.floor(rand() * px.length)]];
  const d2 = (a: number[], b: number[]) => (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2;
  while (centers.length < k) {
    const dist = px.map((p) => Math.min(...centers.map((c) => d2(p, c))));
    const tot = dist.reduce((s, x) => s + x, 0);
    if (tot === 0) break;
    let r = rand() * tot, i = 0;
    while (r > dist[i] && i < dist.length - 1) { r -= dist[i]; i++; }
    centers.push([...px[i]]);
  }
  const lab = new Int32Array(px.length);
  for (let it = 0; it < 20; it++) {
    for (let i = 0; i < px.length; i++) {
      let b = 0, bd = Infinity;
      for (let c = 0; c < centers.length; c++) { const d = d2(px[i], centers[c]); if (d < bd) { bd = d; b = c; } }
      lab[i] = b;
    }
    const sums = centers.map(() => [0, 0, 0, 0]);
    for (let i = 0; i < px.length; i++) { const s = sums[lab[i]]; s[0] += px[i][0]; s[1] += px[i][1]; s[2] += px[i][2]; s[3]++; }
    sums.forEach((s, c) => { if (s[3]) centers[c] = [s[0] / s[3], s[1] / s[3], s[2] / s[3]]; });
  }
  const counts = centers.map((_, c) => 0 + Array.prototype.filter.call(lab, (x: number) => x === c).length / px.length);
  return centers.map((c, i) => ({ hex: hex(...labToRgb(c[0], c[1], c[2])), share: +counts[i].toFixed(3) }))
    .sort((a, b) => b.share - a.share);
}

export function toneAndColor(frames: RGBA[]) {
  const ys: number[] = [], sats: number[] = [], as: number[] = [], bs: number[] = [];
  for (const f of frames) {
    const s = shrink(f, 160);
    for (let i = 0; i < s.data.length; i += 4) {
      const r = s.data[i], g = s.data[i + 1], b = s.data[i + 2];
      ys.push((0.299 * r + 0.587 * g + 0.114 * b) / 255);
      sats.push(hsvPixel(r, g, b)[1] / 255);
      const L = labPixel(r, g, b);
      if (L[0] > 60 && L[0] < 220) { as.push(L[1] - 128); bs.push(L[2] - 128); }
    }
  }
  const mean = ys.reduce((s, x) => s + x, 0) / ys.length;
  const [p5, p50, p95] = [5, 50, 95].map((q) => percentile(ys, q));
  const contrast = p95 - p5;
  const clipHi = ys.filter((y) => y > 0.98).length / ys.length, clipLo = ys.filter((y) => y < 0.02).length / ys.length;
  const hist = new Array(10).fill(0);
  for (const y of ys) hist[Math.min(9, Math.floor(y * 10))] += 1 / ys.length;
  const sat = sats.reduce((s, x) => s + x, 0) / sats.length;
  const a = as.length ? as.reduce((s, x) => s + x, 0) / as.length : 0, b = bs.length ? bs.reduce((s, x) => s + x, 0) / bs.length : 0;
  const key = mean > 0.58 && contrast < 0.65 ? "high-key" : mean < 0.32 && p95 > 0.45 ? "low-key" : mean < 0.25 ? "dark / underexposed" : "mid-key";
  let temp = "neutral", cct = "≈5000–5600 K look";
  if (b > 6 && b > Math.abs(a) * 0.6) { temp = "warm"; cct = "≈3200–4300 K look"; }
  else if (b < -5) { temp = "cool"; cct = "≈6000–8000 K look"; }
  else if (a > 8) { temp = "magenta-shifted"; cct = "mixed / stylised"; }
  else if (a < -8) { temp = "green-shifted"; cct = "mixed / fluorescent-like"; }
  return {
    mean_luminance: tag(+mean.toFixed(3), 0.95, "observed"),
    median_luminance: tag(+p50.toFixed(3), 0.95, "observed"),
    contrast: tag({ range_p5_p95: +contrast.toFixed(3), label: contrast > 0.75 ? "high" : contrast < 0.4 ? "low" : "medium" }, 0.9, "observed"),
    clipping: tag({ highlights: +clipHi.toFixed(4), shadows: +clipLo.toFixed(4) }, 0.95, "observed"),
    histogram: tag(hist.map((x) => +x.toFixed(4)), 0.95, "observed"),
    saturation: tag({ mean: +sat.toFixed(3), label: sat > 0.45 ? "rich" : sat < 0.2 ? "muted" : "moderate" }, 0.9, "observed"),
    tonal_key: tag(key, 0.7, "estimated", "From luminance distribution of the graded footage, not the set."),
    color_temperature: tag({ cast: temp, approx: cct, lab_a: +a.toFixed(1), lab_b: +b.toFixed(1) }, 0.55, "estimated",
      "Apparent colour cast in the footage; camera white balance and grade both affect it."),
  };
}

// ---------------------------------------------------------------- composition
/** Spectral residual saliency (Hou & Zhang 2007) at 64 px wide, 0..1. Uses a direct DFT (small image). */
export function saliency(img: RGBA): Gray {
  const g0 = toGray(img);
  const W = 64, H = Math.max(8, Math.round((64 * img.h) / img.w));
  const g = resizeGray(g0, W, H);
  const re = new Float64Array(W * H), im = new Float64Array(W * H);
  const dft = (inRe: Float64Array, inIm: Float64Array, inverse: boolean) => {
    const sgn = inverse ? 1 : -1;
    const tRe = new Float64Array(W * H), tIm = new Float64Array(W * H), oRe = new Float64Array(W * H), oIm = new Float64Array(W * H);
    for (let y = 0; y < H; y++) for (let u = 0; u < W; u++) {
      let sr = 0, si = 0;
      for (let x = 0; x < W; x++) { const ang = (sgn * 2 * Math.PI * u * x) / W, c = Math.cos(ang), s = Math.sin(ang), r = inRe[y * W + x], i = inIm[y * W + x]; sr += r * c - i * s; si += r * s + i * c; }
      tRe[y * W + u] = sr; tIm[y * W + u] = si;
    }
    for (let u = 0; u < W; u++) for (let v = 0; v < H; v++) {
      let sr = 0, si = 0;
      for (let y = 0; y < H; y++) { const ang = (sgn * 2 * Math.PI * v * y) / H, c = Math.cos(ang), s = Math.sin(ang), r = tRe[y * W + u], i = tIm[y * W + u]; sr += r * c - i * s; si += r * s + i * c; }
      oRe[v * W + u] = sr; oIm[v * W + u] = si;
    }
    return [oRe, oIm];
  };
  for (let i = 0; i < W * H; i++) re[i] = g.data[i];
  const [FR, FI] = dft(re, im, false);
  const logA = new Float32Array(W * H), ph = new Float64Array(W * H);
  for (let i = 0; i < W * H; i++) { logA[i] = Math.log(Math.hypot(FR[i], FI[i]) + 1e-8); ph[i] = Math.atan2(FI[i], FR[i]); }
  const avg = new Float32Array(W * H);
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    let s = 0, n = 0;
    for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) { const yy = Math.min(H - 1, Math.max(0, y + dy)), xx = Math.min(W - 1, Math.max(0, x + dx)); s += logA[yy * W + xx]; n++; }
    avg[y * W + x] = s / n;
  }
  const r2 = new Float64Array(W * H), i2 = new Float64Array(W * H);
  for (let i = 0; i < W * H; i++) { const m = Math.exp(logA[i] - avg[i]); r2[i] = m * Math.cos(ph[i]); i2[i] = m * Math.sin(ph[i]); }
  const [BR, BI] = dft(r2, i2, true);
  const sal = new Float32Array(W * H);
  for (let i = 0; i < W * H; i++) sal[i] = BR[i] ** 2 + BI[i] ** 2;
  const bl = gaussian({ w: W, h: H, data: sal }, 2.5);
  let mn = Infinity, mx = -Infinity;
  for (const v of bl.data) { if (v < mn) mn = v; if (v > mx) mx = v; }
  for (let i = 0; i < bl.data.length; i++) bl.data[i] = mx - mn > 1e-12 ? (bl.data[i] - mn) / (mx - mn) : 0;
  return bl;
}

export function composition(img: RGBA) {
  const sal = saliency(img);
  const { w: sw, h: sh, data } = sal;
  let wsum = 0, cx = 0, cy = 0;
  for (let y = 0; y < sh; y++) for (let x = 0; x < sw; x++) { const wt = Math.max(0, data[y * sw + x] - 0.25); wsum += wt; cx += x * wt; cy += y * wt; }
  cx = wsum > 1e-6 ? cx / wsum / sw : 0.5; cy = wsum > 1e-6 ? cy / wsum / sh : 0.5;
  const dThird = Math.min(...[1 / 3, 2 / 3].flatMap((a) => [1 / 3, 2 / 3].map((b) => Math.hypot(cx - a, cy - b))));
  const placement = dThird < 0.09 ? "on a rule-of-thirds intersection" : Math.hypot(cx - 0.5, cy - 0.5) < 0.08 ? "centred" : "off-centre";
  const neg = Array.prototype.filter.call(data, (v: number) => v < 0.15).length / data.length;

  const small = shrink(img, 320), g = toGray(small);
  const { gx, gy } = sobel(g);
  const mag = new Float32Array(g.w * g.h);
  for (let i = 0; i < mag.length; i++) mag[i] = Math.hypot(gx[i], gy[i]);
  const thr = percentile(mag, 90);
  // symmetry: compare edge map with its mirror
  let diff = 0, tot = 0;
  for (let y = 0; y < g.h; y++) for (let x = 0; x < g.w; x++) {
    const e = mag[y * g.w + x] > thr ? 1 : 0, m = mag[y * g.w + (g.w - 1 - x)] > thr ? 1 : 0;
    diff += Math.abs(e - m); tot += e;
  }
  const sym = Math.max(0, Math.min(1, 1 - diff / (tot * 2 + 1e-6)));
  // horizon roll from strong near-horizontal edges (gradient pointing vertically)
  const angles: number[] = [];
  for (let i = 0; i < mag.length; i++) {
    if (mag[i] < thr * 1.5) continue;
    const ang = (Math.atan2(gy[i], gx[i]) * 180) / Math.PI;           // gradient direction
    const edge = ang - 90;                                             // edge runs perpendicular
    const n = ((edge + 90) % 180 + 180) % 180 - 90;
    if (Math.abs(n) < 12) angles.push(n);
  }
  const roll = angles.length > g.w * 0.6 ? median(angles) : null;
  // depth-of-field cue: sharpness of salient vs low-salience areas
  const lap = laplacianAbs(g);
  const salUp = (x: number, y: number) => data[Math.min(sh - 1, Math.floor((y * sh) / g.h)) * sw + Math.min(sw - 1, Math.floor((x * sw) / g.w))];
  let sS = 0, nS = 0, sB = 0, nB = 0;
  const bands = [0, 0, 0], bandN = [0, 0, 0];
  for (let y = 1; y < g.h - 1; y++) for (let x = 1; x < g.w - 1; x++) {
    const v = lap[y * g.w + x], s = salUp(x, y);
    if (s > 0.5) { sS += v; nS++; } else if (s < 0.15) { sB += v; nB++; }
    const b = Math.min(2, Math.floor((3 * y) / g.h)); bands[b] += v; bandN[b]++;
  }
  const sharpSal = nS ? sS / nS : 0, sharpBg = nB ? sB / nB : 0;
  const sep = sharpBg > 0 ? sharpSal / (sharpBg + 1e-6) : 1;
  const dof = sep > 2.2 ? "shallow (subject sharper than background)" : sep < 1.3 ? "deep / everything similarly sharp" : "moderate";
  return {
    subject_centroid: tag({ x: +cx.toFixed(3), y: +cy.toFixed(3) }, 0.5, "estimated", "Visual-attention centroid (saliency), not a detected person."),
    placement: tag(placement, 0.5, "estimated"),
    negative_space: tag(+neg.toFixed(3), 0.55, "estimated", "Share of the frame with low visual saliency."),
    symmetry: tag(+sym.toFixed(3), 0.6, "observed", "Left–right edge mirror similarity (1 = perfectly symmetric)."),
    leading_lines: tag(null, 0.1, "estimated", "Not measured in the browser edition."),
    vanishing_point: tag(null, 0.1, "estimated", "Not measured in the browser edition."),
    perspective: tag("not measured in the browser edition", 0.1, "estimated"),
    horizon_roll_deg: tag(roll === null ? null : +roll.toFixed(2), roll === null ? 0.1 : 0.45, "estimated",
      "Positive = clockwise tilt of near-horizontal edges."),
    camera_tilt: tag("undetermined", 0.1, "estimated"),
    depth_of_field: tag(dof, 0.4, "estimated", "Sharpness of salient vs low-salience regions."),
    depth_layers: tag({ top_detail: +(bands[0] / (bandN[0] || 1)).toFixed(2), middle_detail: +(bands[1] / (bandN[1] || 1)).toFixed(2),
      bottom_detail: +(bands[2] / (bandN[2] || 1)).toFixed(2) }, 0.35, "estimated", "Edge detail per horizontal band; lower bands are usually foreground."),
    _saliency: sal,
  };
}

// ---------------------------------------------------------------- environment
function skyAndFoliage(img: RGBA) {
  const s = shrink(img, 160), g = toGray(s), lap = laplacianAbs(g), top = Math.floor(s.h / 3);
  let blue = 0, white = 0, warm = 0, fol = 0, nTop = 0;
  for (let y = 0; y < s.h; y++) for (let x = 0; x < s.w; x++) {
    const i = (y * s.w + x) * 4, [H, S8, V8] = hsvPixel(s.data[i], s.data[i + 1], s.data[i + 2]), S = S8 / 255, V = V8 / 255;
    if (H > 33 && H < 85 && S > 0.25 && V > 0.15) fol++;
    if (y < top) {
      nTop++;
      const smooth = lap[y * s.w + x] < 6;
      if (!smooth) continue;
      if (H > 95 && H < 130 && S > 0.15 && V > 0.45) blue++;
      else if (S < 0.12 && V > 0.8) white++;
      else if ((H < 25 || H > 160) && S > 0.25 && V > 0.5) warm++;
    }
  }
  return { blue_sky: blue / nTop, white_sky: white / nTop, warm_sky: warm / nTop, foliage: fol / (s.w * s.h) };
}

function practicals(img: RGBA) {
  const s = shrink(img, 320), n = s.w * s.h, y = new Float32Array(n);
  for (let i = 0; i < n; i++) y[i] = 0.299 * s.data[i * 4] + 0.587 * s.data[i * 4 + 1] + 0.114 * s.data[i * 4 + 2];
  if (percentile(y, 50) > 170) return [];
  const thr = Math.max(235, percentile(y, 99.6));
  const seen = new Uint8Array(n), out: { x: number; y: number; size: number; tint: string }[] = [];
  for (let i = 0; i < n; i++) {
    if (seen[i] || y[i] < thr) continue;
    const stack = [i]; seen[i] = 1;
    let area = 0, sx = 0, sy = 0, minx = s.w, maxx = 0, miny = s.h, maxy = 0, r = 0, b = 0;
    while (stack.length) {
      const k = stack.pop()!, kx = k % s.w, ky = (k / s.w) | 0;
      area++; sx += kx; sy += ky; r += s.data[k * 4]; b += s.data[k * 4 + 2];
      minx = Math.min(minx, kx); maxx = Math.max(maxx, kx); miny = Math.min(miny, ky); maxy = Math.max(maxy, ky);
      for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
        const nx = kx + dx, ny = ky + dy, j = ny * s.w + nx;
        if (nx >= 0 && ny >= 0 && nx < s.w && ny < s.h && !seen[j] && y[j] >= thr) { seen[j] = 1; stack.push(j); }
      }
    }
    const share = area / n;
    if (share > 0.00015 && share < 0.02 && maxx - minx < s.w * 0.25 && maxy - miny < s.h * 0.3) {
      r /= area; b /= area;
      out.push({ x: +(sx / area / s.w).toFixed(3), y: +(sy / area / s.h).toFixed(3), size: +share.toFixed(4), tint: r > b + 12 ? "warm" : b > r + 12 ? "cool" : "neutral" });
    }
  }
  return out.sort((a, b) => b.size - a.size).slice(0, 6);
}

export function environment(frames: RGBA[], meanLum: number) {
  const feats = frames.map(skyAndFoliage);
  const avg = (f: (x: ReturnType<typeof skyAndFoliage>) => number) => feats.reduce((s, x) => s + f(x), 0) / feats.length;
  const sky = avg((f) => f.blue_sky + f.white_sky + f.warm_sky), warmSky = avg((f) => f.warm_sky), fol = avg((f) => f.foliage);
  const pr = frames.flatMap(practicals), nPr = pr.length / Math.max(1, frames.length);
  const score = 2.2 * sky + 1.2 * fol - 0.25 * Math.min(nPr, 3);
  let env = "uncertain", ec = 0.3;
  if (score > 0.35) { env = "outdoor"; ec = Math.min(0.8, 0.45 + score / 2); }
  else if (score < 0.08 && (nPr >= 0.5 || meanLum < 0.5)) { env = "indoor"; ec = 0.45 + (nPr >= 0.5 ? 0.15 : 0); }
  let tod = "uncertain", tc = 0.2;
  if (meanLum < 0.2 && sky < 0.05) { tod = "night"; tc = 0.5 + (nPr >= 0.5 ? 0.15 : 0); }
  else if (warmSky > 0.06 && meanLum < 0.55) { tod = "dawn / dusk (golden or blue hour)"; tc = 0.45; }
  else if (sky > 0.08 && meanLum > 0.4) { tod = "day"; tc = 0.6; }
  else if (env === "indoor") { tod = "uncertain (interior)"; tc = 0.25; }
  const note = "Rule-based from sky/foliage colour, brightness and light sources; grading can mislead it.";
  const seen = new Map<string, (typeof pr)[0]>();
  for (const p of pr) { const k = `${p.x.toFixed(1)},${p.y.toFixed(1)}`; if (!seen.has(k)) seen.set(k, p); }
  const list = [...seen.values()].slice(0, 6);
  return {
    setting: tag(env, ec, "heuristic", note), time_of_day: tag(tod, tc, "heuristic", note),
    practical_lights: tag(list, list.length ? 0.4 : 0.3, "estimated", "Small very bright regions in an otherwise darker frame (lamps, screens, windows)."),
    cues: tag({ sky_fraction_top: +sky.toFixed(3), foliage_fraction: +fol.toFixed(3), practicals_per_frame: +nPr.toFixed(2) }, 0.8, "observed"),
  };
}

// ---------------------------------------------------------------- narrative
export function narrative(o: { shots: { start_s: number; end_s: number }[]; duration: number; motion: any[]; tone: any; people: number; env: any; comps: any[] }) {
  const n = o.shots.length, avgShot = o.duration / Math.max(1, n);
  const cvs = o.motion.filter((m) => m.camera_vs_subject).map((m) => m.camera_vs_subject.value);
  const mean = (a: number[]) => (a.length ? a.reduce((s, x) => s + x, 0) / a.length : 0);
  const energy = mean(cvs.map((c) => c.camera_motion)) + mean(cvs.map((c) => c.subject_motion));
  const handheld = o.motion.some((m) => m.handheld?.value);
  const ml = o.tone.mean_luminance.value, con = o.tone.contrast.value.range_p5_p95, cast = o.tone.color_temperature.value.cast, sat = o.tone.saturation.value.mean;
  const neg = mean(o.comps.map((c) => c.negative_space.value));
  let pacing = avgShot < 1.5 ? "rapid cutting" : avgShot < 5 ? "measured" : "long take / unhurried";
  if (energy > 0.25) pacing += ", high on-screen energy"; else if (energy < 0.04) pacing += ", very still";
  const moods: string[] = [];
  if (ml < 0.3 && con > 0.55) moods.push("tense / mysterious");
  if (ml < 0.3 && cast === "cool") moods.push("melancholic / cold");
  if (ml > 0.55 && cast === "warm") moods.push("warm / inviting");
  if (ml > 0.6 && con < 0.5) moods.push("light / airy");
  if (sat < 0.18) moods.push("restrained / desaturated");
  if (handheld || energy > 0.25) moods.push("urgent / immediate");
  if (neg > 0.6 && o.people <= 1) moods.push("isolated / contemplative");
  if (!moods.length) moods.push("neutral / observational");
  const purpose = o.people === 0 && avgShot > 3 ? "establishing the space" : energy > 0.25 ? "action or movement beat" : "scene coverage";
  return {
    pacing: tag({ label: pacing, shots: n, average_shot_s: +avgShot.toFixed(2), motion_energy: +energy.toFixed(3) }, 0.6, "estimated"),
    mood: tag(moods, 0.35, "heuristic", "Inferred from light, colour and movement only. Add your intended emotion in the director's brief."),
    spatial_relationships: tag("People aren't detected in the browser edition, so spatial relationships aren't measured.", 0.1, "estimated"),
    apparent_purpose: tag(purpose, 0.25, "heuristic", "A guess from visual structure. The brief's scene purpose overrides this."),
    setting_summary: tag(`${o.env.setting.value}, ${o.env.time_of_day.value}`, 0.3, "heuristic"),
  };
}
