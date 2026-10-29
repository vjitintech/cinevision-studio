import { useEffect, useMemo, useState } from "react";
import { api, ApiError } from "../../api";
import { ErrorNote } from "../../components/common";
import { PrevisCanvas } from "../../previs/PrevisCanvas";
import { fmtTime } from "../../previs/math";
import { aspect, camAt, lightingOpts, sceneFaces } from "../../previs/shot";
import type { Shot } from "../../types";
import { useWS } from "../Workspace";

interface Panel { shot: number; duration_s: number; title: string; note: string; transition: string; transition_s: number }
interface Board { panels: Panel[]; animatic: { playback_rate?: number; show_paths?: boolean }; derived?: boolean }

/** Movement notation drawn over a storyboard panel. */
export function MoveArrow({ move }: { move: string }) {
  const m = move.toLowerCase();
  const stroke = "#b3541e";
  const head = <defs><marker id="ah" markerUnits="userSpaceOnUse" markerWidth="7" markerHeight="7" refX="5" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7 z" fill={stroke} /></marker></defs>;
  let body: React.ReactNode = null;
  if (/push|dolly[ -]?in|creep/.test(m)) body = [[10, 10, 30, 22], [90, 10, 70, 22], [10, 90, 30, 78], [90, 90, 70, 78]].map(([a, b, c, d], i) =>
    <line key={i} x1={a} y1={b} x2={c} y2={d} stroke={stroke} strokeWidth={2.5} markerEnd="url(#ah)" />);
  else if (/pull|dolly out/.test(m)) body = [[30, 22, 10, 10], [70, 22, 90, 10], [30, 78, 10, 90], [70, 78, 90, 90]].map(([a, b, c, d], i) =>
    <line key={i} x1={a} y1={b} x2={c} y2={d} stroke={stroke} strokeWidth={2.5} markerEnd="url(#ah)" />);
  else if (/whip|pan/.test(m)) body = <line x1={25} y1={50} x2={75} y2={50} stroke={stroke} strokeWidth={3} markerEnd="url(#ah)" />;
  else if (/tilt|crane|rise/.test(m)) body = <line x1={50} y1={75} x2={50} y2={25} stroke={stroke} strokeWidth={3} markerEnd="url(#ah)" />;
  else if (/arc|orbit/.test(m)) body = <path d="M20,80 Q50,40 80,80" fill="none" stroke={stroke} strokeWidth={3} markerEnd="url(#ah)" />;
  else if (/lateral|slide|drift|track/.test(m) && !/handheld/.test(m)) body = <line x1={20} y1={88} x2={80} y2={88} stroke={stroke} strokeWidth={3} markerEnd="url(#ah)" />;
  else if (/handheld|follow|breathing/.test(m)) body = <path d="M20,88 q7.5,-8 15,0 t15,0 t15,0 t15,0" fill="none" stroke={stroke} strokeWidth={2.5} />;
  if (!body) return null;
  return <svg viewBox="0 0 100 100" preserveAspectRatio="none" style={{ position: "absolute", inset: 0, width: "100%", height: "100%", pointerEvents: "none" }} aria-hidden>{head}{body}</svg>;
}

function Animatic({ board }: { board: Board }) {
  const ws = useWS();
  const c = ws.concept!, scene = ws.scene!;
  const faces = useMemo(() => sceneFaces(scene), [scene]);
  const shots = c.data.shots;
  // find current & next shot for dissolves
  let acc = 0, cur: Shot = shots[0], local = 0;
  for (const s of shots) { if (ws.time < acc + s.duration_s) { cur = s; local = ws.time - acc; break; } acc += s.duration_s; cur = s; local = s.duration_s; }
  const panel = board.panels.find((p) => p.shot === cur.index);
  const next = shots.find((s) => s.index === cur.index + 1);
  const ts = panel?.transition_s || 0;
  const mix = next && ts > 0 && cur.duration_s - local < ts ? 1 - (cur.duration_s - local) / ts : 0;
  const lastFade = !next && cur.duration_s - local < 0.6 ? (cur.duration_s - local) / 0.6 : 1;
  const ar = aspect(c.data);
  return (
    <div style={{ maxWidth: 880 }}>
      <div className="stage" style={{ opacity: lastFade }}>
        <PrevisCanvas cam={camAt(cur, local / cur.duration_s, null, ws.shotOffset(cur))} faces={faces} aspect={ar} opts={{ sensor: c.data.sensor, ...lightingOpts(scene, cur) }} label={`Animatic, shot ${cur.index + 1}`} />
        {next && mix > 0 && (
          <div style={{ position: "absolute", inset: 0, opacity: mix }}>
            <PrevisCanvas cam={camAt(next, 0, null, ws.shotOffset(next))} faces={faces} aspect={ar} opts={{ sensor: c.data.sensor, ...lightingOpts(scene, next) }} />
          </div>
        )}
        <span className="tag">Shot {cur.index + 1} · {cur.size} · {cur.movement.type}</span>
        <span className="tag r tc">{fmtTime(ws.time)}</span>
      </div>
      <p className="small muted" style={{ marginTop: 6 }}>{panel?.note || cur.purpose}</p>
    </div>
  );
}

export const StoryboardSection = {
  Center() {
    const ws = useWS();
    const c = ws.concept, scene = ws.scene;
    const [board, setBoard] = useState<Board | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [dirty, setDirty] = useState(false);
    const faces = useMemo(() => (scene ? sceneFaces(scene) : []), [scene]);
    useEffect(() => { if (c) api("GET", `/api/concepts/${c.id}/storyboard`).then((b) => { setBoard(b); setDirty(false); }).catch(() => {}); }, [c?.id]);
    if (!c || !scene) return <div className="empty">Generate and open a concept to build its storyboard.</div>;
    if (!board) return <div className="empty">Loading storyboard…</div>;
    const save = async () => {
      setError(null);
      try { setBoard(await api("PUT", `/api/concepts/${c.id}/storyboard`, board)); setDirty(false); }
      catch (e) { setError(e instanceof ApiError ? e.message : "Save failed."); }
    };
    const edit = (i: number, patch: Partial<Panel>) => { setBoard({ ...board, panels: board.panels.map((p, j) => (j === i ? { ...p, ...patch } : p)) }); setDirty(true); };
    const ar = aspect(c.data);
    let start = 0;
    return (
      <div className="stack">
        <div className="row"><h2 className="grow" style={{ margin: 0 }}>Storyboard & animatic</h2>
          <button className="primary" onClick={() => ws.setPlaying(!ws.playing)}>{ws.playing ? "Pause animatic" : "Play animatic"}</button>
        </div>
        <Animatic board={board} />
        <div className="boards">
          {c.data.shots.map((s, i) => {
            const p = board.panels.find((x) => x.shot === s.index) || board.panels[i];
            const t0 = start; start += s.duration_s;
            const playing = ws.time >= t0 && ws.time < t0 + s.duration_s;
            return (
              <figure key={s.index} className={`board ${playing ? "playing" : ""}`} style={{ margin: 0 }}>
                <div style={{ position: "relative" }} onClick={() => ws.setShotIdx(s.index)}>
                  <PrevisCanvas cam={camAt(s, 0.15, null, ws.shotOffset(s))} faces={faces} aspect={ar} opts={{ sensor: c.data.sensor, style: "sketch", ...lightingOpts(scene, s), haze: false }} label={`Storyboard panel ${s.index + 1}`} />
                  <MoveArrow move={s.movement.type} />
                </div>
                <figcaption className="cap">
                  <div className="row"><b className="grow">{s.index + 1}. {p?.title || s.size}</b><span className="tc">{s.duration_s}s</span></div>
                  <div>{s.lens.focal_mm} mm · {s.movement.type} · {s.lighting.key_to_fill_ratio}</div>
                  <textarea aria-label={`Notes for panel ${s.index + 1}`} value={p?.note || ""} onChange={(e) => edit(i, { note: e.target.value })} />
                  <div className="row small">
                    <span>Then</span>
                    <select value={p?.transition_s ? "dissolve" : "cut"} onChange={(e) => edit(i, { transition: e.target.value, transition_s: e.target.value === "dissolve" ? 0.8 : 0 })}
                      style={{ width: "auto", padding: "2px 6px", background: "#f6f1e7", color: "#2a241e" }}>
                      <option value="cut">cut</option><option value="dissolve">dissolve</option>
                    </select>
                  </div>
                </figcaption>
              </figure>
            );
          })}
        </div>
        <ErrorNote error={error} />
        <div className="row"><button className="primary" disabled={!dirty} onClick={save}>Save storyboard</button>{board.derived && <span className="small muted">Generated from the concept — edit notes and transitions, then save.</span>}</div>
      </div>
    );
  },
  Right() {
    const ws = useWS();
    const c = ws.concept;
    if (!c) return null;
    return (
      <div className="stack">
        <div className="panel-h">How to read the panels</div>
        <p className="small muted">Each panel is drawn from the shot's planned camera position, lens and lighting on proxy figures. Arrows show camera movement: inward corners for a push-in, outward for a pull-out, a curve for an arc, a wave for handheld.</p>
        <p className="small muted">The animatic plays the camera paths in real time with your cut or dissolve choices. Use the timeline below to scrub.</p>
        <div className="panel-h">Editing rhythm</div>
        <p className="small">{c.data.editing.rhythm}</p>
      </div>
    );
  },
};
