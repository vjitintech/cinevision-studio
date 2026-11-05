import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, BROWSER, uploadClip } from "../api";
import { linkTo, navigate } from "../router";
import { ErrorNote, Labeled, usePoll } from "../components/common";
import { useT } from "../i18n";
import { fmtTime } from "../previs/math";

const EXAMPLE = `Opening titles.
EXT. VILLAGE ROAD - DAY. Ravi rides his bicycle past the fields, smiling.
INT. HOUSE - NIGHT. His mother waits alone by the lamp, worried.
EXT. VILLAGE ROAD - NIGHT. Ravi runs home in the dark.
End credits.`;

export function NewAssembly() {
  const { t } = useT();
  const [title, setTitle] = useState("");
  const [script, setScript] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true); setError(null);
    try { const r = await api("POST", "/api/assemblies", { title, script: script || null }); navigate(`/a/${r.id}`); }
    catch (err) { setError(err instanceof ApiError ? err.message : "Could not create it."); }
    finally { setBusy(false); }
  };
  return (
    <div className="page" style={{ maxWidth: 820 }}>
      <h1>{t("asm_title")}</h1>
      <p className="muted">{t("asm_intro")}</p>
      <form onSubmit={create}>
        <Labeled label={t("asm_name")}><input autoFocus required maxLength={160} value={title} onChange={(e) => setTitle(e.target.value)} /></Labeled>
        <Labeled label={t("asm_script")} hint={t("asm_script_hint")}>
          <textarea rows={9} maxLength={60000} value={script} onChange={(e) => setScript(e.target.value)} placeholder={EXAMPLE} />
        </Labeled>
        <ErrorNote error={error} />
        <div className="row" style={{ marginTop: 12 }}><button className="primary" type="submit" disabled={busy || !title.trim()}>{t("asm_create")}</button></div>
      </form>
    </div>
  );
}

interface Clip { clip_id: string; name: string | null; status: string; thumb: string | null; meta: any; job: any }

export function AssemblyPage({ aid }: { aid: string }) {
  const { t, lang } = useT();
  const [a, setA] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [queue, setQueue] = useState<{ name: string; progress: number; error?: string }[]>([]);
  const [script, setScript] = useState<string | null>(null);
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try { const r = await api("GET", `/api/assemblies/${aid}?lang=${lang}`); setA(r); setScript((s) => (s === null ? r.script : s)); }
    catch (e) { setError(e instanceof ApiError && e.status === 404 ? "This assembly doesn't exist or isn't yours." : "Could not load it."); }
  }, [aid, lang]);
  useEffect(() => { load(); }, [load]);
  const busyClips = a?.clips.some((c: Clip) => c.status !== "analysed" && c.status !== "failed" && c.job?.status !== "failed");
  const busyJob = a?.jobs.some((j: any) => j.status === "queued" || j.status === "processing");
  usePoll(load, !!a && (busyClips || busyJob || queue.some((q) => q.progress < 1 && !q.error)), 2500);

  const addFiles = async (files: File[]) => {
    const items = files.filter((f) => /\.(mp4|mov|m4v|mkv|webm|avi)$/i.test(f.name));
    setQueue((q) => [...q, ...items.map((f) => ({ name: f.name, progress: 0 }))]);
    for (const f of items) {      // one at a time keeps memory and bandwidth low, even for 100 clips
      try {
        const c = await api("POST", `/api/assemblies/${aid}/clips`, { filename: f.name });
        await uploadClip(c.clip_id, f, (p) => setQueue((q) => q.map((x) => (x.name === f.name ? { ...x, progress: p } : x))));
        setQueue((q) => q.map((x) => (x.name === f.name ? { ...x, progress: 1 } : x)));
      } catch (e) {
        setQueue((q) => q.map((x) => (x.name === f.name ? { ...x, error: e instanceof ApiError ? e.message : "Upload failed." } : x)));
      }
      load();
    }
  };
  const saveScript = async () => { await api("PATCH", `/api/assemblies/${aid}`, { script: script || "" }); await order(); };
  const order = async () => { try { await api("POST", `/api/assemblies/${aid}/assemble`, { lang }); await load(); } catch (e) { setError(e instanceof ApiError ? e.message : "Failed."); } };
  const removeClip = async (cid: string) => { await api("DELETE", `/api/assemblies/${aid}/clips/${cid}`); load(); };
  const removeAll = async () => { if (confirmDelete()) { await api("DELETE", `/api/assemblies/${aid}`); navigate("/"); } };
  const [confirming, setConfirming] = useState(false);
  const confirmDelete = () => { if (!confirming) { setConfirming(true); return false; } return true; };

  if (BROWSER) return <div className="page"><p>{t("asm_full_only")}</p></div>;
  if (error && !a) return <div className="page"><ErrorNote error={error} /><a {...linkTo("/")}>{t("nav_dashboard")}</a></div>;
  if (!a) return <div className="empty">…</div>;
  const r = a.result;
  const thumbOf = (id: string) => a.clips.find((c: Clip) => c.clip_id === id)?.thumb;
  const analysed = a.clips.filter((c: Clip) => c.status === "analysed").length;
  const job = a.jobs.find((j: any) => j.kind === "assemble" && (j.status === "queued" || j.status === "processing"));

  return (
    <div className="page assembly" dir={r?.rtl ? "rtl" : undefined}>
      <div className="row"><h1 className="grow" style={{ margin: 0 }}>{a.title}</h1>
        <button className="danger" onClick={removeAll}>{confirming ? t("asm_delete_confirm") : t("asm_delete")}</button></div>
      <p className="muted">{t("asm_intro")}</p>

      <div className={`dropzone ${over ? "over" : ""}`} onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); addFiles(Array.from(e.dataTransfer.files)); }}>
        <p><strong>{t("asm_drop")}</strong> · <button type="button" onClick={() => input.current?.click()}>{t("choose_file")}</button></p>
        <p className="small muted">{t("asm_drop_hint", { n: a.max_clips })}</p>
        <input ref={input} type="file" hidden multiple accept=".mp4,.mov,.m4v,.mkv,.webm,.avi,video/*" onChange={(e) => addFiles(Array.from(e.target.files || []))} />
      </div>
      {queue.filter((q) => q.progress < 1 || q.error).map((q) => (
        <div key={q.name} className="small row"><span className="grow">{q.name}</span>
          {q.error ? <span style={{ color: "var(--danger)" }}>{q.error}</span> : <span>{Math.round(q.progress * 100)}%</span>}</div>
      ))}

      <h2>{t("asm_clips", { n: a.clips.length, done: analysed })}</h2>
      <div className="clip-grid">
        {a.clips.map((c: Clip) => (
          <figure key={c.clip_id} className="clip-card">
            <div className="reel-thumb" style={{ width: "100%" }}>{c.thumb ? <img src={c.thumb} alt="" /> : c.status}</div>
            <figcaption className="small"><span className="grow" title={c.name || ""}>{c.name}</span>
              <span className={`status ${c.status === "analysed" ? "ready" : c.status === "failed" || c.job?.status === "failed" ? "failed" : "busy"}`} />
              <button className="ghost" aria-label={`${t("asm_remove")} ${c.name}`} onClick={() => removeClip(c.clip_id)}>×</button></figcaption>
          </figure>
        ))}
      </div>

      <h2>{t("asm_script")}</h2>
      <textarea rows={6} value={script ?? ""} maxLength={60000} onChange={(e) => setScript(e.target.value)} placeholder={EXAMPLE} aria-label={t("asm_script")} />
      <div className="row" style={{ marginTop: 8 }}>
        <button className="primary" disabled={!analysed || !!job} onClick={saveScript}>{t("asm_order")}</button>
        {job && <span className="small muted">{job.stage || t("job_assemble")} · {Math.round((job.progress || 0) * 100)}%</span>}
        {!a.ai_available && <span className="small muted">{t("asm_ai_off")} <a {...linkTo("/settings")}>{t("ai_set_up")}</a></span>}
      </div>
      <ErrorNote error={error} />

      {r && (
        <>
          <h2>{t("asm_result", { n: r.sequence.length, time: fmtTime(r.runtime_s) })}</h2>
          {r.notes.map((n: any) => <p key={n.code} className="notice small">{n.text}</p>)}
          <ol className="cutlist">
            {r.sequence.map((s: any) => (
              <li key={s.clip_id} className="cut">
                <div className="transition small faint">↳ {s.transition_label}{s.transition_extra_label ? ` · ${s.transition_extra_label}` : ""}</div>
                <div className="row" style={{ alignItems: "flex-start" }}>
                  <div className="reel-thumb">{thumbOf(s.clip_id) ? <img src={thumbOf(s.clip_id)} alt="" /> : null}</div>
                  <div className="grow">
                    <h4 style={{ margin: 0, color: "var(--text)" }}>{s.position}. {s.name}</h4>
                    <div className="small faint tc">{fmtTime(s.starts_at_s)} · {t("asm_use", { a: fmtTime(s.in_s), b: fmtTime(s.out_s) })}</div>
                    <ul className="small">{s.reasons.map((x: any, i: number) => <li key={i}>{x.text}</li>)}</ul>
                    {s.flags.length > 0 && <ul className="small" style={{ color: "var(--tungsten)" }}>{s.flags.map((x: any, i: number) => <li key={i}>{x.text}</li>)}</ul>}
                  </div>
                </div>
                {s.transition_out_label && <div className="transition small faint">↳ {s.transition_out_label}</div>}
              </li>
            ))}
          </ol>
          {r.rejected.length > 0 && (
            <>
              <h3>{t("asm_unused", { n: r.rejected.length })}</h3>
              <ul className="unused">{r.rejected.map((x: any) => (
                <li key={x.clip_id} className="row" style={{ alignItems: "flex-start" }}>
                  <div className="reel-thumb" style={{ width: 96 }}>{thumbOf(x.clip_id) ? <img src={thumbOf(x.clip_id)} alt="" /> : null}</div>
                  <div><b>{x.name}</b><ul className="small">{x.flags.map((f: any, i: number) => <li key={i}>{f.text}</li>)}</ul></div>
                </li>))}</ul>
            </>
          )}
          {a.ai?.status === "used" && (
            <section className="concept ai-notes">
              <h3 style={{ marginTop: 0 }}>{t("asm_ai_order")} <span className="small faint">{a.ai.model}</span></h3>
              <ol className="small">{a.ai.sequence.map((s: any) => <li key={s.clip_id}>{s.name}</li>)}</ol>
              {a.ai.unused.length > 0 && <><h4>{t("asm_ai_unused")}</h4><ul className="small">{a.ai.unused.map((u: any) => <li key={u.clip_id}><b>{u.name}</b> — {u.reason}</li>)}</ul></>}
              {a.ai.notes.length > 0 && <><h4>{t("edit_ideas")}</h4><ul className="small">{a.ai.notes.map((n: string) => <li key={n}>{n}</li>)}</ul></>}
              <p className="small faint">{a.ai.note}</p>
            </section>
          )}
          {a.ai?.status === "failed" && <p className="small" style={{ color: "var(--danger)" }}>{t("ai_failed")} {a.ai.error}</p>}
          <details><summary>{t("asm_beats", { n: r.beats.length })}</summary>
            <ol className="small">{r.beats.map((b: any) => <li key={b.index}>{b.text}</li>)}</ol></details>
        </>
      )}
    </div>
  );
}
