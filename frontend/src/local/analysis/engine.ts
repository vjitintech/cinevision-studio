// In-browser scene analysis orchestrator (port of backend/cinevision/analysis/engine.py).
import { toGray, RGBA } from "./img";
import { classify, detectShots, histOf, pairMotion, PairMotion, Sample } from "./motion";
import { composition, environment, narrative, palette, shrink, toneAndColor } from "./scene";
import { tag } from "./tag";

export const ENGINE = { id: "browser-scene", version: "1.0", detectors: ["block-matching+ransac", "spectral-residual"], analysis_fps: 8 };

export interface Keyframe { t: number; img: RGBA }
export interface Meta { duration_s: number; width: number; height: number; display_aspect: number; fps: number | null; video_codec: string }

const yieldUI = () => new Promise((r) => setTimeout(r, 0));

function sizeFromSaliency(share: number) {
  const size = share > 0.45 ? "close / insert (large single subject)" : share < 0.15 ? "wide or establishing" : "undetermined";
  return tag(size, 0.2, "heuristic", "Person detection isn't available in the browser edition, so this is based only on how much of the frame draws attention.");
}

export async function run(samplesIn: { t: number; img: RGBA }[], keyframes: Keyframe[], meta: Meta, progress: (p: number, stage: string) => void, cancelled: () => boolean) {
  const duration = meta.duration_s, aspect = meta.display_aspect || meta.width / meta.height;
  progress(0.05, "Preparing frames");
  const samples: Sample[] = samplesIn.map((s) => { const small = shrink(s.img, 320); return { t: s.t, gray: toGray(small), hist: histOf(small), rgba: small }; });
  progress(0.2, "Detecting shot boundaries");
  const shots = detectShots(samples, duration);

  progress(0.25, "Estimating camera motion");
  const shotMotion = [];
  for (const [si, sh] of shots.entries()) {
    const sm = samples.filter((s) => s.t >= sh.start_s && s.t < sh.end_s);
    const pairs: PairMotion[] = [];
    for (let i = 1; i < sm.length; i++) {
      const pm = pairMotion(sm[i - 1], sm[i]);
      if (pm) pairs.push(pm);
      if (i % 4 === 0) {
        progress(0.25 + 0.4 * ((si + i / sm.length) / shots.length), "Estimating camera motion");
        await yieldUI();
        if (cancelled()) throw new Error("cancelled");
      }
    }
    shotMotion.push(classify(pairs));
  }

  progress(0.68, "Analysing colour and exposure");
  await yieldUI();
  const kfImgs = keyframes.length ? keyframes.map((k) => k.img) : samples.map((s) => s.rgba);
  const pick = kfImgs.filter((_, i) => i % Math.max(1, Math.floor(kfImgs.length / 24)) === 0);
  const tone = toneAndColor(pick);
  const pal = palette(pick);

  progress(0.78, "Reading composition");
  const perShot = [];
  for (const [si, sh] of shots.entries()) {
    await yieldUI();
    const mid = (sh.start_s + sh.end_s) / 2;
    const inShot = keyframes.filter((k) => k.t >= sh.start_s && k.t < sh.end_s);
    const pool = inShot.length ? inShot : keyframes;
    const rep = pool.reduce((b, k) => (Math.abs(k.t - mid) < Math.abs(b.t - mid) ? k : b), pool[0]);
    const comp = composition(rep.img);
    const sal = comp._saliency;
    delete (comp as Partial<typeof comp>)._saliency;
    const share = Array.prototype.filter.call(sal.data, (v: number) => v > 0.35).length / sal.data.length;
    const roll = comp.horizon_roll_deg.value;
    const angle = roll !== null && Math.abs(roll) > 6 ? ["dutch angle", 0.45] : ["undetermined (assume eye level)", 0.15];
    perShot.push({
      ...sh, representative_t: +rep.t.toFixed(3),
      shot_size: sizeFromSaliency(share),
      camera_angle: tag(angle[0] as string, angle[1] as number, "estimated", "Camera tilt isn't measured in the browser edition; only a strong roll is detected."),
      camera_height: tag("about eye level", 0.1, "estimated", "Absolute height cannot be recovered from 2D video."),
      movement: shotMotion[si], composition: comp,
      subjects: { count: tag(0, 0.1, "estimated", "Person detection isn't available in the browser edition."), positions: tag([], 0.1, "estimated") },
      scene_geometry: tag({ assumed_hfov_deg: 55, subjects_m: [] }, 0.1, "estimated", "No people detected, so the previs uses one default subject 3 m from camera."),
    });
    progress(0.78 + 0.12 * (si + 1) / shots.length, "Reading composition");
  }

  progress(0.92, "Reading environment and mood");
  await yieldUI();
  const env = environment(pick, tone.mean_luminance.value);
  const subjects = { count: tag(0, 0.1, "estimated", "Person detection isn't available in the browser edition."), max_visible: tag(0, 0.1, "estimated"),
    positions: tag([], 0.1, "estimated"), face_detection: tag("not available in browser edition", 1, "observed") };
  const narr = narrative({ shots, duration, motion: shotMotion, tone, people: 0, env, comps: perShot.map((s) => s.composition) });
  return {
    engine: ENGINE,
    clip: { duration_s: tag(duration, 1, "observed"), resolution: tag(`${meta.width}×${meta.height}`, 1, "observed"),
      fps: tag(meta.fps ?? "not readable in browser", meta.fps ? 1 : 0.2, "observed"), aspect: tag(+aspect.toFixed(3), 1, "observed"),
      codec: tag(meta.video_codec, 1, "observed") },
    shots: perShot,
    continuity: tag({ shot_count: shots.length, cuts_at_s: shots.slice(1).map((s) => s.start_s), composition_changes: [] as string[] }, 0.65, "estimated",
      "Hard cuts are detected from colour-histogram changes; dissolves may be missed."),
    color: { ...tone, palette: tag(pal, 0.9, "observed", "Dominant colours (k-means in Lab) with screen share.") },
    environment: env, subjects, narrative: narr,
    limitations: [
      "Exact camera body, lens, focal length and lighting equipment cannot be recovered from video.",
      "Values marked 'estimated' come from models that can be wrong; 'heuristic' values are interpretations.",
      "Browser edition: people and faces aren't detected, and camera tilt, leading lines and lens perspective aren't measured. The self-hosted edition adds these.",
    ],
  };
}
