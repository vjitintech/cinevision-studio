// Decode a clip with the browser's own <video> element and pull frames into canvases. Nothing leaves the page.
import type { RGBA } from "./analysis/img";

export class VideoError extends Error { constructor(public code: string, msg: string) { super(msg); } }

export const LIMITS = { max_upload_mb: 500, max_duration_s: 300, max_width: 4096, max_height: 4096, min_dimension: 64,
  extensions: [".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"] };

function load(url: string): Promise<HTMLVideoElement> {
  return new Promise((resolve, reject) => {
    const v = document.createElement("video");
    v.muted = true; v.playsInline = true; v.preload = "auto"; v.crossOrigin = "anonymous";
    const t = setTimeout(() => reject(new VideoError("decode_failed", "The browser took too long to open this file.")), 30000);
    v.onloadeddata = () => { clearTimeout(t); resolve(v); };
    v.onerror = () => { clearTimeout(t); reject(new VideoError("unsupported_codec", "Your browser can't play this file. Try an MP4 (H.264) or WebM export.")); };
    v.src = url;
  });
}

function seek(v: HTMLVideoElement, t: number): Promise<void> {
  return new Promise((resolve) => {
    const done = () => { v.removeEventListener("seeked", done); resolve(); };
    v.addEventListener("seeked", done);
    v.currentTime = Math.min(Math.max(0, t), Math.max(0, v.duration - 0.001));
  });
}

export async function probe(file: File) {
  const ext = (file.name.match(/\.[^.]+$/)?.[0] || "").toLowerCase();
  if (!LIMITS.extensions.includes(ext)) throw new VideoError("unsupported_type", `Unsupported file type '${ext || "none"}'. Use MP4, MOV, M4V, MKV, WEBM or AVI.`);
  if (file.size > LIMITS.max_upload_mb * 1024 * 1024) throw new VideoError("too_large", `Clip exceeds the ${LIMITS.max_upload_mb} MB limit.`);
  const url = URL.createObjectURL(file);
  try {
    const v = await load(url);
    const w = v.videoWidth, h = v.videoHeight, d = v.duration;
    if (!w || !h) throw new VideoError("unsupported_codec", "No picture could be decoded. Try an MP4 (H.264) export.");
    if (w < LIMITS.min_dimension || h < LIMITS.min_dimension) throw new VideoError("bad_resolution", `Resolution ${w}×${h} is too small.`);
    if (w > LIMITS.max_width || h > LIMITS.max_height) throw new VideoError("bad_resolution", `Resolution ${w}×${h} exceeds the ${LIMITS.max_width}×${LIMITS.max_height} limit.`);
    if (!Number.isFinite(d) || d <= 0.2) throw new VideoError("bad_duration", "The clip is too short or its duration is unreadable.");
    if (d > LIMITS.max_duration_s) throw new VideoError("bad_duration", `Clip is ${Math.round(d)}s; the limit is ${LIMITS.max_duration_s}s.`);
    return { container: ext.slice(1), video_codec: "decoded by browser", audio_codec: null, width: w, height: h, display_aspect: +(w / h).toFixed(4),
      fps: null as number | null, duration_s: +d.toFixed(3), bitrate: Math.round((file.size * 8) / d), pix_fmt: null, rotation: 0, frame_count: null };
  } finally { URL.revokeObjectURL(url); }
}

function grab(v: HTMLVideoElement, ctx: CanvasRenderingContext2D, w: number): RGBA {
  const h = Math.round((w * v.videoHeight) / v.videoWidth);
  ctx.canvas.width = w; ctx.canvas.height = h;
  ctx.drawImage(v, 0, 0, w, h);
  const d = ctx.getImageData(0, 0, w, h);
  return { w, h, data: d.data };
}

/** Samples for motion analysis (~8 fps, ≤240) and keyframes (~2 fps, ≤60, also saved as JPEGs). */
export async function extract(blob: Blob, duration: number, progress: (f: number) => void, cancelled: () => boolean) {
  const url = URL.createObjectURL(blob);
  try {
    const v = await load(url);
    const fps = Math.min(8, 240 / Math.max(duration, 0.5));
    const n = Math.max(2, Math.floor(duration * fps));
    const every = Math.max(1, Math.round(fps / Math.min(2, 60 / Math.max(duration, 0.5))));
    const c1 = document.createElement("canvas").getContext("2d", { willReadFrequently: true })!;
    const c2 = document.createElement("canvas").getContext("2d", { willReadFrequently: true })!;
    const samples: { t: number; img: RGBA }[] = [], keyframes: { t: number; img: RGBA; jpeg: Blob }[] = [];
    for (let i = 0; i < n; i++) {
      if (cancelled()) throw new Error("cancelled");
      const t = (i / fps);
      await seek(v, t);
      samples.push({ t, img: grab(v, c1, 320) });
      if (i % every === 0 && keyframes.length < 60) {
        const img = grab(v, c2, 640);
        const jpeg: Blob = await new Promise((r) => c2.canvas.toBlob((b) => r(b!), "image/jpeg", 0.82));
        keyframes.push({ t, img, jpeg });
      }
      progress((i + 1) / n);
    }
    return { samples, keyframes };
  } finally { URL.revokeObjectURL(url); }
}
