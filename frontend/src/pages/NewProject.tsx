import { useEffect, useRef, useState } from "react";
import { api, ApiError, BROWSER, uploadClip } from "../api";
import { navigate } from "../router";
import type { Controls } from "../types";
import { ControlsForm, DEFAULT_CONTROLS } from "../components/ControlsForm";
import { ErrorNote, Labeled } from "../components/common";
import { useT } from "../i18n";

export function UploadBox({ pid, onUploaded }: { pid: string; onUploaded: () => void }) {
  const { t } = useT();
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
          <p><strong>{t("drop_clip")}</strong> · <button type="button" onClick={() => input.current?.click()}>{t("choose_file")}</button></p>
          <p className="small muted">
            {limits ? `MP4, MOV, MKV, WEBM or AVI · phone or cinema camera · up to ${limits.max_upload_mb >= 1024 ? `${Math.round(limits.max_upload_mb / 1024)} GB` : `${limits.max_upload_mb} MB`} and ${limits.max_duration_s >= 3600 ? `${Math.round(limits.max_duration_s / 3600)} hours` : `${Math.round(limits.max_duration_s / 60)} minutes`}.` : "Any camera, any length."}
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
  const { t } = useT();
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
  const next = BROWSER ? "analysis" : "review";

  return (
    <div className="page" style={{ maxWidth: 760 }}>
      <h1>{t("new_title")}</h1>
      {!pid ? (
        <form onSubmit={create}>
          <Labeled label={t("new_name")}><input autoFocus required maxLength={160} value={title} onChange={(e) => setTitle(e.target.value)} placeholder={t("new_name_ph")} /></Labeled>
          <p className="muted small">{t("new_hint")}</p>
          <details style={{ marginTop: 12 }}>
            <summary>{t("new_optional")}</summary>
            <ControlsForm value={controls} onChange={setControls} />
          </details>
          <ErrorNote error={error} />
          <div className="row" style={{ marginTop: 16 }}><button className="primary" type="submit" disabled={busy || !title.trim()}>{t("new_create")}</button></div>
        </form>
      ) : (
        <div className="stack">
          <p className="notice">{t("new_created")}</p>
          <UploadBox pid={pid} onUploaded={() => navigate(`/p/${pid}/${next}`)} />
          <button className="ghost" onClick={() => navigate(`/p/${pid}/${next}`)}>{t("skip")}</button>
        </div>
      )}
    </div>
  );
}
