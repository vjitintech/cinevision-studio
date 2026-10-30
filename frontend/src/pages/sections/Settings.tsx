import { useState } from "react";
import { api, ApiError } from "../../api";
import { ControlsForm, DEFAULT_CONTROLS } from "../../components/ControlsForm";
import { ErrorNote, Labeled } from "../../components/common";
import { navigate } from "../../router";
import type { Controls } from "../../types";
import { useWS } from "../Workspace";

export const SettingsSection = {
  Center() {
    const ws = useWS();
    const [title, setTitle] = useState(ws.project.title);
    const [controls, setControls] = useState<Controls>({ ...DEFAULT_CONTROLS, ...ws.project.brief });
    const [msg, setMsg] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const save = async () => {
      setError(null);
      try { await api("PATCH", `/api/projects/${ws.pid}`, { title, brief: controls }); await ws.reload(); setMsg("Saved."); }
      catch (e) { setError(e instanceof ApiError ? e.message : "Save failed."); }
    };
    return (
      <div className="stack" style={{ maxWidth: 720 }}>
        <h2>Project settings</h2>
        <Labeled label="Project name"><input value={title} maxLength={160} onChange={(e) => setTitle(e.target.value)} /></Labeled>
        <h3>Director's creative controls</h3>
        <ControlsForm value={controls} onChange={setControls} />
        <ErrorNote error={error} />
        {msg && <p className="small" style={{ color: "var(--ok)" }}>{msg}</p>}
        <div><button className="primary" onClick={save}>Save settings</button></div>
      </div>
    );
  },
  Right() {
    const ws = useWS();
    const [confirm, setConfirm] = useState("");
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);
    const removeClip = async () => {
      if (!window.confirm("Remove the clip and everything derived from it (frames, analysis, concepts)? This cannot be undone.")) return;
      setBusy(true);
      try { await api("DELETE", `/api/projects/${ws.pid}/video`); location.reload(); }
      catch (e) { setError(e instanceof ApiError ? e.message : "Could not remove the clip."); setBusy(false); }
    };
    const del = async () => {
      setBusy(true);
      try { await api("DELETE", `/api/projects/${ws.pid}`); navigate("/", true); }
      catch (e) { setError(e instanceof ApiError ? e.message : "Delete failed."); setBusy(false); }
    };
    return (
      <div className="stack">
        <div className="panel-h">Clip</div>
        {ws.project.video ? (
          <>
            <p className="small muted">{ws.project.video.filename}{ws.project.video.scan_status.includes("browser") ? "" : ` · malware scan: ${ws.project.video.scan_status.replace(/_/g, " ")}`}</p>
            <button className="danger" disabled={busy} onClick={removeClip}>Remove clip to replace it</button>
          </>
        ) : <p className="small muted">No clip uploaded.</p>}
        <div className="panel-h" style={{ marginTop: 24 }}>Delete project</div>
        <p className="small muted">Deletes the clip, frames, previews, analysis, concepts, plans, shot lists and pending jobs. Encryption keys for its files are destroyed, so the data can't be recovered.</p>
        <Labeled label={`Type the project name to confirm`}><input value={confirm} onChange={(e) => setConfirm(e.target.value)} /></Labeled>
        <button className="danger" disabled={busy || confirm !== ws.project.title} onClick={del}>Delete project permanently</button>
        <ErrorNote error={error} />
      </div>
    );
  },
};
