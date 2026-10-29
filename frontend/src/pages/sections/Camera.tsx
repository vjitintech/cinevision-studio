import { useMemo, useState } from "react";
import { api, ApiError } from "../../api";
import { ErrorNote, Labeled } from "../../components/common";
import { PlanView, PlanMarker } from "../../previs/PlanView";
import { PrevisCanvas } from "../../previs/PrevisCanvas";
import { ANGLES, deg, hfovDeg, lensCategory, sampleCamera, SHOT_SIZES } from "../../previs/math";
import { aspect, camAt, CameraState, lightingOpts, rebuildPath, sceneFaces, subjectFor } from "../../previs/shot";
import { useWS } from "../Workspace";

const MOVES = ["static", "pan", "tilt", "slow push-in (dolly)", "dolly out", "lateral dolly / track", "crane up", "slow arc / orbit",
  "handheld (breathing)", "handheld tracking", "gimbal follow", "whip pan"];
const COMPOSITIONS: [string, number][] = [["Frontal", 0], ["Three-quarter", 35], ["Profile", 80], ["Over-the-shoulder", 150], ["Point of view", 0]];

function cameraMarkers(ws: ReturnType<typeof useWS>, cam: CameraState | null, t: number): { markers: PlanMarker[]; paths: any[] } {
  const shot = ws.shot!, scene = ws.scene!;
  const c = camAt(shot, t, cam, ws.shotOffset(shot));
  const heading = deg(Math.atan2(c.target[0] - c.pos[0], c.target[2] - c.pos[2]));
  const keys = cam?.camera_path?.keyframes || shot.camera_path.keyframes;
  const pts: [number, number][] = [];
  for (let i = 0; i <= 20; i++) { const p = camAt(shot, i / 20, cam, ws.shotOffset(shot)).pos; pts.push([p[0], p[2]]); }
  const o = scene.original_camera;
  const oh = deg(Math.atan2(o.target[0] - o.position[0], o.target[2] - o.position[2]));
  return {
    markers: [
      ...scene.subjects.map((s) => ({ id: s.id, kind: "subject" as const, x: s.x, z: s.z, heading: s.facing_deg, label: s.label })),
      { id: "orig", kind: "camera", x: o.position[0], z: o.position[2], heading: oh, fovDeg: 55, color: "#6b727c", label: "original (est.)", dashed: true },
      { id: "cam", kind: "camera", x: c.pos[0], z: c.pos[2], heading, fovDeg: hfovDeg(c.focal, ws.concept!.data.sensor), color: "#86b4e0", label: `shot ${shot.index + 1}` },
    ],
    paths: [{ pts, color: "#e3a453" }],
  };
}

export const CameraSection = {
  Center() {
    const ws = useWS();
    const state = ws.camera.state;
    const c = ws.concept;
    const faces = useMemo(() => (ws.scene ? sceneFaces(ws.scene) : []), [ws.scene]);
    if (!c || !ws.shot || !ws.scene) return <div className="empty">Generate and open a concept to plan its camera.</div>;
    const shot = ws.shot;
    const orig = ws.analysis?.data?.shots?.find((s: any) => s.index === shot.source_shot_index);
    const of = orig ? ws.frameAt(orig.representative_t) : null;
    const cam = camAt(shot, ws.localT, state, ws.shotOffset(shot));
    const { markers, paths } = cameraMarkers(ws, state, ws.localT);
    return (
      <div className="stack">
        <div className="row"><h2 className="grow" style={{ margin: 0 }}>Camera planner</h2><span className="muted small">Shot {shot.index + 1} of {c.data.shots.length} · {c.data.name}</span></div>
        <div className="grid2">
          <div className="stage">{of ? <img src={of.url} alt="Original composition" style={{ width: "100%", display: "block" }} /> : <div className="empty">No frame</div>}<span className="tag">Original composition</span></div>
          <div className="stage">
            <PrevisCanvas cam={cam} faces={faces} aspect={aspect(c.data)} opts={{ sensor: c.data.sensor, ...lightingOpts(ws.scene, shot) }} label="Planned framing" />
            <span className="tag">Planned framing · {Math.round(cam.focal)} mm</span>
            <span className="tag r">{state && !state.derived ? "edited" : "from concept"}</span>
          </div>
        </div>
        <PlanView scene={ws.scene} markers={markers} paths={paths} />
        <p className="small faint">Positions are estimates in metres from a ~55° assumed original lens and average human height. Use them to plan, then confirm on set.</p>
      </div>
    );
  },
  Right() {
    return <CameraControls />;
  },
};

function CameraControls() {
  const ws = useWS();
  const cs = ws.camera;
  const [error, setError] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const st = cs.state;
  const c = ws.concept, shot = ws.shot;
  if (!c || !shot || !ws.scene) return null;
  if (!st) return <div className="small muted">Loading camera…</div>;
  const update = (patch: Partial<CameraState>) => cs.setState(rebuildPath(shot, ws.scene!, { ...st, ...patch }, c.data.sensor));
  const save = async () => {
    setError(null);
    try { await api("PUT", `/api/concepts/${c.id}/camera/${shot.index}`, st); cs.setDirty(false); setMsg("Saved to this concept's camera plan."); }
    catch (e) { setError(e instanceof ApiError ? e.message : "Save failed."); }
  };
  const apply = async () => {
    setError(null);
    try {
      const patches = [
        { shot: shot.index, field: "size", value: st.size }, { shot: shot.index, field: "angle", value: st.angle in ANGLES ? st.angle : "eye level" },
        { shot: shot.index, field: "camera_height_m", value: st.camera_height_m }, { shot: shot.index, field: "azimuth_deg", value: st.azimuth_deg },
        { shot: shot.index, field: "lens.ff_equiv_mm", value: st.lens_ff_mm }, { shot: shot.index, field: "movement.type", value: st.movement },
        { shot: shot.index, field: "movement.speed_m_s", value: st.speed_m_s }, { shot: shot.index, field: "dutch_roll_deg", value: st.dutch_roll_deg },
      ];
      const r = await api("POST", `/api/concepts/${c.id}/refine`, { patches });
      ws.replaceConcept(r.concept);
      setMsg("Applied as a new concept revision. The previous revision is kept in history.");
    } catch (e) { setError(e instanceof ApiError ? e.message : "Could not apply."); }
  };
  const reset = async () => { cs.reset(await api("DELETE", `/api/concepts/${c.id}/camera/${shot.index}`)); };
  const subj = subjectFor(ws.scene, shot);
  return (
    <div>
      <div className="panel-h">Shot {shot.index + 1} camera <span className="faint small">target: {subj.label}</span></div>
      <Labeled label="Shot size">
        <select value={st.size} onChange={(e) => update({ size: e.target.value })}>{SHOT_SIZES.map((s) => <option key={s}>{s}</option>)}</select>
      </Labeled>
      <Labeled label="Camera angle">
        <select value={st.angle in ANGLES ? st.angle : ""} onChange={(e) => {
          const [h] = ANGLES[e.target.value]; update({ angle: e.target.value, camera_height_m: h, dutch_roll_deg: e.target.value === "dutch angle" ? 12 : 0 });
        }}>
          {!(st.angle in ANGLES) && <option value="">{st.angle}</option>}
          {Object.keys(ANGLES).map((a) => <option key={a}>{a}</option>)}
        </select>
      </Labeled>
      <Labeled label="Composition">
        <div className="chips">{COMPOSITIONS.map(([n, az]) => (
          <button key={n} type="button" className={`chip ${Math.abs(Math.abs(st.azimuth_deg) - az) < 6 ? "cool" : ""}`}
            onClick={() => update({ azimuth_deg: az * (st.azimuth_deg < 0 ? -1 : 1), composition: n.toLowerCase(), size: n === "Point of view" ? "medium" : st.size })}>{n}</button>
        ))}</div>
      </Labeled>
      <Labeled label={`Around the subject — ${Math.round(st.azimuth_deg)}° (${st.azimuth_deg >= 0 ? "camera-left side" : "camera-right side"})`}>
        <input type="range" min={-170} max={170} value={st.azimuth_deg} onChange={(e) => update({ azimuth_deg: +e.target.value })} />
      </Labeled>
      <Labeled label={`Lens height — ${st.camera_height_m.toFixed(2)} m`}>
        <input type="range" min={0.1} max={5} step={0.05} value={st.camera_height_m} onChange={(e) => update({ camera_height_m: +e.target.value })} />
      </Labeled>
      <Labeled label={`Lens — ${Math.round(st.lens_ff_mm)} mm full-frame equivalent (${lensCategory(st.lens_ff_mm)})`}
        hint={`On ${c.data.sensor.replace(/_/g, " ")}: about ${Math.round(st.lens_ff_mm / ({ full_frame: 1, super35: 1.45, micro_four_thirds: 2, phone: 5.6 } as any)[c.data.sensor])} mm. Camera distance follows the lens to keep the shot size.`}>
        <input type="range" min={12} max={200} value={st.lens_ff_mm} onChange={(e) => update({ lens_ff_mm: +e.target.value })} />
      </Labeled>
      <Labeled label="Movement">
        <select value={MOVES.includes(st.movement) ? st.movement : ""} onChange={(e) => update({ movement: e.target.value, speed_m_s: e.target.value === "static" ? 0 : st.speed_m_s || 0.15 })}>
          {!MOVES.includes(st.movement) && <option value="">{st.movement}</option>}
          {MOVES.map((m) => <option key={m}>{m}</option>)}
        </select>
      </Labeled>
      {st.movement !== "static" && (
        <Labeled label={`Speed — ${st.speed_m_s.toFixed(2)} m/s`}>
          <input type="range" min={0} max={2} step={0.01} value={st.speed_m_s} onChange={(e) => update({ speed_m_s: +e.target.value })} />
        </Labeled>
      )}
      <Labeled label={`Dutch tilt — ${st.dutch_roll_deg}°`}>
        <input type="range" min={-25} max={25} value={st.dutch_roll_deg} onChange={(e) => update({ dutch_roll_deg: +e.target.value })} />
      </Labeled>
      <p className="small muted">Camera ~{st.distance_m} m from the subject. Focus: {st.focus || shot.dof.focus}.</p>
      <ErrorNote error={error} />
      {msg && <p className="small" style={{ color: "var(--ok)" }}>{msg}</p>}
      <div className="row">
        <button className="primary" disabled={!cs.dirty} onClick={save}>Save camera plan</button>
        <button onClick={apply}>Apply to concept</button>
        <button className="ghost" onClick={reset}>Reset</button>
      </div>
    </div>
  );
}
