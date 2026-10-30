// Same-origin API client. Sends the per-session CSRF token on every state-changing request.
// In the browser edition (GitHub Pages) the same calls are answered in-page by ./local/server — nothing goes over the network.
export const BROWSER = __EDITION__ === "browser";

let csrf: string | null = null;
export const setCsrf = (t: string | null) => { csrf = t; };

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) { super(message); }
}

type Method = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

let onUnauthenticated: (() => void) | null = null;
export const setUnauthenticatedHandler = (fn: () => void) => { onUnauthenticated = fn; };

export async function api<T = any>(method: Method, path: string, body?: unknown, opts: { raw?: BodyInit; signal?: AbortSignal } = {}): Promise<T> {
  if (BROWSER) {
    const local = await import("./local/server");
    const res = await local.handle(method, path, body);
    return (res === undefined ? undefined : JSON.parse(JSON.stringify(res))) as T;
  }
  const headers: Record<string, string> = {};
  if (method !== "GET" && csrf) headers["X-CSRF-Token"] = csrf;
  let payload: BodyInit | undefined = opts.raw;
  if (body !== undefined) { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
  const res = await fetch(path, { method, headers, body: payload, credentials: "same-origin", signal: opts.signal });
  if (res.status === 204) return undefined as T;
  const isJson = (res.headers.get("content-type") || "").includes("json");
  const data = isJson ? await res.json() : null;
  if (!res.ok) {
    const err = data?.error || { code: "http_" + res.status, message: res.statusText || "Request failed." };
    if (res.status === 401 && err.code === "unauthenticated" && onUnauthenticated) onUnauthenticated();
    throw new ApiError(res.status, err.code, err.message);
  }
  if (data && typeof data === "object" && "csrf_token" in data && data.csrf_token) csrf = data.csrf_token;
  return data as T;
}

/** Upload with progress (fetch has no upload progress). */
export function uploadClip(pid: string, file: File, onProgress: (f: number) => void): Promise<any> {
  if (BROWSER) return import("./local/server").then((l) => l.upload(pid, file, onProgress));
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `/api/projects/${pid}/video`);
    if (csrf) xhr.setRequestHeader("X-CSRF-Token", csrf);
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(e.loaded / e.total); };
    xhr.onload = () => {
      let data: any = null;
      try { data = JSON.parse(xhr.responseText); } catch { /* not json */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else reject(new ApiError(xhr.status, data?.error?.code || "upload_failed", data?.error?.message || "Upload failed."));
    };
    xhr.onerror = () => reject(new ApiError(0, "network", "The connection dropped during upload. Try again."));
    const fd = new FormData();
    fd.append("file", file, file.name);
    xhr.send(fd);
  });
}

export async function download(path: string, body: unknown, fallbackName: string) {
  if (BROWSER) { const l = await import("./local/server"); return l.download(path, body, fallbackName); }
  const res = await fetch(path, { method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(csrf ? { "X-CSRF-Token": csrf } : {}) }, body: JSON.stringify(body) });
  if (!res.ok) {
    const d = await res.json().catch(() => null);
    throw new ApiError(res.status, d?.error?.code || "export_failed", d?.error?.message || "Export failed.");
  }
  const blob = await res.blob();
  const cd = res.headers.get("content-disposition") || "";
  const name = /filename="([^"]+)"/.exec(cd)?.[1] || fallbackName;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}
