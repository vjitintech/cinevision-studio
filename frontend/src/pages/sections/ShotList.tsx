import { useEffect, useState } from "react";
import { api, ApiError, download } from "../../api";
import { ErrorNote, Swatches } from "../../components/common";
import { useWS } from "../Workspace";

interface Snapshot { id: string; created_at: string; data: any }

export const ShotListSection = {
  Center() {
    const ws = useWS();
    const c = ws.concept;
    const [lists, setLists] = useState<Snapshot[]>([]);
    const [cur, setCur] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const load = async () => {
      if (!c) return;
      const r = await api("GET", `/api/concepts/${c.id}/shotlists`);
      setLists(r.shotlists);
      setCur((x) => x && r.shotlists.some((s: Snapshot) => s.id === x) ? x : r.shotlists[0]?.id || null);
    };
    useEffect(() => { load().catch(() => {}); /* eslint-disable-next-line */ }, [c?.id]);
    if (!c) return <div className="empty">Generate and choose a concept to produce a shot list.</div>;
    const make = async () => {
      setError(null);
      try { const r = await api("POST", `/api/concepts/${c.id}/shotlist`); await load(); setCur(r.id); }
      catch (e) { setError(e instanceof ApiError ? e.message : "Could not build the shot list."); }
    };
    const snap = lists.find((l) => l.id === cur);
    const d = snap?.data;
    return (
      <div className="stack">
        <div className="row">
          <h2 className="grow" style={{ margin: 0 }}>Shot list</h2>
          <button className="primary" onClick={make}>{lists.length ? "Make a new version" : "Build shot list"}</button>
        </div>
        <ErrorNote error={error} />
        {!d && <p className="muted">A shot list is a snapshot of “{c.data.name}” (revision {c.revision}), including any lighting layouts you saved.</p>}
        {d && (
          <>
            <div className="row small muted">
              <span>{d.concept_name} · revision {d.concept_revision}</span><span>{d.aspect_ratio} · {d.frame_rate} fps</span>
              <span className="tc">{d.total_duration_s}s total</span>
              {lists.length > 1 && (
                <select value={cur || ""} onChange={(e) => setCur(e.target.value)} style={{ width: "auto" }} aria-label="Version">
                  {lists.map((l) => <option key={l.id} value={l.id}>{new Date(l.created_at).toLocaleString()}</option>)}
                </select>
              )}
            </div>
            <div className="scroll-x">
              <table className="list">
                <thead><tr><th>#</th><th>Purpose</th><th>Frame</th><th>Camera & lens</th><th>Movement</th><th>Focus</th><th>Blocking</th><th>Lighting</th><th>Kit</th><th>Next</th></tr></thead>
                <tbody>{d.rows.map((r: any) => (
                  <tr key={r.shot}>
                    <td className="tc">{r.shot}<div className="faint small">{r.duration_s}s</div></td>
                    <td>{r.purpose}</td>
                    <td>{r.size}<div className="faint small">{r.angle}</div></td>
                    <td className="small">{r.camera_position}<div>{r.lens}</div></td>
                    <td className="small">{r.movement}</td>
                    <td className="small">{r.focus_dof}</td>
                    <td className="small">{r.blocking}</td>
                    <td className="small">{r.lighting}<ul style={{ margin: "4px 0 0", paddingLeft: 16 }}>{r.lighting_detail.map((x: string) => <li key={x}>{x}</li>)}</ul></td>
                    <td className="small">{r.equipment.join(", ")}</td>
                    <td className="small">{r.transition}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
            <div className="grid2">
              <div><h4>Grade</h4><Swatches colors={d.palette} /><p className="small" style={{ marginTop: 6 }}>{d.grade}</p></div>
              <div><h4>Continuity</h4><p className="small">{d.rows[0]?.continuity}</p></div>
            </div>
          </>
        )}
      </div>
    );
  },
  Right() {
    const ws = useWS();
    const c = ws.concept;
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState<string | null>(null);
    if (!c) return null;
    const exp = async (format: "csv" | "pdf" | "json") => {
      setError(null); setBusy(format);
      try {
        const lists = (await api("GET", `/api/concepts/${c.id}/shotlists`)).shotlists;
        const id = lists[0]?.id || (await api("POST", `/api/concepts/${c.id}/shotlist`)).id;
        await download(`/api/shotlists/${id}/export`, { format }, `shot-list.${format}`);
      } catch (e) { setError(e instanceof ApiError ? e.message : "Export failed."); }
      finally { setBusy(null); }
    };
    const project = async () => {
      setBusy("project"); setError(null);
      try { await download(`/api/projects/${ws.pid}/export`, {}, "project.cinevision.json"); }
      catch (e) { setError(e instanceof ApiError ? e.message : "Export failed."); }
      finally { setBusy(null); }
    };
    return (
      <div className="stack">
        <div className="panel-h">Export</div>
        <p className="small muted">Downloads go straight to this device. Nothing is sent to any other service.</p>
        <button onClick={() => exp("pdf")} disabled={!!busy}>{busy === "pdf" ? "Preparing…" : "Download PDF"}</button>
        <button onClick={() => exp("csv")} disabled={!!busy}>{busy === "csv" ? "Preparing…" : "Download CSV (spreadsheets)"}</button>
        <button onClick={() => exp("json")} disabled={!!busy}>{busy === "json" ? "Preparing…" : "Download JSON"}</button>
        <hr />
        <button onClick={project} disabled={!!busy}>Download project file</button>
        <p className="small faint">The project file contains the brief, analysis, concepts and plans — no video or frames.</p>
        <ErrorNote error={error} />
      </div>
    );
  },
};
