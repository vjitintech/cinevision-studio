// Movie assembly: the director's own order — drag and drop (or Move up / Move down buttons), "Check my order",
// reset to the suggested cut, and EDL / FCPXML downloads for editing software.
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../api";
import { ErrorNote } from "../components/common";
import { useT } from "../i18n";
import { fmtTime } from "../previs/math";

interface Clip { clip_id: string; name: string | null; thumb: string | null }
type ListName = "used" | "unused";

/** GET download from the owner's own session (no public link, nothing cached). */
async function getDownload(path: string, fallbackName: string) {
  const res = await fetch(path, { credentials: "same-origin" });
  if (!res.ok) {
    const d = await res.json().catch(() => null);
    throw new ApiError(res.status, d?.error?.code || "export_failed", d?.error?.message || "Download failed.");
  }
  const blob = await res.blob();
  const name = /filename="([^"]+)"/.exec(res.headers.get("content-disposition") || "")?.[1] || fallbackName;
  const url = URL.createObjectURL(blob);
  const el = document.createElement("a");
  el.href = url; el.download = name; document.body.appendChild(el); el.click(); el.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

export function MyOrder({ aid, a, r, onSaved }: { aid: string; a: any; r: any; onSaved: () => void }) {
  const { t, lang } = useT();
  const suggested = useCallback((): Record<ListName, string[]> => {
    const used: string[] = r.sequence.map((s: any) => s.clip_id);
    const unused = a.clips.map((c: Clip) => c.clip_id).filter((id: string) => !used.includes(id));
    return { used, unused };
  }, [r, a.clips]);
  const fromSaved = useCallback((): Record<ListName, string[]> | null => (a.mine
    ? { used: a.mine.clips, unused: [...a.mine.excluded, ...a.mine.unplaced] } : null), [a.mine]);
  const [lists, setLists] = useState<Record<ListName, string[]>>(() => fromSaved() || suggested());
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [announce, setAnnounce] = useState("");
  const [dragOver, setDragOver] = useState<string | null>(null);
  const drag = useRef<{ list: ListName; index: number } | null>(null);
  const savedKey = `${r.created_at}|${a.mine?.updated_at || ""}|${a.clips.length}`;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { if (!dirty) setLists(fromSaved() || suggested()); }, [savedKey]);

  const clip = (id: string) => a.clips.find((c: Clip) => c.clip_id === id) as Clip | undefined;
  const name = (id: string) => clip(id)?.name || id;
  const focusLater = (sel: string) => requestAnimationFrame(() => (document.querySelector(sel) as HTMLElement | null)?.focus());

  const move = (from: ListName, index: number, to: ListName, toIndex: number, how: "drag" | "key") => {
    const next = { used: [...lists.used], unused: [...lists.unused] };
    const [id] = next[from].splice(index, 1);
    if (id === undefined) return;
    const at = Math.max(0, Math.min(toIndex, next[to].length));
    next[to].splice(at, 0, id);
    setLists(next); setDirty(true);
    setAnnounce(to === "unused" ? t("asm_left_out", { name: name(id) })
      : from === "unused" ? t("asm_put_back", { name: name(id), pos: at + 1 })
      : t("asm_moved", { name: name(id), pos: at + 1, n: next.used.length }));
    if (how === "key") {
      // keep keyboard focus on the moved clip (same button if still possible)
      const act = from === to ? (toIndex < index ? "up" : "down") : "toggle";
      focusLater(`[data-order-item="${to}-${at}"] button[data-act="${act}"]:not([disabled])`);
      requestAnimationFrame(() => {
        const row = document.querySelector(`[data-order-item="${to}-${at}"]`);
        if (row && !row.contains(document.activeElement)) (row.querySelector("button:not([disabled])") as HTMLElement | null)?.focus();
      });
    }
  };
  const onDrop = (to: ListName, toIndex: number) => (e: React.DragEvent) => {
    e.preventDefault(); e.stopPropagation(); setDragOver(null);
    const d = drag.current; drag.current = null;
    if (!d) return;
    const adj = d.list === to && d.index < toIndex ? toIndex - 1 : toIndex;
    if (d.list === to && adj === d.index) return;
    move(d.list, d.index, to, adj, "drag");
  };

  const check = async () => {
    setBusy(true); setError(null);
    try {
      await api("PUT", `/api/assemblies/${aid}/order`, { clips: lists.used, excluded: lists.unused, lang });
      setDirty(false); setAnnounce(t("asm_checked")); onSaved();
    } catch (e) { setError(e instanceof ApiError ? e.message : "Could not check the order."); }
    finally { setBusy(false); }
  };
  const reset = async () => {
    setBusy(true); setError(null);
    try {
      if (a.mine) await api("DELETE", `/api/assemblies/${aid}/order`);
      setLists(suggested()); setDirty(false); setAnnounce(t("asm_reset_done")); onSaved();
    } catch (e) { setError(e instanceof ApiError ? e.message : "Failed."); }
    finally { setBusy(false); }
  };
  const dl = async (format: "edl" | "fcpxml", order: "suggested" | "mine") => {
    setError(null);
    try { await getDownload(`/api/assemblies/${aid}/export?format=${format}&order=${order}`, `${order}.${format}`); }
    catch (e) { setError(e instanceof ApiError ? e.message : "Download failed."); }
  };

  const item = (list: ListName, id: string, i: number) => {
    const n = name(id);
    const key = `${list}-${i}`;
    const th = clip(id)?.thumb;
    return (
      <li key={`${id}-${i}`} data-order-item={key} draggable className={`order-item${dragOver === key ? " drop-target" : ""}`}
        onDragStart={(e) => { drag.current = { list, index: i }; e.dataTransfer.effectAllowed = "move"; e.dataTransfer.setData("text/plain", n); }}
        onDragEnd={() => { drag.current = null; setDragOver(null); }}
        onDragOver={(e) => { e.preventDefault(); e.stopPropagation(); e.dataTransfer.dropEffect = "move"; setDragOver(key); }}
        onDrop={onDrop(list, i)}>
        <span className="drag-handle" aria-hidden="true" title={t("asm_drag_handle")}>⠿</span>
        {list === "used" && <span className="pos" aria-hidden="true">{i + 1}</span>}
        <div className="reel-thumb">{th ? <img src={th} alt="" /> : null}</div>
        <span className="grow order-name" title={n}>{list === "used" && <span className="sr-only">{i + 1}. </span>}{n}</span>
        {list === "used" && <>
          <button type="button" className="ghost" data-act="up" disabled={i === 0} aria-label={`${t("asm_move_up")}: ${n}`}
            onClick={() => move("used", i, "used", i - 1, "key")}>↑</button>
          <button type="button" className="ghost" data-act="down" disabled={i === lists.used.length - 1} aria-label={`${t("asm_move_down")}: ${n}`}
            onClick={() => move("used", i, "used", i + 1, "key")}>↓</button>
        </>}
        <button type="button" className="ghost" data-act="toggle" aria-label={`${list === "used" ? t("asm_leave_out") : t("asm_use_clip")}: ${n}`}
          onClick={() => (list === "used" ? move("used", i, "unused", 0, "key") : move("unused", i, "used", lists.used.length, "key"))}>
          {list === "used" ? t("asm_leave_out") : t("asm_use_clip")}</button>
      </li>
    );
  };
  const zone = (list: ListName) => ({
    onDragOver: (e: React.DragEvent) => { e.preventDefault(); setDragOver(`${list}-end`); },
    onDragLeave: (e: React.DragEvent) => { if (e.currentTarget === e.target) setDragOver(null); },
    onDrop: onDrop(list, lists[list].length),
  });
  const mine = a.mine;

  return (
    <section className="my-order" aria-labelledby="my-order-h">
      <h2 id="my-order-h">{t("asm_mine")}</h2>
      <p className="small muted">{t("asm_mine_intro")}</p>
      <div aria-live="polite" role="status" className="sr-only">{announce}</div>
      <div className="order-cols">
        <div>
          <h3 id="used-h">{t("asm_used", { n: lists.used.length })}</h3>
          <ol className={`order-list used${dragOver === "used-end" ? " drop-target" : ""}`} aria-labelledby="used-h" {...zone("used")}>
            {lists.used.map((id, i) => item("used", id, i))}
            {!lists.used.length && <li className="small faint order-empty">{t("asm_drop_here")}</li>}
          </ol>
        </div>
        <div>
          <h3 id="unused-h">{t("asm_not_used_list", { n: lists.unused.length })}</h3>
          <ul className={`order-list unused-list${dragOver === "unused-end" ? " drop-target" : ""}`} aria-labelledby="unused-h" {...zone("unused")}>
            {lists.unused.map((id, i) => item("unused", id, i))}
            {!lists.unused.length && <li className="small faint order-empty">{t("asm_drop_here")}</li>}
          </ul>
        </div>
      </div>
      <div className="row" style={{ marginTop: 10, flexWrap: "wrap" }}>
        <button className="primary" type="button" disabled={busy} onClick={check}>{t("asm_check")}</button>
        <button type="button" disabled={busy || (!dirty && !mine)} onClick={reset}>{t("asm_reset")}</button>
        {dirty && <span className="small muted">{t("asm_unchecked")}</span>}
      </div>
      <ErrorNote error={error} />
      {mine && !dirty && (
        <div className="order-check">
          {mine.stale && <p className="notice warm small">{t("asm_stale")}</p>}
          <ul className="check-notes">
            {mine.notes.map((n: any, i: number) => (
              <li key={i} className={`check-note ${n.severity}`}>
                <p style={{ margin: 0 }}>{n.text}{n.because_text ? ` ${n.because_text}` : ""}</p>
                {n.fix_text && <p className="small" style={{ margin: "2px 0 0" }}><b>{t("asm_fix")}</b> {n.fix_text}</p>}
              </li>))}
          </ul>
          <p className="small faint">{t("asm_mine_runtime", { n: mine.sequence.length, time: fmtTime(mine.runtime_s) })}</p>
        </div>
      )}
      <h3>{t("asm_download")}</h3>
      <div className="row" style={{ flexWrap: "wrap" }}>
        <button type="button" onClick={() => dl("edl", "suggested")}>{t("asm_dl_edl_suggested")}</button>
        <button type="button" onClick={() => dl("fcpxml", "suggested")}>{t("asm_dl_xml_suggested")}</button>
        <button type="button" disabled={!mine || dirty} onClick={() => dl("edl", "mine")}>{t("asm_dl_edl_mine")}</button>
        <button type="button" disabled={!mine || dirty} onClick={() => dl("fcpxml", "mine")}>{t("asm_dl_xml_mine")}</button>
      </div>
      <p className="small faint">{t("asm_dl_hint")}</p>
    </section>
  );
}
