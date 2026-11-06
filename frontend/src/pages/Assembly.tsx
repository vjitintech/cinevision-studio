import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, BROWSER, uploadClip } from "../api";
import { linkTo, leaveThenDelete, navigate } from "../router";
import { ErrorNote, Labeled, usePoll } from "../components/common";
import { useT } from "../i18n";
import { fmtTime } from "../previs/math";
import { MyOrder } from "./AssemblyOrder";

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

interface QueueItem { id: string; name: string; progress: number; state: "waiting" | "uploading" | "retrying" | "done" | "failed"; error?: string }
interface Clip { clip_id: string; name: string | null; status: string; thumb: string | null; meta: any; job: any }

/** Seconds until a signed media URL ("/api/media/<id>?t=<exp>.<sig>") expires; 0 if it can't be read. */
function urlLife(u: string | null | undefined): number {
  const m = /[?&]t=(\d+)\./.exec(u || "");
  return m ? Number(m[1]) - Date.now() / 1000 : 0;
}

/** Keep the thumbnail URL we already show while it is valid for another minute. Every poll signs fresh URLs, and a
 *  changed src makes the browser download every thumbnail again (300 clips × a poll every 2.5 s would trip the
 *  server's rate limit). */
export function keepThumbs(prev: Clip[] | undefined, next: Clip[]): Clip[] {
  if (!prev) return next;
  const old = new Map(prev.map((c) => [c.clip_id, c.thumb]));
  return next.map((c) => {
    const o = old.get(c.clip_id);
    return o && c.thumb && o.split("?")[0] === c.thumb.split("?")[0] && urlLife(o) > 60 ? { ...c, thumb: o } : c;
  });
}

/** Wait (briefly) until no thumbnail on the page is still downloading, so deleting the clips can't pull the
 *  files away from under requests that are already on their way. */
function thumbsSettled(maxMs = 4000): Promise<void> {
  const pending = Array.from(document.querySelectorAll<HTMLImageElement>(".assembly img")).filter((i) => !i.complete);
  if (!pending.length) return Promise.resolve();
  return Promise.race([
    Promise.all(pending.map((i) => new Promise<void>((r) => { i.addEventListener("load", () => r(), { once: true }); i.addEventListener("error", () => r(), { once: true }); }))).then(() => undefined),
    new Promise<void>((r) => setTimeout(r, maxMs)),
  ]);
}

export function AssemblyPage({ aid }: { aid: string }) {
  const { t, lang } = useT();
  const [a, setA] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [script, setScript] = useState<string | null>(null);
  const [over, setOver] = useState(false);
  // clips being removed (hidden at once) and whether the whole assembly is being deleted (no more polling)
  const [removing, setRemoving] = useState<Set<string>>(() => new Set());
  const [deleting, setDeleting] = useState(false);
  const gen = useRef(0);          // bumped when a delete starts: a load that began earlier is stale and is dropped
  const deletingRef = useRef(false);  // set synchronously, so a poll tick that fires before React re-renders sends nothing
  const input = useRef<HTMLInputElement>(null);

  const inflight = useRef<Promise<unknown> | null>(null);   // a refresh still on its way to the server
  const load = useCallback(async () => {
    const g = gen.current;
    if (deletingRef.current) return;
    try {
      const req = api("GET", `/api/assemblies/${aid}?lang=${lang}`);
      inflight.current = req.catch(() => undefined);
      const r = await req;
      if (g !== gen.current) return;
      setA((prev: any) => ({ ...r, clips: keepThumbs(prev?.clips, r.clips) }));
      setScript((s) => (s === null ? r.script : s));
    }
    catch (e) { if (g === gen.current) setError(e instanceof ApiError && e.status === 404 ? "This assembly doesn't exist or isn't yours." : "Could not load it."); }
  }, [aid, lang]);
  useEffect(() => { load(); }, [load]);
  const busyClips = a?.clips.some((c: Clip) => c.status !== "analysed" && c.status !== "failed" && c.job?.status !== "failed");
  const busyJob = a?.jobs.some((j: any) => j.status === "queued" || j.status === "processing");
  usePoll(load, !!a && !deleting && (busyClips || busyJob || queue.some((q) => q.state !== "done" && q.state !== "failed")), 2500);

  const addFiles = async (files: File[]) => {
    const items = files.filter((f) => /\.(mp4|mov|m4v|mkv|webm|avi)$/i.test(f.name))
      .map((f) => ({ f, id: `${Date.now()}-${Math.random().toString(36).slice(2)}` }));
    if (!items.length) return;
    setQueue((q) => [...q, ...items.map(({ f, id }) => ({ id, name: f.name, progress: 0, state: "waiting" as const }))]);
    const patch = (id: string, p: Partial<QueueItem>) => setQueue((q) => q.map((x) => (x.id === id ? { ...x, ...p } : x)));
    // 3 uploads at a time: much faster for big batches, still gentle on memory; a failed file is retried once,
    // and one bad file never stops the rest
    const one = async ({ f, id }: { f: File; id: string }) => {
      let clipId: string | null = null;
      for (let attempt = 0; attempt < 2; attempt++) {
        try {
          patch(id, { state: attempt ? "retrying" : "uploading", progress: 0, error: undefined });
          if (!clipId) clipId = (await api("POST", `/api/assemblies/${aid}/clips`, { filename: f.name })).clip_id;
          await uploadClip(clipId!, f, (p) => patch(id, { progress: p }));
          patch(id, { state: "done", progress: 1 });
          return;
        } catch (e) {
          const msg = e instanceof ApiError ? e.message : "Upload failed.";
          const final = attempt === 1 || (e instanceof ApiError && e.status >= 400 && e.status < 500 && e.status !== 408 && e.status !== 429);
          if (final) {
            patch(id, { state: "failed", error: msg });
            if (clipId && !(e instanceof ApiError && e.status === 409)) api("DELETE", `/api/assemblies/${aid}/clips/${clipId}`).catch(() => undefined);
            return;
          }
        }
      }
    };
    let next = 0;
    const lane = async () => { while (next < items.length) { const it = items[next++]; await one(it); load(); } };
    await Promise.all([lane(), lane(), lane()]);
    load();
  };
  const saveScript = async () => { await api("PATCH", `/api/assemblies/${aid}`, { script: script || "" }); await order(); };
  const order = async () => { try { await api("POST", `/api/assemblies/${aid}/assemble`, { lang }); await load(); } catch (e) { setError(e instanceof ApiError ? e.message : "Failed."); } };
  const removeClip = async (cid: string) => {
    gen.current++;
    setRemoving((s) => new Set(s).add(cid));             // hide it (and its thumbnails) straight away
    await thumbsSettled();
    try { await api("DELETE", `/api/assemblies/${aid}/clips/${cid}`); }
    catch (e) { setError(e instanceof ApiError ? e.message : "Could not remove the clip."); }
    gen.current++;
    await load();
    setRemoving((s) => { const n = new Set(s); n.delete(cid); return n; });
  };
  const removeAll = async () => {
    if (!confirmDelete()) return;
    gen.current++;
    deletingRef.current = true;
    setDeleting(true);                                    // stops polling; nothing new is requested
    await thumbsSettled();
    await inflight.current;                               // let a refresh already sent finish before deleting
    await leaveThenDelete(() => api("DELETE", `/api/assemblies/${aid}`), `/a/${aid}`);
  };
  const [confirming, setConfirming] = useState(false);
  const confirmDelete = () => { if (!confirming) { setConfirming(true); return false; } return true; };

  if (BROWSER) return <div className="page"><p>{t("asm_full_only")}</p></div>;
  if (error && !a) return <div className="page"><ErrorNote error={error} /><a {...linkTo("/")}>{t("nav_dashboard")}</a></div>;
  if (!a) return <div className="empty">…</div>;
  const r = a.result;
  const thumbOf = (id: string) => (deleting || removing.has(id) ? null : a.clips.find((c: Clip) => c.clip_id === id)?.thumb);
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
        <p className="small muted">{t("asm_drop_hint3", { n: a.max_clips })}</p>
        <input ref={input} type="file" hidden multiple accept=".mp4,.mov,.m4v,.mkv,.webm,.avi,video/*" onChange={(e) => addFiles(Array.from(e.target.files || []))} />
      </div>
      {queue.length > 0 && (
        <div className="upload-queue">
          <p className="small" role="status" aria-live="polite">{t("asm_upload_counts", {
            done: queue.filter((q) => q.state === "done").length, n: queue.length,
            active: queue.filter((q) => q.state === "uploading" || q.state === "retrying").length,
            failed: queue.filter((q) => q.state === "failed").length })}</p>
          <ul className="small">
            {queue.filter((q) => q.state !== "done").map((q) => (
              <li key={q.id} className="row"><span className="grow">{q.name}</span>
                {q.state === "failed" ? <span style={{ color: "var(--danger)" }}>{q.error}</span>
                  : <><progress max={1} value={q.progress} aria-label={q.name} />
                    <span>{q.state === "retrying" ? t("asm_retrying") : q.state === "waiting" ? "…" : `${Math.round(q.progress * 100)}%`}</span></>}
              </li>))}
          </ul>
        </div>
      )}

      <h2>{t("asm_clips", { n: a.clips.length, done: analysed })}</h2>
      <div className="clip-grid">
        {a.clips.filter((c: Clip) => !removing.has(c.clip_id)).map((c: Clip) => (
          <figure key={c.clip_id} className="clip-card">
            <div className="reel-thumb" style={{ width: "100%" }}>{thumbOf(c.clip_id) ? <img src={thumbOf(c.clip_id)!} alt="" /> : c.status}</div>
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
          <MyOrder aid={aid} r={r} onSaved={load}
            a={deleting || removing.size ? { ...a, clips: a.clips.map((c: Clip) => ({ ...c, thumb: thumbOf(c.clip_id) || null })) } : a} />
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
