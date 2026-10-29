import { useState } from "react";
import { api } from "../../api";
import { Fact, Legend, Swatches } from "../../components/common";
import { fmtTime } from "../../previs/math";
import { linkTo } from "../../router";
import { useWS } from "../Workspace";

export function AnalysisSummary() {
  const a = useWS().analysis?.data;
  if (!a) return null;
  return (
    <div style={{ marginTop: 16 }}>
      <div className="panel-h">Scene at a glance</div>
      <Swatches colors={a.color.palette.value.map((p: any) => p.hex)} />
      <div className="small" style={{ marginTop: 8 }}>
        <div>{a.environment.setting.value}, {a.environment.time_of_day.value}</div>
        <div className="muted">{a.color.tonal_key.value} · {a.color.contrast.value.label} contrast · {a.color.color_temperature.value.cast} cast</div>
        <div className="muted">{a.narrative.pacing.value.label}</div>
      </div>
    </div>
  );
}

export const AnalysisSection = {
  Center() {
    const ws = useWS();
    const a = ws.analysis?.data;
    if (!a) {
      return (
        <div className="stack" style={{ maxWidth: 620 }}>
          <h2>Video analysis</h2>
          <p className="muted">{ws.activeJobs.length ? "Your clip is being processed. Results appear here as soon as they are measured — nothing is shown before then." :
            "No analysis is available yet."}</p>
        </div>
      );
    }
    return (
      <div className="stack">
        <div className="row"><h2 className="grow" style={{ margin: 0 }}>Video analysis</h2><Legend /></div>
        <p className="muted small">Each value shows how it was obtained. Solid outlines were measured; dashed ones were estimated by a model; dotted ones are interpretations to check against your intent.</p>
        <div className="grid3">
          <Fact label="Duration" t={a.clip.duration_s} render={(v) => `${v.toFixed(2)} s`} />
          <Fact label="Resolution & rate" t={{ ...a.clip.resolution, value: `${a.clip.resolution.value} · ${a.clip.fps.value} fps` }} />
          <Fact label="Shots detected" t={{ ...a.continuity, value: a.continuity.value.shot_count }} />
        </div>

        <h3>Shots</h3>
        {a.shots.map((s: any) => {
          const f = ws.frameAt(s.representative_t);
          return (
            <div key={s.index} className="concept" style={{ padding: 14 }}>
              <div className="row" style={{ alignItems: "flex-start" }}>
                {f && <img src={f.url} alt={`Shot ${s.index + 1}`} style={{ width: 220, borderRadius: 4 }} />}
                <div className="grow" style={{ minWidth: 240 }}>
                  <h4 style={{ color: "var(--text)" }}>Shot {s.index + 1} <span className="faint tc">{fmtTime(s.start_s)}–{fmtTime(s.end_s)}</span></h4>
                  <div className="grid2">
                    <Fact label="Shot size" t={s.shot_size} />
                    <Fact label="Camera angle" t={s.camera_angle} />
                    <Fact label="Camera movement" t={s.movement.movement} />
                    {s.movement.camera_vs_subject && <Fact label="Camera vs subject motion" t={s.movement.camera_vs_subject}
                      render={(v) => `camera ${v.camera_motion.toFixed(3)} · subject ${v.subject_motion.toFixed(3)} (dominant: ${v.dominant})`} />}
                    <Fact label="Camera height" t={s.camera_height} />
                    <Fact label="Visible subjects" t={s.subjects.count} />
                  </div>
                  <details>
                    <summary>Composition</summary>
                    <div className="grid2">
                      <Fact label="Subject placement" t={s.composition.placement} />
                      <Fact label="Negative space" t={s.composition.negative_space} render={(v) => `${Math.round(v * 100)}% of frame`} />
                      <Fact label="Symmetry" t={s.composition.symmetry} render={(v) => `${Math.round(v * 100)}%`} />
                      <Fact label="Leading lines toward subject" t={s.composition.leading_lines} />
                      <Fact label="Perspective / lens character" t={s.composition.perspective} />
                      <Fact label="Horizon roll" t={s.composition.horizon_roll_deg} render={(v) => (v == null ? "no clear horizon" : `${v}°`)} />
                      <Fact label="Depth of field" t={s.composition.depth_of_field} />
                      <Fact label="Approximate placement (previs)" t={s.scene_geometry}
                        render={(v) => v.subjects_m.length ? v.subjects_m.map((p: any) => `${p.x} m across, ${p.z} m away`).join("; ") : "no people to place"} />
                    </div>
                  </details>
                </div>
              </div>
            </div>
          );
        })}

        <h3>Light and colour</h3>
        <Swatches colors={a.color.palette.value.map((p: any) => p.hex)} />
        <div className="grid3">
          <Fact label="Tonal key" t={a.color.tonal_key} />
          <Fact label="Contrast" t={a.color.contrast} render={(v) => `${v.label} (${Math.round(v.range_p5_p95 * 100)}% range)`} />
          <Fact label="Mean brightness" t={a.color.mean_luminance} render={(v) => `${Math.round(v * 100)}%`} />
          <Fact label="Colour cast" t={a.color.color_temperature} render={(v) => `${v.cast}, ${v.approx}`} />
          <Fact label="Saturation" t={a.color.saturation} render={(v) => v.label} />
          <Fact label="Clipping" t={a.color.clipping} render={(v) => `${(v.highlights * 100).toFixed(1)}% highlights, ${(v.shadows * 100).toFixed(1)}% shadows`} />
        </div>

        <h3>Environment</h3>
        <div className="grid3">
          <Fact label="Setting" t={a.environment.setting} />
          <Fact label="Time of day" t={a.environment.time_of_day} />
          <Fact label="Practical lights" t={a.environment.practical_lights} render={(v) => v.length ? `${v.length} candidate${v.length > 1 ? "s" : ""} (${v.map((p: any) => p.tint).join(", ")})` : "none found"} />
        </div>

        <h3>Story and emotion</h3>
        <div className="grid2">
          <Fact label="Pacing" t={a.narrative.pacing} render={(v) => `${v.label} — ${v.shots} shot(s), average ${v.average_shot_s}s`} />
          <Fact label="Mood" t={a.narrative.mood} />
          <Fact label="Spatial relationships" t={a.narrative.spatial_relationships} />
          <Fact label="Apparent purpose" t={a.narrative.apparent_purpose} />
        </div>
        <div className="notice">
          <strong>Limits of this analysis</strong>
          <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>{a.limitations.map((l: string) => <li key={l} className="small">{l}</li>)}</ul>
        </div>
      </div>
    );
  },
  Right() {
    const ws = useWS();
    const [busy, setBusy] = useState(false);
    const rerun = async () => { setBusy(true); try { await api("POST", `/api/projects/${ws.pid}/analysis/rerun`); await ws.reload(); } finally { setBusy(false); } };
    return (
      <div className="stack">
        <div className="panel-h">Analysis</div>
        {ws.analysis && <p className="small muted">Engine {ws.analysis.engine?.id} v{ws.analysis.engine?.version} · {new Date(ws.analysis.created_at).toLocaleString()}</p>}
        <button disabled={busy || !!ws.activeJobs.length || ws.project.video?.status !== "ready"} onClick={rerun}>Run analysis again</button>
        {ws.analysis && <a {...linkTo(`/p/${ws.pid}/concepts`)}>Next: generate concepts</a>}
        <p className="small faint">Analysis runs entirely on this server with OpenCV and FFmpeg. No frames are sent anywhere.</p>
      </div>
    );
  },
};
