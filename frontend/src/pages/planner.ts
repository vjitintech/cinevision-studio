import { useEffect, useState } from "react";
import { api } from "../api";

export interface Planner<T> { state: T | null; setState: (s: T) => void; dirty: boolean; setDirty: (d: boolean) => void; reset: (s: T) => void }

/** Per-shot planner state (camera or lighting) loaded from the server; falls back to the concept-derived default. */
export function usePlanner<T>(kind: "camera" | "lighting", cid: string | undefined, shot: number | undefined): Planner<T> {
  const [state, setStateRaw] = useState<T | null>(null);
  const [dirty, setDirty] = useState(false);
  useEffect(() => {
    if (!cid || shot == null) { setStateRaw(null); return; }
    let live = true;
    setStateRaw(null); setDirty(false);
    api("GET", `/api/concepts/${cid}/${kind}/${shot}`).then((s) => { if (live) setStateRaw(s); }).catch(() => {});
    return () => { live = false; };
  }, [kind, cid, shot]);
  return { state, setState: (s) => { setStateRaw(s); setDirty(true); }, dirty, setDirty, reset: (s) => { setStateRaw(s); setDirty(false); } };
}
