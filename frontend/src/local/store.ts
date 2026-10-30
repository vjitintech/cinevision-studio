// IndexedDB storage for the browser edition. Data lives only in this browser profile on this device.
const DB = "cinevision-browser", VERSION = 1;
export const STORES = ["projects", "blobs", "videos", "frames", "analyses", "concepts", "planners", "shotlists", "jobs"] as const;
type Store = (typeof STORES)[number];

let dbp: Promise<IDBDatabase> | null = null;
function open(): Promise<IDBDatabase> {
  if (!dbp) dbp = new Promise((resolve, reject) => {
    const req = indexedDB.open(DB, VERSION);
    req.onupgradeneeded = () => { for (const s of STORES) if (!req.result.objectStoreNames.contains(s)) req.result.createObjectStore(s, { keyPath: "id" }); };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
  return dbp;
}

async function tx<T>(store: Store, mode: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest | void): Promise<T> {
  const db = await open();
  return new Promise((resolve, reject) => {
    const t = db.transaction(store, mode);
    const r = fn(t.objectStore(store));
    t.oncomplete = () => resolve(r ? (r.result as T) : (undefined as T));
    t.onerror = () => reject(t.error);
    t.onabort = () => reject(t.error || new Error("Storage quota exceeded or transaction aborted."));
  });
}

export const get = <T = any>(s: Store, id: string) => tx<T | undefined>(s, "readonly", (st) => st.get(id));
export const all = <T = any>(s: Store) => tx<T[]>(s, "readonly", (st) => st.getAll());
export const put = (s: Store, v: any) => tx<void>(s, "readwrite", (st) => { st.put(v); });
export const del = (s: Store, id: string) => tx<void>(s, "readwrite", (st) => { st.delete(id); });
export async function where<T = any>(s: Store, pred: (x: T) => boolean) { return (await all<T>(s)).filter(pred); }
export async function wipe() { const db = await open(); db.close(); dbp = null; await new Promise((r, j) => { const q = indexedDB.deleteDatabase(DB); q.onsuccess = r; q.onerror = j; q.onblocked = r; }); }

export async function usage() {
  try { const e = await navigator.storage?.estimate?.(); return e ? { used: e.usage || 0, quota: e.quota || 0 } : null; } catch { return null; }
}
