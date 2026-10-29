import type { ConceptData, Lighting, Scene, Shot } from "../types";
import { buildScene, worldLights, negativeFillDirs, Face, RenderOpts, Line3 } from "./render";
import { sampleCamera, CamState, V3, cctToRgb, aspectOf, orbit, distanceFor, ANGLES, SUBJECT_REF_HEIGHT } from "./math";

export interface CameraState {
  size: string; angle: string; camera_height_m: number; azimuth_deg: number; distance_m: number; dutch_roll_deg: number;
  lens_ff_mm: number; movement: string; speed_m_s: number; focus?: string; composition?: string;
  camera_path: { keyframes: { t: number; pos: number[]; target: number[]; focal_mm: number }[] }; derived?: boolean;
}

export function subjectFor(scene: Scene, shot: Shot) {
  return scene.subjects.find((s) => s.id === shot.subject_target) || scene.subjects[0];
}

/** Camera at normalised time t. `offset` shifts the path when the target subject was restaged in the 3D view. */
export function camAt(shot: Shot, t: number, cam?: CameraState | null, offset: [number, number] = [0, 0]): CamState {
  const keys = cam?.camera_path?.keyframes || shot.camera_path.keyframes;
  const c = sampleCamera(keys, t, cam?.dutch_roll_deg ?? shot.dutch_roll_deg ?? 0);
  if (!offset[0] && !offset[1]) return c;
  return { ...c, pos: [c.pos[0] + offset[0], c.pos[1], c.pos[2] + offset[1]], target: [c.target[0] + offset[0], c.target[1], c.target[2] + offset[1]] };
}

export function subjectOffset(base: Scene, current: Scene | null, shot: Shot): [number, number] {
  if (!current || current === base) return [0, 0];
  const a = base.subjects.find((s) => s.id === shot.subject_target) || base.subjects[0];
  const b = current.subjects.find((s) => s.id === a?.id);
  return a && b ? [b.x - a.x, b.z - a.z] : [0, 0];
}

export function lightingOpts(scene: Scene, shot: Shot, lighting?: Lighting | null): Pick<RenderOpts, "lights" | "ambient" | "haze" | "practicals" | "negative"> {
  const L = lighting || shot.lighting;
  const subj = subjectFor(scene, shot);
  const lights = worldLights(L.sources, subj, L.haze);
  const amb = L.ambient?.level === "low" ? 0.12 : L.ambient?.level === "high" ? 0.32 : 0.2;
  return {
    lights, ambient: amb, haze: L.haze, negative: negativeFillDirs(L.sources, subj),
    practicals: scene.set.practicals.map((p) => ({ pos: [p.x, p.height, p.z] as V3, color: cctToRgb(p.tint === "cool" ? 6500 : 2700) })),
  };
}

export function negDirs(scene: Scene, shot: Shot, lighting?: Lighting | null) {
  return negativeFillDirs((lighting || shot.lighting).sources, subjectFor(scene, shot));
}

const faceCache = new WeakMap<Scene, Face[]>();
export function sceneFaces(scene: Scene): Face[] {
  let f = faceCache.get(scene);
  if (!f) { f = buildScene(scene); faceCache.set(scene, f); }
  return f;
}

export function pathLines(shot: Shot, color = "#e3a453", cam?: CameraState | null, offset: [number, number] = [0, 0]): Line3[] {
  const pts: V3[] = [];
  for (let i = 0; i <= 24; i++) pts.push(camAt(shot, i / 24, cam, offset).pos);
  return pts.slice(1).map((p, i) => ({ a: pts[i], b: p, color, width: 2 }));
}

export function aspect(c: ConceptData) {
  return aspectOf(c.aspect_ratio || "16:9");
}

/** Recompute a camera path from planner controls (mirrors backend builder.camera_path for common moves). */
export function rebuildPath(shot: Shot, scene: Scene, st: CameraState, sensor: string): CameraState {
  const subj = subjectFor(scene, shot);
  const target: V3 = [subj.x, (SUBJECT_REF_HEIGHT[st.size] ?? 1.35) * subj.height / 1.7, subj.z];
  const dist = distanceFor(st.size, st.lens_ff_mm, sensor);
  const pos = orbit(target, st.azimuth_deg, dist, st.camera_height_m, subj.facing_deg);
  const crop = { full_frame: 1, super35: 1.45, micro_four_thirds: 2, phone: 5.6 }[sensor] || 1.45;
  const focal = st.lens_ff_mm / crop;
  const m = st.movement.toLowerCase();
  const D = st.speed_m_s * shot.duration_s;
  const fwd = (() => { const d: V3 = [target[0] - pos[0], target[1] - pos[1], target[2] - pos[2]]; const l = Math.hypot(...d) || 1; return d.map((x) => x / l) as V3; })();
  let keys: { t: number; pos: number[]; target: number[]; focal_mm: number }[];
  const K = (t: number, p: V3, tg: V3 = target) => ({ t, pos: p.map((v) => +v.toFixed(3)), target: tg.map((v) => +v.toFixed(3)), focal_mm: +focal.toFixed(1) });
  if (/push|dolly[ -]?in|creep/.test(m)) keys = [K(0, pos.map((v, i) => v - fwd[i] * D / 2) as V3), K(1, pos.map((v, i) => v + fwd[i] * D / 2) as V3)];
  else if (/pull|dolly out/.test(m)) keys = [K(0, pos.map((v, i) => v + fwd[i] * D / 2) as V3), K(1, pos.map((v, i) => v - fwd[i] * D / 2) as V3)];
  else if (/lateral|slide|track|drift/.test(m) && !/handheld/.test(m)) {
    const side: V3 = [fwd[2], 0, -fwd[0]];
    keys = [K(0, pos.map((v, i) => v - side[i] * D / 2) as V3), K(1, pos.map((v, i) => v + side[i] * D / 2) as V3)];
  } else if (/arc|orbit/.test(m)) {
    const sweep = Math.min(60, (D / Math.max(dist, 0.5)) * 57.3);
    keys = [0, 0.25, 0.5, 0.75, 1].map((t) => K(t, orbit(target, st.azimuth_deg - sweep / 2 + sweep * t, dist, st.camera_height_m, subj.facing_deg)));
  } else if (/crane|rise/.test(m)) keys = [K(0, [pos[0], Math.max(0.4, pos[1] - 0.8), pos[2]]), K(1, [pos[0], pos[1] + 1.2, pos[2]])];
  else if (/pan|whip/.test(m)) keys = [K(0, pos, [target[0] - (m.includes("whip") ? 1.2 : 0.5), target[1], target[2]]), K(1, pos)];
  else if (/handheld|follow|breathing/.test(m)) {
    const amp = m.includes("breathing") ? 0.03 : 0.08;
    keys = [0, 1, 2, 3, 4, 5, 6].map((i) => K(i / 6, [pos[0] + amp * Math.sin(i * 2.1), pos[1] + amp * 0.6 * Math.cos(i * 1.7), pos[2] + amp * Math.cos(i * 2.9)],
      [target[0] + amp * Math.sin(i * 1.3), target[1], target[2]]));
  } else keys = [K(0, pos), K(1, pos)];
  return { ...st, distance_m: +dist.toFixed(2), camera_path: { keyframes: keys } };
}

export const angleHeight = (angle: string) => (ANGLES[angle] || ANGLES["eye level"])[0];
