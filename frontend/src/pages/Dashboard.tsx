import { useEffect, useState } from "react";
import { api } from "../api";
import { linkTo, navigate } from "../router";
import { ErrorNote } from "../components/common";

interface Row { id: string; title: string; updated_at: string; video_status: string | null; concept_count: number }

function Thumb({ pid, ready }: { pid: string; ready: boolean }) {
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    if (!ready) return;
    api("GET", `/api/projects/${pid}/frames`).then((r) => {
      const f = r.frames.find((x: any) => x.is_keyframe) || r.frames[0];
      if (f) setSrc(f.url);
    }).catch(() => {});
  }, [pid, ready]);
  return <div className="reel-thumb">{src ? <img src={src} alt="" style={{ width: "100%", height: "100%", objectFit: "cover" }} /> : "No clip yet"}</div>;
}

const STATUS: Record<string, [string, string]> = {
  ready: ["ready", "Analysed"], validated: ["busy", "Queued"], ingesting: ["busy", "Preparing"], failed: ["failed", "Processing failed"],
};

export function Dashboard() {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { api("GET", "/api/projects").then((r) => setRows(r.projects)).catch((e) => setError(e.message)); }, []);
  return (
    <div className="page">
      <div className="row" style={{ marginBottom: 20 }}>
        <div className="grow">
          <h1>Your projects</h1>
          <p className="muted">Only you can see these. Nothing here has a public link.</p>
        </div>
        <button className="primary" onClick={() => navigate("/new")}>New project</button>
      </div>
      <ErrorNote error={error} />
      {rows && rows.length === 0 && (
        <div className="empty">
          <p>No projects yet. Start one with a short clip of a scene — a rehearsal, a phone test or a cut you want to rethink.</p>
          <a {...linkTo("/new")}>Create your first project</a>
        </div>
      )}
      <div>
        {rows?.map((p) => {
          const [cls, label] = p.video_status ? (STATUS[p.video_status] || ["", p.video_status]) : ["", "No clip"];
          return (
            <div className="reel" key={p.id}>
              <Thumb pid={p.id} ready={p.video_status === "ready"} />
              <div>
                <h3><a className="title" {...linkTo(`/p/${p.id}`)}>{p.title}</a></h3>
                <div className="row small">
                  <span className={`status ${cls}`}>{label}</span>
                  <span className="muted">{p.concept_count ? `${p.concept_count} concepts` : "No concepts yet"}</span>
                  <span className="faint">Updated {new Date(p.updated_at).toLocaleString()}</span>
                </div>
              </div>
              <a {...linkTo(`/p/${p.id}`)}>Open</a>
            </div>
          );
        })}
      </div>
    </div>
  );
}
