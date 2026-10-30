import { useEffect, useRef, useState } from "react";
import { api, ApiError, BROWSER, uploadClip } from "../api";
import { navigate } from "../router";
import type { Controls } from "../types";
import { ControlsForm, DEFAULT_CONTROLS } from "../components/ControlsForm";
import { ErrorNote, Labeled } from "../components/common";

export function UploadBox({ pid, onUploaded }: { pid: string; onUploaded: () => void }) {
  const [over, setOver] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [limits, setLimits] = useState<any>(null);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { api("GET", "/api/system/capabilities").then((c) => setLimits(c.limits)).catch(() => {}); }, []);

  const send = async (file: File) => {
    setError(null);
    if (limits && file.size > limits.max_upload_mb * 1024 * 1024) { setError(`That file is larger than the ${limits.max_upload_mb} MB limit.`); return; }
    setProgress(0);
    try { await uploadClip(pid, file, setProgress); onUploaded(); }
    catch (e) { setError(e instanceof ApiError ? e.message : "Upload failed."); }
    finally { setProgress(null); }
  };

  return (
    <div className={`dropzone ${over ? "over" : ""}`} onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
      onDrop={(e) => { e.preventDefault(); setOver(false); const f = e.dataTransfer.files[0]; if (f) send(f); }}>
      {progress === null ? (
        <>
          <p><strong>Drop a clip here</strong> or <button type="button" onClick={() => input.current?.click()}>choose a file</button></p>
          <p className="small muted">
            {limits ? `MP4, MOV, MKV, WEBM or AVI · up to ${limits.max_upload_mb} MB and ${Math.round(limits.max_duration_s / 60)} minutes.` : "Short clips work best."}
            {BROWSER ? " The file stays on this device — it's analysed inside your browser and never uploaded anywhere."
              : " The file is checked, encrypted on arrival, and processed on this server only."}
          </p>
          <input ref={input} type="file" hidden accept=".mp4,.mov,.m4v,.mkv,.webm,.avi,video/*" onChange={(e) => { const f = e.target.files?.[0]; if (f) send(f); }} />
        </>
      ) : (
        <div>
          <p>{BROWSER ? "Checking the clip…" : `Uploading… ${Math.round(progress * 100)}%`}</p>
          <div className="bar" style={{ maxWidth: 320, margin: "0 auto" }}><b style={{ width: `${progress * 100}%` }} /></div>
          {progress > 0.99 && <p className="small muted" style={{ marginTop: 8 }}>Validating and encrypting…</p>}
        </div>
      )}
      <ErrorNote error={error} />
    </div>
  );
}

export function NewProject() {
  const [title, setTitle] = useState("");
  const [controls, setControls] = useState<Controls>(DEFAULT_CONTROLS);
  const [pid, setPid] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      const p = await api("POST", "/api/projects", { title, brief: controls });
      setPid(p.id);
    } catch (err) { setError(err instanceof ApiError ? err.message : "Could not create the project."); }
    finally { setBusy(false); }
  };

  return (
    <div className="page" style={{ maxWidth: 760 }}>
      <h1>New project</h1>
      {!pid ? (
        <form onSubmit={create}>
          <Labeled label="Project or scene name"><input autoFocus required maxLength={160} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Sc. 14 — Kitchen, night" /></Labeled>
          <h3 style={{ marginTop: 24 }}>Creative direction</h3>
          <p className="muted small">Optional now — you can change all of this before generating concepts.</p>
          <ControlsForm value={controls} onChange={setControls} />
          <ErrorNote error={error} />
          <div className="row" style={{ marginTop: 16 }}><button className="primary" type="submit" disabled={busy || !title.trim()}>Create project</button></div>
        </form>
      ) : (
        <div className="stack">
          <p className="notice">Project created. Add the clip you want to rethink.</p>
          <UploadBox pid={pid} onUploaded={() => navigate(`/p/${pid}/analysis`)} />
          <button className="ghost" onClick={() => navigate(`/p/${pid}/analysis`)}>Skip for now</button>
        </div>
      )}
    </div>
  );
}
