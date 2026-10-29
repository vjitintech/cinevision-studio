import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "../api";
import { linkTo } from "../router";
import type { Concept, Frame, Job, Project, Scene, Shot } from "../types";
import { ErrorNote, JobCard, usePoll } from "../components/common";
import { fmtTime } from "../previs/math";
import { AnalysisSection, AnalysisSummary } from "./sections/Analysis";
import { ConceptsSection } from "./sections/Concepts";
import { CameraSection } from "./sections/Camera";
import { LightingSection } from "./sections/Lighting";
import { StoryboardSection } from "./sections/Storyboard";
import { PrevisSection } from "./sections/Previs3D";
import { ShotListSection } from "./sections/ShotList";
import { SettingsSection } from "./sections/Settings";
import { UploadBox } from "./NewProject";
import { Planner, usePlanner } from "./planner";
import { subjectOffset, type CameraState } from "../previs/shot";
import type { Lighting } from "../types";

export const SECTIONS: [string, string][] = [
  ["analysis", "Video analysis"], ["concepts", "Cinematography concepts"], ["camera", "Camera planner"], ["lighting", "Lighting studio"],
  ["storyboard", "Storyboard & animatic"], ["previs", "3D previsualisation"], ["shotlist", "Shot list"], ["settings", "Project settings"],
];

export interface WS {
  pid: string; project: Project; analysis: any | null; frames: Frame[]; concepts: Concept[]; concept: Concept | null;
  scene: Scene | null; setScene: (s: Scene | null) => void;
  setConceptId: (id: string) => void; replaceConcept: (c: Concept) => void;
  shotIdx: number; setShotIdx: (i: number) => void; time: number; setTime: (t: number) => void; playing: boolean; setPlaying: (p: boolean) => void;
  shot: Shot | null; localT: number; reload: () => Promise<void>; reloadConcepts: () => Promise<void>; activeJobs: Job[];
  frameAt: (t: number) => Frame | null; proxyUrl: string | null;
  camera: Planner<CameraState>; lighting: Planner<Lighting>;
  shotOffset: (s: Shot) => [number, number];
}
const Ctx = createContext<WS | null>(null);
export const useWS = () => useContext(Ctx)!;

export function Workspace({ pid, section }: { pid: string; section: string }) {
  const [project, setProject] = useState<Project | null>(null);
  const [analysis, setAnalysis] = useState<any | null>(null);
  const [frames, setFrames] = useState<Frame[]>([]);
  const [concepts, setConcepts] = useState<Concept[]>([]);
  const [conceptId, setConceptId] = useState<string | null>(null);
  const [sceneOverride, setScene] = useState<Scene | null>(null);
  const [shotIdx, setShotIdx] = useState(0);
  const [time, setTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [proxyUrl, setProxyUrl] = useState<string | null>(null);

  const loadFrames = useCallback(async () => {
    try { setFrames((await api("GET", `/api/projects/${pid}/frames`)).frames); } catch { /* none yet */ }
  }, [pid]);

  const reloadConcepts = useCallback(async () => {
    const r = await api("GET", `/api/projects/${pid}/concepts`);
    setConcepts(r.concepts);
    setConceptId((cur) => {
      if (cur && r.concepts.some((c: Concept) => c.id === cur)) return cur;
      const sel = r.concepts.find((c: Concept) => c.is_selected) || r.concepts[0];
      return sel ? sel.id : null;
    });
  }, [pid]);

  const reload = useCallback(async () => {
    try {
      const p: Project = await api("GET", `/api/projects/${pid}`);
      setProject(p);
      if (p.analysis_id) {
        if (!analysis || analysis.id !== p.analysis_id) {
          setAnalysis(await api("GET", `/api/projects/${pid}/analysis`));
          await loadFrames();
        }
      } else if (p.video?.status === "ready" && !frames.length) await loadFrames();
      if (p.concept_count) await reloadConcepts();
    } catch (e) { setError(e instanceof ApiError ? (e.status === 404 ? "This project doesn't exist or isn't yours." : e.message) : "Could not load the project."); }
  }, [pid, analysis, frames.length, loadFrames, reloadConcepts]);

  useEffect(() => { reload(); /* eslint-disable-next-line */ }, [pid]);
  const activeJobs = (project?.jobs || []).filter((j) => j.status === "queued" || j.status === "processing");
  const prevActive = useRef(0);
  usePoll(reload, activeJobs.length > 0, 1500);
  useEffect(() => {
    if (prevActive.current > 0 && activeJobs.length === 0) { reloadConcepts().catch(() => {}); }
    prevActive.current = activeJobs.length;
  }, [activeJobs.length, reloadConcepts]);
  // frame/proxy tokens expire after 5 minutes; refresh them every 4
  usePoll(() => { loadFrames(); setProxyUrl(null); }, !!project?.video, 240000);

  useEffect(() => {
    const bid = project?.video?.proxy_blob_id;
    if (!bid || proxyUrl) return;
    api("POST", `/api/projects/${pid}/media-token`, { blob_id: bid }).then((r) => setProxyUrl(r.url)).catch(() => {});
  }, [project?.video?.proxy_blob_id, proxyUrl, pid]);

  const concept = concepts.find((c) => c.id === conceptId) || null;
  useEffect(() => { setScene(null); setShotIdx(0); setTime(0); }, [concept?.lineage_id]);
  useEffect(() => {
    if (!concept) return;
    api("GET", `/api/concepts/${concept.id}/scene3d`).then((r) => setScene(r.derived ? null : r.scene)).catch(() => {});
  }, [concept?.id]);

  const shots = concept?.data.shots || [];
  const total = shots.reduce((a, s) => a + s.duration_s, 0);
  // playback clock
  useEffect(() => {
    if (!playing || !total) return;
    let last = performance.now(), raf = 0;
    const tick = (now: number) => {
      const dt = (now - last) / 1000; last = now;
      setTime((t) => (t + dt >= total ? 0 : t + dt));
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, total]);
  // derive current shot from time
  const cur = useMemo(() => {
    let acc = 0;
    for (const s of shots) { if (time < acc + s.duration_s) return { shot: s, localT: (time - acc) / s.duration_s }; acc += s.duration_s; }
    return shots.length ? { shot: shots[shots.length - 1], localT: 1 } : { shot: null, localT: 0 };
  }, [time, shots]);
  useEffect(() => { if (cur.shot && cur.shot.index !== shotIdx && playing) setShotIdx(cur.shot.index); }, [cur.shot, playing, shotIdx]);

  const selectShot = (i: number) => {
    setShotIdx(i);
    const start = shots.slice(0, i).reduce((a, s) => a + s.duration_s, 0);
    setTime(start + (shots[i]?.duration_s || 0) * 0.5);
  };

  const frameAt = useCallback((t: number) => {
    if (!frames.length) return null;
    return frames.reduce((best, f) => (Math.abs(f.t_s - t) < Math.abs(best.t_s - t) ? f : best), frames[0]);
  }, [frames]);

  const centerRef = useRef<HTMLElement>(null);
  useEffect(() => { centerRef.current?.scrollTo(0, 0); }, [section]);
  const shotForPlanner = (shots.find((s) => s.index === shotIdx) || shots[0])?.index;
  const camera = usePlanner<CameraState>("camera", concept?.id, shotForPlanner);
  const lighting = usePlanner<Lighting>("lighting", concept?.id, shotForPlanner);

  if (error) return <div className="page"><ErrorNote error={error} /><p><a {...linkTo("/")}>Back to projects</a></p></div>;
  if (!project) return <div className="empty">Loading project…</div>;

  const shot = shots.find((s) => s.index === shotIdx) || shots[0] || null;
  const ws: WS = {
    pid, project, analysis, frames, concepts, concept, scene: sceneOverride || concept?.data.scene || null, setScene,
    setConceptId: (id) => setConceptId(id),
    replaceConcept: (c) => { setConcepts((cs) => cs.map((x) => (x.lineage_id === c.lineage_id ? c : x))); setConceptId(c.id); },
    shotIdx: shot?.index ?? 0, setShotIdx: selectShot, time, setTime, playing, setPlaying, shot,
    localT: cur.shot && shot && cur.shot.index === shot.index ? cur.localT : 0.5, reload, reloadConcepts, activeJobs, frameAt, proxyUrl,
    camera, lighting,
    shotOffset: (s) => (concept ? subjectOffset(concept.data.scene, sceneOverride, s) : [0, 0]),
  };

  const needsClip = !project.video;
  const Section = { analysis: AnalysisSection, concepts: ConceptsSection, camera: CameraSection, lighting: LightingSection,
    storyboard: StoryboardSection, previs: PrevisSection, shotlist: ShotListSection, settings: SettingsSection }[section] || AnalysisSection;

  return (
    <Ctx.Provider value={ws}>
      <div className="ws">
        <nav className="ws-tabs" aria-label="Project sections">
          <span className="ptitle" title={project.title}>{project.title}</span>
          {SECTIONS.map(([k, label]) => (
            <a key={k} {...linkTo(`/p/${pid}/${k}`)} aria-current={section === k ? "page" : undefined}>{label}</a>
          ))}
        </nav>
        <aside className="ws-left" aria-label="Original clip">
          <LeftPanel />
        </aside>
        <section className="ws-center" ref={centerRef}>
          {needsClip && section !== "settings" ? (
            <div className="stack" style={{ maxWidth: 640 }}>
              <h2>Add a clip to begin</h2>
              <p className="muted">Concepts are built from what's actually in your footage, so analysis needs a clip first.</p>
              <UploadBox pid={pid} onUploaded={reload} />
            </div>
          ) : <Section.Center />}
        </section>
        <aside className="ws-right" aria-label="Controls">
          {activeJobs.map((j) => <JobCard key={j.id} job={j} onChange={reload} />)}
          {(project.jobs || []).filter((j) => j.status === "failed").slice(0, 1).map((j) => <JobCard key={j.id} job={j} onChange={reload} />)}
          <Section.Right />
        </aside>
        <footer className="ws-bottom" aria-label="Shot sequence and playback">
          <BottomPanel />
        </footer>
      </div>
    </Ctx.Provider>
  );
}

function LeftPanel() {
  const ws = useWS();
  const v = ws.project.video;
  const vref = useRef<HTMLVideoElement>(null);
  const [curFrame, setCurFrame] = useState<string | null>(null);
  const shots = ws.analysis?.data?.shots || [];
  const dur = ws.analysis?.data?.clip?.duration_s?.value || v?.metadata?.duration_s || 1;
  const [vt, setVt] = useState(0);
  return (
    <div>
      <div className="panel-h">Original clip {v && <span className="faint small">{v.metadata ? `${v.metadata.width}×${v.metadata.height} · ${v.metadata.fps} fps` : ""}</span>}</div>
      {v?.proxy_blob_id && ws.proxyUrl ? (
        <video ref={vref} className="clip" src={ws.proxyUrl} controls preload="metadata" playsInline onTimeUpdate={(e) => setVt(e.currentTarget.currentTime)} />
      ) : v ? <div className="reel-thumb" style={{ width: "100%" }}>{v.status === "failed" ? "Processing failed" : "Preparing preview…"}</div>
        : <div className="small muted">No clip uploaded.</div>}
      {shots.length > 0 && (
        <>
          <div className="panel-h">Scene timeline <span className="faint small">{shots.length} shot{shots.length > 1 ? "s" : ""}</span></div>
          <div className="timeline" role="list">
            {shots.map((s: any) => (
              <div key={s.index} role="listitem" className={`seg ${vt >= s.start_s && vt < s.end_s ? "on" : ""}`} style={{ width: `${((s.end_s - s.start_s) / dur) * 100}%` }}
                title={`Shot ${s.index + 1}: ${fmtTime(s.start_s)}–${fmtTime(s.end_s)}`}
                onClick={() => { if (vref.current) vref.current.currentTime = s.start_s; }}>{s.index + 1}</div>
            ))}
            <div className="head" style={{ left: `${(vt / dur) * 100}%` }} />
          </div>
        </>
      )}
      {ws.frames.length > 0 && (
        <>
          <div className="panel-h">Keyframes <span className="faint small">outlined = representative</span></div>
          <div className="strip">
            {ws.frames.slice(0, 40).map((f) => (
              <button key={f.id} className={`${f.is_keyframe ? "key" : ""} ${curFrame === f.id ? "on" : ""}`} title={`${fmtTime(f.t_s)}${f.shot_index != null ? ` · shot ${f.shot_index + 1}` : ""}`}
                onClick={() => { setCurFrame(f.id); if (vref.current) vref.current.currentTime = f.t_s; }}>
                <img src={f.url} alt={`Frame at ${fmtTime(f.t_s)}`} loading="lazy" />
              </button>
            ))}
          </div>
        </>
      )}
      {ws.analysis && <AnalysisSummary />}
    </div>
  );
}

function BottomPanel() {
  const ws = useWS();
  const c = ws.concept;
  if (!c) return <div className="small muted">Generate concepts to see a shot sequence and play it back here.</div>;
  const shots = c.data.shots;
  const total = shots.reduce((a, s) => a + s.duration_s, 0);
  return (
    <div className="stack" style={{ marginTop: 0 }}>
      <div className="row">
        <button className="primary" onClick={() => ws.setPlaying(!ws.playing)} aria-label={ws.playing ? "Pause" : "Play"}>{ws.playing ? "Pause" : "Play"}</button>
        <span className="tc small">{fmtTime(ws.time)} / {fmtTime(total)}</span>
        <input className="grow" type="range" min={0} max={total} step={0.02} value={ws.time} aria-label="Scrub through the concept"
          onChange={(e) => { ws.setPlaying(false); ws.setTime(+e.target.value); }} style={{ minWidth: 160 }} />
        <span className="small muted">{c.data.name}{c.revision > 1 ? ` · rev ${c.revision}` : ""}</span>
      </div>
      <div className="timeline" role="list" aria-label="Shot sequence">
        {shots.map((s) => (
          <div key={s.index} role="listitem" className={`seg ${ws.shotIdx === s.index ? "on" : ""}`} style={{ width: `${(s.duration_s / total) * 100}%` }}
            title={`${s.size} · ${s.lens.focal_mm} mm · ${s.movement.type} · ${s.lighting.key_to_fill_ratio}`} onClick={() => ws.setShotIdx(s.index)}>
            {s.index + 1} · {s.size} · {s.movement.type.split(" (")[0]}
          </div>
        ))}
        <div className="head" style={{ left: `${(ws.time / total) * 100}%` }} />
      </div>
    </div>
  );
}
