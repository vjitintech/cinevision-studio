import { useMemo, useState } from "react";
import { api, ApiError } from "../../api";
import { ErrorNote, Labeled } from "../../components/common";
import { PlanView, PlanMarker } from "../../previs/PlanView";
import { PrevisCanvas } from "../../previs/PrevisCanvas";
import { cctToRgb, deg, orbit, rad, rgbCss } from "../../previs/math";
import { aspect, camAt, lightingOpts, sceneFaces, subjectFor } from "../../previs/shot";
import type { LightSource, Lighting } from "../../types";
import { useWS } from "../Workspace";

const ROLE_LABEL: Record<string, string> = { key: "Key", fill: "Fill", backlight: "Backlight", rim: "Rim", practical: "Practical", negative_fill: "Negative fill" };
const sourceColor = (s: LightSource) => s.role === "negative_fill" ? "#3a3f47" : s.gel?.includes("blue") ? "#5d9de8" : rgbCss(cctToRgb(s.cct_k || 5600));

export const LightingSection = {
  Center() {
    const ws = useWS();
    const c = ws.concept, shot = ws.shot, scene = ws.scene;
    const L = ws.lighting.state;
    const faces = useMemo(() => (scene ? sceneFaces(scene) : []), [scene]);
    if (!c || !shot || !scene) return <div className="empty">Generate and open a concept to design its lighting.</div>;
    if (!L) return <div className="empty">Loading lighting plan…</div>;
    const subj = subjectFor(scene, shot);
    const head: [number, number, number] = [subj.x, 1.6, subj.z];
    const closeCam = { pos: orbit(head, 25, 1.25, 1.62, subj.facing_deg), target: head, focal: 60, roll: 0 };
    const lit = lightingOpts(scene, shot, L);
    const markers: PlanMarker[] = [
      ...scene.subjects.map((s) => ({ id: "subj-" + s.id, kind: "subject" as const, x: s.x, z: s.z, heading: s.facing_deg, label: s.label })),
      ...L.sources.map((s, i) => {
        const d = Math.min(s.distance_m, 6), a = rad(subj.facing_deg + s.azimuth_deg);
        const x = subj.x + d * Math.sin(a), z = subj.z + d * Math.cos(a);
        return { id: String(i), kind: "light" as const, x, z, heading: deg(Math.atan2(subj.x - x, subj.z - z)), color: sourceColor(s),
          label: ROLE_LABEL[s.role] || s.role, draggable: true, size: 9 };
      }),
    ];
    const cam = camAt(shot, 0.5, ws.camera.state, ws.shotOffset(shot));
    markers.push({ id: "cam", kind: "camera", x: cam.pos[0], z: cam.pos[2], heading: deg(Math.atan2(cam.target[0] - cam.pos[0], cam.target[2] - cam.pos[2])), fovDeg: 40, label: "camera" });
    const drag = (id: string, x: number, z: number) => {
      const i = Number(id);
      if (Number.isNaN(i)) return;
      const dx = x - subj.x, dz = z - subj.z;
      const az = ((deg(Math.atan2(dx, dz)) - subj.facing_deg + 540) % 360) - 180;
      const src = { ...L.sources[i], azimuth_deg: Math.round(az), distance_m: +Math.max(0.5, Math.hypot(dx, dz)).toFixed(2) };
      ws.lighting.setState({ ...L, sources: L.sources.map((s, j) => (j === i ? src : s)) });
    };
    return (
      <div className="stack">
        <div className="row"><h2 className="grow" style={{ margin: 0 }}>Lighting studio</h2><span className="muted small">Shot {shot.index + 1} · {L.setup}</span></div>
        <div className="notice warm small">Conceptual simulation: light direction, colour, softness and ratio are shown on simplified proxies. It is not a physically accurate render — there are no cast shadows or calibrated set geometry.</div>
        <div className="grid2">
          <div className="stage"><PrevisCanvas cam={closeCam} faces={faces} aspect={4 / 3} opts={{ sensor: "full_frame", ...lit }} label="Lighting on the subject's face" /><span className="tag">On the face</span></div>
          <div className="stage"><PrevisCanvas cam={cam} faces={faces} aspect={aspect(c.data)} opts={{ sensor: c.data.sensor, ...lit }} label="Lighting through the shot's lens" /><span className="tag">Through the lens</span></div>
        </div>
        <div>
          <div className="panel-h">Lighting diagram <span className="faint small">drag lights to reposition them</span></div>
          <PlanView scene={scene} markers={markers} onDrag={drag} height={320} />
        </div>
      </div>
    );
  },
  Right() {
    const ws = useWS();
    const [error, setError] = useState<string | null>(null);
    const [msg, setMsg] = useState<string | null>(null);
    const c = ws.concept, shot = ws.shot, L = ws.lighting.state;
    if (!c || !shot || !L) return null;
    const set = (patch: Partial<Lighting>) => ws.lighting.setState({ ...L, ...patch });
    const setSrc = (i: number, patch: Partial<LightSource>) => set({ sources: L.sources.map((s, j) => (j === i ? { ...s, ...patch } : s)) });
    const add = (role: string) => set({ sources: [...L.sources, { role, fixture: role === "practical" ? "Practical lamp" : "LED panel", azimuth_deg: role === "backlight" ? 170 : 60,
      elevation_deg: 30, distance_m: 2.5, intensity: role === "fill" ? 0.3 : 0.7, cct_k: role === "practical" ? 2700 : 4300, softness: 0.6, motivation: "added in lighting studio" }] });
    const save = async () => {
      setError(null);
      const keyI = L.sources.find((s) => s.role === "key")?.intensity || 1;
      const fill = L.sources.filter((s) => s.role === "fill").reduce((a, s) => a + s.intensity, 0);
      const ratio = +(Math.max(1, Math.min(32, (keyI + fill) / Math.max(0.03, fill || 0.06)))).toFixed(1);
      const next = { ...L, ratio, key_to_fill_ratio: `${ratio}:1 (~${Math.log2(ratio).toFixed(1)} stops)` };
      try { await api("PUT", `/api/concepts/${c.id}/lighting/${shot.index}`, next); ws.lighting.reset(next); setMsg("Saved. Shot lists generated from now on use this layout."); }
      catch (e) { setError(e instanceof ApiError ? e.message : "Save failed."); }
    };
    const reset = async () => { ws.lighting.reset(await api("DELETE", `/api/concepts/${c.id}/lighting/${shot.index}`)); setMsg(null); };
    return (
      <div>
        <div className="panel-h">Sources <span className="faint small">{L.key_to_fill_ratio}</span></div>
        {L.sources.map((s, i) => (
          <details key={i} className="fact" open={s.role === "key"}>
            <summary className="row" style={{ display: "flex" }}>
              <span style={{ width: 10, height: 10, borderRadius: 5, background: sourceColor(s), display: "inline-block" }} />
              <span className="grow" style={{ color: "var(--text)" }}>{ROLE_LABEL[s.role] || s.role}</span>
              <span className="small faint">{s.cct_k ? `${s.cct_k} K` : ""}</span>
            </summary>
            <div style={{ paddingTop: 8 }}>
              <div className="small muted" style={{ marginBottom: 8 }}>{s.fixture}{s.motivation ? ` — ${s.motivation}` : ""}</div>
              {s.role !== "negative_fill" && <>
                <Labeled label={`Intensity — ${Math.round(s.intensity * 100)}%`}><input type="range" min={0} max={1.5} step={0.01} value={s.intensity} onChange={(e) => setSrc(i, { intensity: +e.target.value })} /></Labeled>
                <Labeled label={`Colour temperature — ${s.cct_k} K`}><input type="range" min={1800} max={10000} step={50} value={s.cct_k || 5600} onChange={(e) => setSrc(i, { cct_k: +e.target.value })} /></Labeled>
                <Labeled label={`Softness — ${Math.round((s.softness ?? 0.5) * 100)}%`}><input type="range" min={0} max={1} step={0.01} value={s.softness ?? 0.5} onChange={(e) => setSrc(i, { softness: +e.target.value })} /></Labeled>
                <Labeled label={`Height above eye line — ${s.elevation_deg}°`}><input type="range" min={-30} max={85} value={s.elevation_deg} onChange={(e) => setSrc(i, { elevation_deg: +e.target.value })} /></Labeled>
              </>}
              <Labeled label={`Direction — ${s.azimuth_deg}° (${s.azimuth_deg > 0 ? "camera-left" : s.azimuth_deg < 0 ? "camera-right" : "on axis"})`}>
                <input type="range" min={-180} max={180} value={s.azimuth_deg} onChange={(e) => setSrc(i, { azimuth_deg: +e.target.value })} />
              </Labeled>
              {s.role !== "key" && <button className="danger" onClick={() => set({ sources: L.sources.filter((_, j) => j !== i) })}>Remove</button>}
            </div>
          </details>
        ))}
        <div className="row" style={{ margin: "8px 0 14px" }}>
          {["fill", "backlight", "rim", "practical", "negative_fill"].map((r) => <button key={r} className="chip" onClick={() => add(r)}>Add {ROLE_LABEL[r].toLowerCase()}</button>)}
        </div>
        <label className="row" style={{ color: "var(--text)" }}><input type="checkbox" checked={L.haze} onChange={(e) => set({ haze: e.target.checked })} /> Atmospheric haze</label>
        <p className="small muted">{L.exposure}</p>
        <ErrorNote error={error} />
        {msg && <p className="small" style={{ color: "var(--ok)" }}>{msg}</p>}
        <div className="row"><button className="primary" disabled={!ws.lighting.dirty} onClick={save}>Save lighting plan</button><button className="ghost" onClick={reset}>Reset to concept</button></div>
      </div>
    );
  },
};
