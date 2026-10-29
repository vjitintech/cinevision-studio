import { cloneElement, isValidElement, useEffect, useId, useRef, useState } from "react";
import type { Job, Tagged } from "../types";
import { api } from "../api";

export function Fact({ label, t, render }: { label: string; t?: Tagged | null; render?: (v: any) => React.ReactNode }) {
  if (!t) return null;
  const v = render ? render(t.value) : fmt(t.value);
  return (
    <div className={`fact ${t.basis}`} title={`${t.basis} · confidence ${(t.confidence * 100).toFixed(0)}%`}>
      <div className="k">{label}<span className="conf" aria-label={`confidence ${Math.round(t.confidence * 100)}%`}><b style={{ width: `${t.confidence * 100}%` }} /></span></div>
      <div className="v">{v}</div>
      {t.note && <div className="why">{t.note}</div>}
    </div>
  );
}

export function fmt(v: any): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(3).replace(/0+$/, "").replace(/\.$/, "");
  if (typeof v === "boolean") return v ? "yes" : "no";
  if (Array.isArray(v)) return v.map(fmt).join(", ");
  if (typeof v === "object") return Object.entries(v).map(([k, x]) => `${k.replace(/_/g, " ")}: ${fmt(x)}`).join(" · ");
  return String(v);
}

export function Legend() {
  return (
    <div className="legend" aria-label="How certain each value is">
      <span><i />observed</span><span><i className="e" />estimated</span><span><i className="h" />heuristic</span>
    </div>
  );
}

const KIND_LABEL: Record<string, string> = { ingest: "Preparing clip", analyze: "Analysing scene", concepts: "Generating concepts" };

export function JobCard({ job, onChange }: { job: Job; onChange?: () => void }) {
  const [busy, setBusy] = useState(false);
  const act = async (what: "retry" | "cancel") => {
    setBusy(true);
    try { await api("POST", `/api/jobs/${job.id}/${what}`); onChange?.(); } finally { setBusy(false); }
  };
  return (
    <div className="job" role="status">
      <div className="row">
        <strong className="grow">{KIND_LABEL[job.kind] || job.kind}</strong>
        <span className={`status ${job.status === "completed" ? "ready" : job.status === "failed" ? "failed" : job.status === "cancelled" ? "" : "busy"}`}>{job.status}</span>
      </div>
      {(job.status === "processing" || job.status === "queued") && (
        <>
          <div className="small muted">{job.stage || "Waiting for the worker…"}{job.attempts > 1 ? ` · attempt ${job.attempts} of ${job.max_attempts}` : ""}</div>
          <div className="bar"><b style={{ width: `${Math.round(job.progress * 100)}%` }} /></div>
          <div className="row" style={{ marginTop: 8 }}><button className="ghost" disabled={busy} onClick={() => act("cancel")}>Cancel</button></div>
        </>
      )}
      {(job.status === "failed" || job.status === "cancelled") && (
        <>
          {job.error && <div className="small" style={{ color: "var(--danger)", marginTop: 4 }}>{job.error}</div>}
          <div className="row" style={{ marginTop: 8 }}><button disabled={busy} onClick={() => act("retry")}>Retry</button></div>
        </>
      )}
    </div>
  );
}

/** Polls while `active` is true. */
export function usePoll(fn: () => void, active: boolean, ms = 1500) {
  const saved = useRef(fn);
  saved.current = fn;
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => saved.current(), ms);
    return () => clearInterval(id);
  }, [active, ms]);
}

export function ErrorNote({ error }: { error: string | null }) {
  return error ? <div className="error" role="alert">{error}</div> : null;
}

export function Swatches({ colors }: { colors: string[] }) {
  return <div className="swatches" aria-label={`palette ${colors.join(", ")}`}>{colors.map((c, i) => <span key={i} style={{ background: c }} title={c} />)}</div>;
}

/** Label + control, associated by id so screen readers (and tests) find the control by its label. */
export function Labeled({ label, children, hint }: { label: string; children: React.ReactNode; hint?: string }) {
  const id = useId();
  const hintId = hint ? id + "-hint" : undefined;
  const child = isValidElement(children) && typeof children.type === "string"
    ? cloneElement(children as React.ReactElement<any>, { id, "aria-describedby": hintId }) : children;
  return (
    <div className="field">
      <label htmlFor={isValidElement(children) && typeof children.type === "string" ? id : undefined}>{label}</label>
      {child}
      {hint && <div id={hintId} className="small faint" style={{ marginTop: 4 }}>{hint}</div>}
    </div>
  );
}
