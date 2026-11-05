// Blocking animation for the proxy people: each concept's staging note ("turns into the key light", "characters
// cross and re-cross"…) becomes a simple, readable motion over the shot. It illustrates the direction; it is not
// motion capture or a prediction of the actor's performance.
import type { Scene, Shot } from "../types";
import { environment, figure, Face } from "./render";
import { lerp } from "./math";

type Subj = Scene["subjects"][0];
const ease = (t: number) => t * t * (3 - 2 * t);
const window_ = (t: number, a: number, b: number) => ease(Math.min(1, Math.max(0, (t - a) / (b - a))));
const angleTo = (s: Subj, x: number, z: number) => (Math.atan2(x - s.x, z - s.z) * 180) / Math.PI;
const mixAngle = (a: number, b: number, k: number) => { const d = ((b - a + 540) % 360) - 180; return a + d * k; };

function keyLightPos(shot: Shot, s: Subj): [number, number] | null {
  const key = shot.lighting?.sources?.find((x) => x.role === "key");
  if (!key) return null;
  const yaw = ((s.facing_deg + (key.azimuth_deg || 0)) * Math.PI) / 180;
  const d = key.distance_m || 2;
  return [s.x + Math.sin(yaw) * d, s.z + Math.cos(yaw) * d];
}

/** Where everyone is at normalised time t (0–1) within the shot. `cam` = camera position for "turn to the lens". */
export function animateSubjects(scene: Scene, shot: Shot, t: number, cam?: [number, number, number]): Subj[] {
  const b = (shot.blocking || "").toLowerCase();
  const subs = scene.subjects;
  return subs.map((s0, i) => {
    const s = { ...s0 };
    const sway = Math.sin((t * shot.duration_s + i) * 1.7) * 1.5;      // idle breathing so nobody is a statue
    if (/turns into the key light/.test(b)) {
      const k = keyLightPos(shot, s0);
      if (k) s.facing_deg = mixAngle(s0.facing_deg, angleTo(s0, k[0], k[1]), 0.6 * window_(t, 0.45, 0.8));
    } else if (/toward the backlight|drift closer/.test(b)) {
      const away = (s0.facing_deg + 180) * Math.PI / 180;
      if (subs.length > 1) {                                             // drift towards the other person
        const o = subs[(i + 1) % subs.length];
        const k = 0.35 * window_(t, 0.1, 0.95);
        s.x = lerp(s0.x, o.x, k / 2); s.z = lerp(s0.z, o.z, k / 2);
      } else {
        const k = 0.7 * window_(t, 0.05, 0.95);
        s.x = s0.x + Math.sin(away) * k; s.z = s0.z + Math.cos(away) * k;
      }
    } else if (/keeps moving through the frame|cross and re-cross/.test(b)) {
      const side = (s0.facing_deg + 90) * Math.PI / 180;
      const k = subs.length > 1 ? Math.sin(t * Math.PI * 2) * 0.8 * (i % 2 ? 1 : -1) : (t - 0.5) * 2.2;
      s.x = s0.x + Math.sin(side) * k; s.z = s0.z + Math.cos(side) * k;
      s.facing_deg = s0.facing_deg + (k >= 0 ? 90 : -90) * 0.7;
    } else if (/something off-screen draws their look/.test(b)) {
      s.facing_deg = s0.facing_deg + 40 * window_(t, 0.55, 0.75);
    } else if (/exact mark in profile/.test(b)) {
      const fwd = s0.facing_deg * Math.PI / 180;
      const k = 1 - window_(t, 0, 0.5);
      s.x = s0.x - Math.sin(fwd) * k; s.z = s0.z - Math.cos(fwd) * k;
      s.facing_deg = s0.facing_deg + 90 * window_(t, 0.5, 0.7);
    } else if (/turns square to the lens|facing the lens/.test(b) && cam) {
      s.facing_deg = mixAngle(s0.facing_deg, angleTo(s0, cam[0], cam[2]), window_(t, 0.55, 0.8));
    } else if (/natural marks/.test(b)) {
      s.x = s0.x + Math.sin(t * Math.PI) * 0.3 * (i % 2 ? -1 : 1);
    }
    s.facing_deg += sway;
    return s;
  });
}

const envCache = new WeakMap<Scene, Face[]>();
/** Faces for the set (cached) plus the people at time t. */
export function animatedFaces(scene: Scene, shot: Shot, t: number, cam?: [number, number, number]): Face[] {
  let env = envCache.get(scene);
  if (!env) { env = environment(scene); envCache.set(scene, env); }
  return [...env, ...animateSubjects(scene, shot, t, cam).flatMap((s, i) => figure(s, i))];
}

/** Short label for what the people do in this shot (shown under animated previews). */
export function blockingLabel(shot: Shot): string {
  return (shot.blocking || "").split(".")[0];
}
