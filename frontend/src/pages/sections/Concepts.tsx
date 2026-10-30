import { useEffect, useMemo, useState } from "react";
import { api, ApiError } from "../../api";
import { ControlsForm, DEFAULT_CONTROLS } from "../../components/ControlsForm";
import { ErrorNote, Swatches } from "../../components/common";
import { PrevisCanvas } from "../../previs/PrevisCanvas";
import { aspect, camAt, lightingOpts, sceneFaces } from "../../previs/shot";
import type { Concept, Controls } from "../../types";
import { useWS } from "../Workspace";

function ConceptFrame({ c, t = 0.5, shotIndex = 0 }: { c: Concept; t?: number; shotIndex?: number }) {
  const ws = useWS();
  const scene = ws.concept?.lineage_id === c.lineage_id && ws.scene ? ws.scene : c.data.scene;
  const shot = c.data.shots[shotIndex] || c.data.shots[0];
  const faces = useMemo(() => sceneFaces(scene), [scene]);
  if (!shot) return null;
  const off = ws.concept?.lineage_id === c.lineage_id ? ws.shotOffset(shot) : [0, 0] as [number, number];
  return <PrevisCanvas cam={camAt(shot, t, null, off)} faces={faces} aspect={aspect(c.data)}
    opts={{ sensor: c.data.sensor, ...lightingOpts(scene, shot) }} label={`${c.data.name}, shot ${shot.index + 1} framing`} />;
}

interface Match { concept_id: string; name: string; score: number; confidence: string; already_matches: string[]; differs: string[] }
interface Assessment { verdict: "good" | "good_with_fixes" | "alternatives"; headline: string; detail: string;
  technical_issues: { severity: string; issue: string; fix: string }[]; best_match: Match | null; matches: Match[]; threshold: number; note: string }

/** Tells the user whether the footage they shot is already good, and how close each concept is to it. */
function Verdict({ a }: { a: Assessment }) {
  const ws = useWS();
  const best = a.best_match;
  const cls = a.verdict === "good" ? "verdict good" : a.verdict === "good_with_fixes" ? "verdict fixes" : "verdict alt";
  return (
    <section className={cls} aria-live="polite">
      <h3 style={{ margin: 0 }}>{a.headline}</h3>
      <p className="small" style={{ margin: "4px 0 0" }}>{a.detail}</p>
      {best && a.verdict !== "alternatives" && best.already_matches.length > 0 && (
        <details style={{ marginTop: 6 }}>
          <summary className="small">What already matches “{best.name}” ({best.score}%, {best.confidence} confidence — based on {best.already_matches.length + best.differs.length} measurements)</summary>
          <ul className="small" style={{ margin: "4px 0 0", paddingLeft: 18 }}>{best.already_matches.map((x) => <li key={x}>{x}</li>)}</ul>
          {best.differs.length > 0 && <><div className="small muted" style={{ marginTop: 4 }}>Small differences:</div>
            <ul className="small muted" style={{ margin: 0, paddingLeft: 18 }}>{best.differs.map((x) => <li key={x}>{x}</li>)}</ul></>}
          <button className="ghost" style={{ marginTop: 4 }} onClick={() => ws.setConceptId(best.concept_id)}>Open “{best.name}”</button>
        </details>
      )}
      {a.technical_issues.length > 0 && (
        <details open={a.verdict === "good_with_fixes"} style={{ marginTop: 6 }}>
          <summary className="small">Technical fixes ({a.technical_issues.length})</summary>
          <ul className="small" style={{ margin: "4px 0 0", paddingLeft: 18 }}>
            {a.technical_issues.map((i) => <li key={i.issue}><strong>{i.issue}</strong> {i.fix}</li>)}
          </ul>
        </details>
      )}
      <p className="small faint" style={{ margin: "6px 0 0" }}>{a.note}</p>
    </section>
  );
}

function ConceptCard({ c, active, onOpen, match }: { c: Concept; active: boolean; onOpen: () => void; match?: Match; }) {
  const d = c.data;
  const s = d.shots[0];
  return (
    <article className={`concept ${c.is_selected ? "selected" : ""} ${active ? "active" : ""}`}>
      <header>
        <div className="row"><h3 className="grow" style={{ margin: 0 }}>{d.name}</h3>{c.is_selected && <span className="chip warm">Chosen</span>}</div>
        {match && <div className={`small ${match.score >= 70 ? "" : "muted"}`} title={`Match confidence: ${match.confidence}`}
          style={match.score >= 70 ? { color: "var(--ok)" } : undefined}>
          {match.score >= 70 ? `Your footage already matches this — ${match.score}%` : `${match.score}% like your footage`}</div>}
        <div className="small muted">{d.intended_emotion}{c.revision > 1 ? ` · revision ${c.revision}` : ""}</div>
      </header>
      <div className="frame"><ConceptFrame c={c} /></div>
      <div className="body">
        <dl className="spec">
          <dt>Frame</dt><dd>{s.size}, {s.angle}</dd>
          <dt>Lens</dt><dd>{s.lens.focal_mm} mm {s.lens.category} · {s.dof.aperture}</dd>
          <dt>Movement</dt><dd>{s.movement.type}</dd>
          <dt>Light</dt><dd>{s.lighting.key_to_fill_ratio}{s.lighting.haze ? ", haze" : ""}</dd>
          <dt>Effort</dt><dd>{d.production.complexity_label} · {d.production.crew_hint}</dd>
        </dl>
        <div style={{ margin: "10px 0" }}><Swatches colors={d.color.palette} /></div>
        <button className={active ? "sel" : ""} onClick={onOpen}>{active ? "Open below" : "Open"}</button>
      </div>
    </article>
  );
}

export const ConceptsSection = {
  Center() {
    const ws = useWS();
    const [compare, setCompare] = useState(false);
    const [assess, setAssess] = useState<Assessment | null>(null);
    const c = ws.concept;
    const key = ws.concepts.map((x) => x.id).join(",");
    useEffect(() => {
      if (!ws.concepts.length) return;
      api("GET", `/api/projects/${ws.pid}/assessment`).then(setAssess).catch(() => setAssess(null));
    }, [key, ws.pid]);
    if (!ws.analysis) return <div className="empty">Concepts need a finished analysis. Watch progress on the right.</div>;
    if (!ws.concepts.length) {
      return (
        <div className="stack" style={{ maxWidth: 620 }}>
          <h2>Cinematography concepts</h2>
          <p className="muted">Set your direction on the right, then generate. Each concept is a different way to shoot this scene — not variations of one set-up.</p>
        </div>
      );
    }
    const orig = ws.analysis.data.shots[0];
    const origFrame = orig ? ws.frameAt(orig.representative_t) : null;
    return (
      <div className="stack">
        <div className="row"><h2 className="grow" style={{ margin: 0 }}>Cinematography concepts</h2>
          <label className="row small" style={{ margin: 0, color: "var(--text)" }}><input type="checkbox" checked={compare} onChange={(e) => setCompare(e.target.checked)} /> Compare with the original</label>
        </div>
        {assess && <Verdict a={assess} />}
        {compare && origFrame && (
          <div className="grid2">
            <div className="stage"><img src={origFrame.url} alt="Original framing" style={{ width: "100%", display: "block" }} /><span className="tag">Original footage</span></div>
            {c && <div className="stage"><ConceptFrame c={c} /><span className="tag">{c.data.name} — proxy previs</span></div>}
          </div>
        )}
        <div className="grid2">
          {ws.concepts.map((x) => <ConceptCard key={x.id} c={x} active={c?.lineage_id === x.lineage_id} onOpen={() => ws.setConceptId(x.id)}
            match={assess?.matches.find((m) => m.concept_id === x.id)} />)}
        </div>
        {c && <ConceptDetail />}
      </div>
    );
  },
  Right() {
    const ws = useWS();
    const [controls, setControls] = useState<Controls>({ ...DEFAULT_CONTROLS, ...ws.project.brief });
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);
    const generate = async () => {
      setBusy(true); setError(null);
      try { await api("POST", `/api/projects/${ws.pid}/concepts/generate`, { controls }); await ws.reload(); }
      catch (e) { setError(e instanceof ApiError ? e.message : "Could not start generation."); }
      finally { setBusy(false); }
    };
    return (
      <div>
        {ws.concept && <RefinePanel />}
        <div className="panel-h" style={{ marginTop: ws.concept ? 24 : 0 }}>{ws.concepts.length ? "Generate a new set" : "Direction"}</div>
        <ControlsForm value={controls} onChange={setControls} compact />
        <ErrorNote error={error} />
        <button className="primary" style={{ marginTop: 8 }} disabled={busy || !ws.analysis || ws.activeJobs.length > 0} onClick={generate}>
          Generate {controls.concept_count} concepts
        </button>
      </div>
    );
  },
};

const SUGGESTIONS = ["Make the lighting more dramatic", "Move the camera lower", "Create a slower dolly-in", "Give the background more separation",
  "Keep the actor's blocking but change the camera angle", "Create three alternative endings", "Make it achievable with a small production budget"];

function RefinePanel() {
  const ws = useWS();
  const c = ws.concept!;
  const [cmd, setCmd] = useState("");
  const [result, setResult] = useState<{ applied: string[]; unrecognized: string[]; help?: string } | null>(null);
  const [history, setHistory] = useState<Concept[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { api("GET", `/api/concepts/${c.id}/history`).then((r) => setHistory(r.revisions)).catch(() => {}); }, [c.id]);
  const run = async (text: string) => {
    if (!text.trim()) return;
    setBusy(true); setError(null);
    try {
      const r = await api("POST", `/api/concepts/${c.id}/refine`, { command: text });
      setResult(r);
      if (r.applied.length) { ws.replaceConcept(r.concept); setCmd(""); }
    } catch (e) { setError(e instanceof ApiError ? e.message : "Refinement failed."); }
    finally { setBusy(false); }
  };
  const revert = async (id: string) => { const r = await api("POST", `/api/concepts/${id}/revert`); ws.replaceConcept(r.concept); };
  const select = async () => { await api("POST", `/api/concepts/${c.id}/select`); await ws.reloadConcepts(); };
  return (
    <div>
      <div className="panel-h">Refine “{c.data.name}”</div>
      <form onSubmit={(e) => { e.preventDefault(); run(cmd); }}>
        <textarea value={cmd} onChange={(e) => setCmd(e.target.value)} maxLength={1000} placeholder="Describe a change, e.g. “shot 2 tighter and warmer”" aria-label="Refinement request" />
        <div className="row" style={{ marginTop: 6 }}>
          <button className="primary" type="submit" disabled={busy || !cmd.trim()}>Apply change</button>
          <button type="button" onClick={select} disabled={c.is_selected}>{c.is_selected ? "Chosen concept" : "Choose this concept"}</button>
        </div>
      </form>
      <div className="chips" style={{ marginTop: 10 }}>
        {SUGGESTIONS.map((s) => <button key={s} type="button" className="chip" onClick={() => run(s)} disabled={busy}>{s}</button>)}
      </div>
      <ErrorNote error={error} />
      {result && (
        <div className="small" style={{ marginTop: 10 }}>
          {result.applied.map((a) => <div key={a}>✓ {a}</div>)}
          {result.unrecognized.length > 0 && <div className="muted" style={{ marginTop: 4 }}>Not understood, so nothing was changed for: “{result.unrecognized.join("”, “")}”.</div>}
          {result.help && <div className="faint" style={{ marginTop: 4 }}>{result.help}</div>}
        </div>
      )}
      {history.length > 1 && (
        <details style={{ marginTop: 10 }}>
          <summary>Revision history ({history.length})</summary>
          {history.slice().reverse().map((h) => (
            <div key={h.id} className="row small" style={{ padding: "4px 0" }}>
              <span className="grow">Rev {h.revision}{h.change_note ? ` — ${h.change_note}` : " — generated"}</span>
              {h.is_current ? <span className="faint">current</span> : <button className="ghost" onClick={() => revert(h.id)}>Restore</button>}
            </div>
          ))}
        </details>
      )}
    </div>
  );
}

function ConceptDetail() {
  const ws = useWS();
  const d = ws.concept!.data;
  return (
    <div className="concept" style={{ padding: 18 }}>
      <h2>{d.name}</h2>
      <p>{d.rationale}</p>
      <div className="grid2">
        <div>
          <h4>How it differs from the original</h4>
          <ul className="small">{d.differs_from_original.map((x) => <li key={x}>{x}</li>)}</ul>
          <h4>Colour and grade</h4>
          <Swatches colors={d.color.palette} />
          <p className="small" style={{ marginTop: 6 }}>{d.color.direction} <span className="muted">({d.color.grade})</span></p>
          <h4>Editing</h4>
          <p className="small">{d.editing.rhythm}. Transitions: {d.editing.transitions}.</p>
        </div>
        <div>
          <h4>Production</h4>
          <dl className="spec">
            <dt>Complexity</dt><dd>{d.production.complexity}/5 — {d.production.complexity_label}</dd>
            <dt>Crew</dt><dd>{d.production.crew_hint}</dd>
            <dt>Support</dt><dd>{d.production.camera_support.join(", ")}</dd>
            <dt>Lenses</dt><dd>{d.production.lenses.join(", ")}</dd>
            <dt>Lighting</dt><dd>{d.production.lighting_equipment.join(", ") || "existing light"}</dd>
          </dl>
          <h4 style={{ marginTop: 12 }}>Continuity watch-outs</h4>
          <ul className="small">{d.continuity_risks.map((x) => <li key={x}>{x}</li>)}</ul>
        </div>
      </div>
      <h4>Shot by shot</h4>
      <div className="scroll-x">
        <table className="list">
          <thead><tr><th>#</th><th>Purpose</th><th>Frame</th><th>Lens & focus</th><th>Movement</th><th>Light</th><th>Blocking</th></tr></thead>
          <tbody>
            {d.shots.map((s) => (
              <tr key={s.index} onClick={() => ws.setShotIdx(s.index)} style={{ cursor: "pointer", background: ws.shotIdx === s.index ? "var(--panel-2)" : undefined }}>
                <td className="tc">{s.index + 1}<div className="faint small">{s.duration_s}s</div></td>
                <td>{s.purpose}</td>
                <td>{s.size}, {s.angle}<div className="faint small">{s.viewing_direction}; camera {s.camera_height_m} m high, ~{s.distance_m} m away</div></td>
                <td>{s.lens.focal_mm} mm ({s.lens.ff_equiv_mm} mm FF) {s.lens.category}<div className="faint small">{s.dof.aperture}; {s.dof.focus}</div></td>
                <td>{s.movement.type}<div className="faint small">{s.movement.speed}{s.movement.speed_m_s ? ` ~${s.movement.speed_m_s} m/s` : ""} · {s.movement.rig}</div></td>
                <td>{s.lighting.setup}<div className="faint small">{s.lighting.key_to_fill_ratio} · {s.lighting.exposure}</div></td>
                <td className="small">{s.blocking}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {d.alternative_endings.length > 0 && (
        <>
          <h4 style={{ marginTop: 14 }}>Alternative endings for the final shot</h4>
          <div className="grid3">{d.alternative_endings.map((e) => (
            <div key={e.name} className="fact"><div className="v">{e.name}</div><div className="small muted">{e.size} · {e.movement}</div><div className="why">{e.note}</div></div>
          ))}</div>
        </>
      )}
    </div>
  );
}
