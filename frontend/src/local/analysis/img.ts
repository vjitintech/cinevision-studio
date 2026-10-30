// Minimal image operations for in-browser analysis (replaces the OpenCV calls used by the server edition).
export interface RGBA { w: number; h: number; data: Uint8ClampedArray }
export interface Gray { w: number; h: number; data: Float32Array }

export function toGray(img: RGBA): Gray {
  const out = new Float32Array(img.w * img.h);
  for (let i = 0, j = 0; i < out.length; i++, j += 4) out[i] = 0.299 * img.data[j] + 0.587 * img.data[j + 1] + 0.114 * img.data[j + 2];
  return { w: img.w, h: img.h, data: out };
}

/** OpenCV-compatible 8-bit HSV: H 0..180, S 0..255, V 0..255. */
export function hsvPixel(r: number, g: number, b: number): [number, number, number] {
  const mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn;
  const s = mx === 0 ? 0 : (255 * d) / mx;
  let h = 0;
  if (d > 0) {
    if (mx === r) h = (60 * (g - b)) / d;
    else if (mx === g) h = 120 + (60 * (b - r)) / d;
    else h = 240 + (60 * (r - g)) / d;
    if (h < 0) h += 360;
  }
  return [h / 2, s, mx];
}

/** OpenCV-compatible 8-bit Lab (D65): L 0..255, a/b offset by 128. */
export function labPixel(r: number, g: number, b: number): [number, number, number] {
  const lin = (c: number) => { c /= 255; return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };
  const R = lin(r), G = lin(g), B = lin(b);
  const X = (0.412453 * R + 0.35758 * G + 0.180423 * B) / 0.950456;
  const Y = 0.212671 * R + 0.71516 * G + 0.072169 * B;
  const Z = (0.019334 * R + 0.119193 * G + 0.950227 * B) / 1.088754;
  const f = (t: number) => (t > 0.008856 ? Math.cbrt(t) : 7.787 * t + 16 / 116);
  const L = Y > 0.008856 ? 116 * Math.cbrt(Y) - 16 : 903.3 * Y;
  return [(L * 255) / 100, 500 * (f(X) - f(Y)) + 128, 200 * (f(Y) - f(Z)) + 128];
}

export function labToRgb(L: number, a: number, b: number): [number, number, number] {
  L = (L * 100) / 255; a -= 128; b -= 128;
  const fy = (L + 16) / 116, fx = fy + a / 500, fz = fy - b / 200;
  const inv = (t: number) => (t ** 3 > 0.008856 ? t ** 3 : (t - 16 / 116) / 7.787);
  const X = inv(fx) * 0.950456, Y = inv(fy), Z = inv(fz) * 1.088754;
  const R = 3.240479 * X - 1.53715 * Y - 0.498535 * Z, G = -0.969256 * X + 1.875992 * Y + 0.041556 * Z, B = 0.055648 * X - 0.204043 * Y + 1.057311 * Z;
  const gam = (c: number) => Math.max(0, Math.min(255, Math.round(255 * (c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055))));
  return [gam(R), gam(G), gam(B)];
}

export function boxBlur(g: Gray, r: number): Gray {
  const { w, h } = g, tmp = new Float32Array(w * h), out = new Float32Array(w * h);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    let s = 0, n = 0;
    for (let k = -r; k <= r; k++) { const xx = Math.min(w - 1, Math.max(0, x + k)); s += g.data[y * w + xx]; n++; }
    tmp[y * w + x] = s / n;
  }
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    let s = 0, n = 0;
    for (let k = -r; k <= r; k++) { const yy = Math.min(h - 1, Math.max(0, y + k)); s += tmp[yy * w + x]; n++; }
    out[y * w + x] = s / n;
  }
  return { w, h, data: out };
}

/** Approximate Gaussian blur with three box passes. */
export const gaussian = (g: Gray, sigma: number) => {
  const r = Math.max(1, Math.round(sigma));
  return boxBlur(boxBlur(boxBlur(g, r), r), r);
};

export function laplacianAbs(g: Gray): Float32Array {
  const { w, h, data } = g, out = new Float32Array(w * h);
  for (let y = 1; y < h - 1; y++) for (let x = 1; x < w - 1; x++) {
    const i = y * w + x;
    out[i] = Math.abs(data[i - 1] + data[i + 1] + data[i - w] + data[i + w] - 4 * data[i]);
  }
  return out;
}

export function sobel(g: Gray) {
  const { w, h, data } = g, gx = new Float32Array(w * h), gy = new Float32Array(w * h);
  for (let y = 1; y < h - 1; y++) for (let x = 1; x < w - 1; x++) {
    const i = y * w + x;
    gx[i] = data[i - w + 1] + 2 * data[i + 1] + data[i + w + 1] - data[i - w - 1] - 2 * data[i - 1] - data[i + w - 1];
    gy[i] = data[i + w - 1] + 2 * data[i + w] + data[i + w + 1] - data[i - w - 1] - 2 * data[i - w] - data[i - w + 1];
  }
  return { gx, gy };
}

export function resizeGray(g: Gray, w: number, h: number): Gray {
  const out = new Float32Array(w * h), sx = g.w / w, sy = g.h / h;
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    // area average
    const x0 = Math.floor(x * sx), x1 = Math.max(x0 + 1, Math.floor((x + 1) * sx)), y0 = Math.floor(y * sy), y1 = Math.max(y0 + 1, Math.floor((y + 1) * sy));
    let s = 0, n = 0;
    for (let yy = y0; yy < y1 && yy < g.h; yy++) for (let xx = x0; xx < x1 && xx < g.w; xx++) { s += g.data[yy * g.w + xx]; n++; }
    out[y * w + x] = n ? s / n : 0;
  }
  return { w, h, data: out };
}

export function percentile(arr: ArrayLike<number>, q: number) {
  const a = Float32Array.from(arr).sort();
  if (!a.length) return 0;
  const pos = (q / 100) * (a.length - 1), lo = Math.floor(pos), hi = Math.ceil(pos);
  return a[lo] + (a[hi] - a[lo]) * (pos - lo);
}

export function median(arr: number[]) {
  if (!arr.length) return 0;
  const a = [...arr].sort((x, y) => x - y), m = a.length >> 1;
  return a.length % 2 ? a[m] : (a[m - 1] + a[m]) / 2;
}

/** Seeded PRNG so analysis is deterministic. */
export function rng(seed: number) {
  let s = seed >>> 0;
  return () => { s = (s + 0x6d2b79f5) >>> 0; let t = s; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; };
}
