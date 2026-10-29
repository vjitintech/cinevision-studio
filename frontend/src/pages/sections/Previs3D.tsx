import { useMemo, useRef, useState } from "react";
import { api, ApiError } from "../../api";
import { ErrorNote, Labeled } from "../../components/common";
import { PlanView, PlanMarker } from "../../previs/PlanView";
import { PrevisCanvas } from "../../previs/PrevisCanvas";
import { clamp, deg, rad, V3 } from "../../previs/math";
import { frustumLines, Line3 } from "../../previs/render";
import { aspect, camAt, lightingOpts, pathLines, sceneFaces } from "../../previs/shot";
import type { Scene } from "../../types";
import { useWS } from "../Workspace";

export const PrevisSection = {
  Center() {
    const ws = useWS();
    const c = ws.concept, shot = ws.shot, scene = ws.scene;
    const [orbitV, setOrbit] = useState({ yaw: -35, pitch: 28, dist: 9 });
    const drag = useRef<{ x: number; y: number } | null>(null);
    const faces = useMemo(() => (scene ? sceneFaces(scene) : []), [scene]);
    if (!c || !shot || !scene) return <div className="empty">Generate and open a concept to explore it in 3D.</div>;
    const centre: V3 = [scene.subjects.reduce((a, s) => a + s.x, 0) / scene.subjects.length, 1, scene.subjects.reduce((a, s) => a + s.z, 0) / scene.subjects.length];
    const obs: V3 = [centre[0] + orbitV.dist * Math.cos(rad(orbitV.pitch)) * Math.sin(rad(orbitV.yaw)), centre[1] + orbitV.dist * Math.sin(rad(orbitV.pitch)),
      centre[2] - orbitV.dist * Math.cos(rad(orbitV.pitch)) * Math.cos(rad(orbitV.yaw))];
    const shotCam = camAt(shot, ws.localT, ws.camera.state, ws.shotOffset(shot));
    const lit = lightingOpts(scene, shot, ws.lighting.state);
    const lines: Line3[] = [...frustumLines(shotCam, c.data.sensor, aspect(c.data), "#86b4e0", 1.0), ...pathLines(shot, "#e3a453", ws.camera.state, ws.shotOffset(shot))];
    for (const L of lit.lights || []) {
      const head: V3 = [L.pos[0], L.pos[1], L.pos[2]];
      lines.push({ a: head, b: [head[0], 0, head[2]], color: "rgba(227,164,83,0.35)", dash: [4, 4] });
    }
    const labels = [...scene.subjects.map((s) => ({ pos: [s.x, 1.95, s.z] as V3, text: s.label })), ...(lit.lights || []).map((L) => ({ pos: L.pos, text: L.role }))];
    const onPointer = (e: React.PointerEvent<HTMLCanvasElement>) => {
      if (e.type === "pointerdown") { drag.current = { x: e.clientX, y: e.clientY }; e.currentTarget.setPointerCapture(e.pointerId); return; }
      if (!drag.current || e.buttons === 0) { drag.current = null; return; }
      const dx = e.clientX - drag.current.x, dy = e.clientY - drag.current.y;
      drag.current = { x: e.clientX, y: e.clientY };
      setOrbit((o) => ({ ...o, yaw: o.yaw + dx * 0.4, pitch: clamp(o.pitch + dy * 0.3, 3, 85) }));
    };
    const markers: PlanMarker[] = [
      ...scene.subjects.map((s) => ({ id: s.id, kind: "subject" as const, x: s.x, z: s.z, heading: s.facing_deg, label: s.label, draggable: true })),
      { id: "cam", kind: "camera", x: shotCam.pos[0], z: shotCam.pos[2], heading: deg(Math.atan2(shotCam.target[0] - shotCam.pos[0], shotCam.target[2] - shotCam.pos[2])), fovDeg: 40, label: `shot ${shot.index + 1}` },
    ];
    const moveSubject = (id: string, x: number, z: number) => {
      if (id === "cam") return;
      ws.setScene({ ...scene, subjects: scene.subjects.map((s) => (s.id === id ? { ...s, x, z: Math.max(0.5, z) } : s)) });
    };
    return (
      <div className="stack">
        <div className="row"><h2 className="grow" style={{ margin: 0 }}>3D previsualisation</h2><span className="small muted">Drag the view to orbit · shot {shot.index + 1}</span></div>
        <div className="stage">
          <PrevisCanvas cam={{ pos: obs, target: centre, focal: 24, roll: 0 }} faces={faces} aspect={16 / 9}
            opts={{ sensor: "full_frame", ...lit, haze: false, lines, labels }} onPointer={onPointer} label="Overview of the proxy set with camera and lights" />
          <span className="tag">Proxy scene · not a reconstruction</span>
          <div style={{ position: "absolute", right: 10, bottom: 10, width: "32%", border: "1px solid var(--line)", borderRadius: 4, overflow: "hidden" }}>
            <PrevisCanvas cam={shotCam} faces={faces} aspect={aspect(c.data)} opts={{ sensor: c.data.sensor, ...lit }} label="Shot camera view" />
          </div>
        </div>
        <div className="row">
          <Labeled label={`Zoom out — ${orbitV.dist.toFixed(0)} m`}><input type="range" min={3} max={25} value={orbitV.dist} onChange={(e) => setOrbit({ ...orbitV, dist: +e.target.value })} /></Labeled>
        </div>
        <div>
          <div className="panel-h">Blocking plan <span className="faint small">drag people to restage them; camera paths keep aiming at their subject</span></div>
          <PlanView scene={scene} markers={markers} onDrag={moveSubject} height={300} />
        </div>
      </div>
    );
  },
  Right() {
    const ws = useWS();
    const c = ws.concept, scene = ws.scene;
    const [error, setError] = useState<string | null>(null);
    const [msg, setMsg] = useState<string | null>(null);
    const [ai, setAi] = useState<string | null>(null);
    if (!c || !scene) return null;
    const setSubj = (id: string, patch: Partial<Scene["subjects"][0]>) => ws.setScene({ ...scene, subjects: scene.subjects.map((s) => (s.id === id ? { ...s, ...patch } : s)) });
    const save = async () => {
      setError(null);
      try { await api("PUT", `/api/concepts/${c.id}/scene3d`, { scene }); setMsg("Scene layout saved for this concept."); }
      catch (e) { setError(e instanceof ApiError ? e.message : "Save failed."); }
    };
    const reset = () => { ws.setScene({ ...c.data.scene }); setMsg("Reset to the estimated layout (not saved yet)."); };
    const preview = async () => {
      setAi(null);
      try { await api("POST", `/api/concepts/${c.id}/ai-preview`); } catch (e) { setAi(e instanceof ApiError ? e.message : "Unavailable."); }
    };
    return (
      <div>
        <div className="panel-h">People and positions</div>
        <p className="small faint">{c.data.scene.note}</p>
        {scene.subjects.map((s) => (
          <div key={s.id} className="fact estimated">
            <div className="row"><input value={s.label} maxLength={40} onChange={(e) => setSubj(s.id, { label: e.target.value })} aria-label="Name" /></div>
            <Labeled label={`Facing — ${Math.round(s.facing_deg)}°`}><input type="range" min={0} max={360} value={s.facing_deg} onChange={(e) => setSubj(s.id, { facing_deg: +e.target.value })} /></Labeled>
            <Labeled label={`Height — ${s.height.toFixed(2)} m`}><input type="range" min={0.9} max={2.1} step={0.01} value={s.height} onChange={(e) => setSubj(s.id, { height: +e.target.value })} /></Labeled>
            <div className="small muted tc">x {s.x.toFixed(2)} m · {s.z.toFixed(2)} m from the original camera</div>
          </div>
        ))}
        <ErrorNote error={error} />
        {msg && <p className="small" style={{ color: "var(--ok)" }}>{msg}</p>}
        <div className="row"><button className="primary" onClick={save}>Save layout</button><button className="ghost" onClick={reset}>Use estimated layout</button></div>
        <div className="panel-h" style={{ marginTop: 24 }}>AI concept preview</div>
        <p className="small muted">Generates a stylised still or motion reference from this concept using an image/video model. It would be clearly labelled as a concept, never as your footage, and needs your explicit consent before anything leaves the server.</p>
        <button onClick={preview}>Request AI preview</button>
        {ai && <div className="notice small" style={{ marginTop: 8 }}>{ai}</div>}
      </div>
    );
  },
};
