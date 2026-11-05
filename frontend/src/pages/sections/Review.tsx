import { useCallback, useEffect, useState } from "react";
import { api, ApiError, BROWSER } from "../../api";
import { linkTo } from "../../router";
import { useT } from "../../i18n";
import { usePoll } from "../../components/common";
import { useWS } from "../Workspace";

interface Finding { code: string; status: string; basis: string; confidence: number; observation: string; suggestion?: string | null; why?: string | null }
interface Dept { id: string; title: string; status: string; status_label: string; findings: Finding[]; score: number | null }
interface Review {
  language: string; rtl: boolean; verdict: string; verdict_label: string; summary: string; departments: Dept[];
  edit_options: { id: string; name: string; idea: string; best_for: string; steps: string[] }[];
  alternatives: { concept_id: string; name: string; match: number }[];
  ai: any | null; ai_available: boolean; ai_languages: string[]; created_at: string;
}

const BASIS: Record<string, "measured" | "estimated" | "interpretation"> = { observed: "measured", estimated: "estimated", heuristic: "interpretation" };

function useReview() {
  const ws = useWS();
  const { lang } = useT();
  const [review, setReview] = useState<Review | null>(null);
  const [missing, setMissing] = useState(false);
  const load = useCallback(async () => {
    if (BROWSER) { setMissing(true); return; }
    try {
      const r = await api("GET", `/api/projects/${ws.pid}/review?lang=${lang}`);
      if (r.ready) { setReview(r); setMissing(false); } else setMissing(true);
    }
    catch (e) { if (e instanceof ApiError && e.status === 404) setMissing(true); }
  }, [ws.pid, lang]);
  useEffect(() => { load(); }, [load, ws.project.updated_at]);
  usePoll(load, ws.activeJobs.length > 0 || (missing && !!ws.analysis), 2500);
  return { review, missing, load };
}

export const ReviewSection = {
  Center() {
    const ws = useWS();
    const { t } = useT();
    const { review } = useReview();
    if (BROWSER) {
      return <div className="stack" style={{ maxWidth: 640 }}><h2>{t("sec_review")}</h2>
        <p className="muted">The director's review runs in the full (self-hosted) edition. This browser edition shows the analysis and concepts.</p>
        <a {...linkTo(`/p/${ws.pid}/analysis`)}>{t("sec_analysis")}</a></div>;
    }
    if (!review) {
      return <div className="stack" style={{ maxWidth: 640 }}><h2>{t("sec_review")}</h2>
        <p className="muted">{ws.project.video ? t("review_waiting") : t("review_none")}</p></div>;
    }
    const cls = review.verdict === "good" ? "verdict good" : review.verdict === "good_with_fixes" ? "verdict fixes" : "verdict alt";
    const vt = review.verdict === "good" ? t("verdict_good") : review.verdict === "good_with_fixes" ? t("verdict_fixes") : t("verdict_alt");
    const ai = review.ai && review.ai.status === "used" ? review.ai : null;
    return (
      <div className="stack review" dir={review.rtl ? "rtl" : undefined} lang={review.language}>
        <h2 style={{ margin: 0 }}>{t("sec_review")}</h2>
        <p className="muted small">{t("review_intro")}</p>
        <section className={cls} aria-label="Verdict">
          <strong style={{ fontSize: 17 }}>{vt}</strong>
          <div className="small" style={{ marginTop: 4 }}>{review.summary}</div>
          {ai?.overall && <p style={{ margin: "8px 0 0" }}>{ai.overall}</p>}
        </section>

        <div className="dept-grid">
          {review.departments.map((d) => (
            <a key={d.id} href={`#dept-${d.id}`} className={`dept-chip ${d.status}`} title={d.status_label}>
              <span>{d.title}</span><b>{t(`status_${d.status}` as any)}</b>
            </a>
          ))}
        </div>

        {ai && (
          <section className="concept ai-notes" aria-label={t("ai_notes")}>
            <div className="row"><h3 className="grow" style={{ margin: 0 }}>{t("ai_notes")}</h3>
              <span className="small faint">{t("ai_model", { model: ai.model, frames: ai.frames_sent })}</span></div>
            {ai.story && <p>{ai.story}</p>}
            {ai.alternative_ways?.length > 0 && <><h4>{t("other_ways")}</h4><ul>{ai.alternative_ways.map((x: string) => <li key={x}>{x}</li>)}</ul></>}
            {ai.edit_ideas?.length > 0 && <><h4>{t("edit_ideas")}</h4><ul>{ai.edit_ideas.map((x: string) => <li key={x}>{x}</li>)}</ul></>}
            {ai.learn?.length > 0 && <details><summary>{t("learn_terms")}</summary><dl className="small">{ai.learn.map((x: any) => <div key={x.term}><dt><b>{x.term}</b></dt><dd>{x.meaning}</dd></div>)}</dl></details>}
            <p className="small faint">{ai.note}</p>
          </section>
        )}

        {review.departments.map((d) => {
          const aiDep = ai?.departments?.[d.id] || (d.id === "camera" ? ai?.departments?.performance_and_blocking : null);
          return (
            <section key={d.id} id={`dept-${d.id}`} className={`concept dept ${d.status}`}>
              <div className="row"><h3 className="grow" style={{ margin: 0 }}>{d.title}</h3><span className={`dept-status ${d.status}`}>{t(`status_${d.status}` as any)}</span></div>
              {d.findings.length === 0 && <p className="small muted">{t("status_unknown")}</p>}
              {d.findings.map((f, i) => (
                <div key={f.code + i} className={`finding ${f.status}`}>
                  <div>{f.observation} <span className={`basis ${f.basis}`}>{t(BASIS[f.basis] || "estimated")}</span></div>
                  {f.suggestion && <div className="sug"><b>{t("suggestion")}:</b> {f.suggestion}</div>}
                  {f.why && <details className="why"><summary>{t("why_learn")}</summary>{f.why}</details>}
                </div>
              ))}
              {aiDep && (aiDep.works?.length > 0 || aiDep.improve?.length > 0) && (
                <div className="ai-dep small">
                  {aiDep.works?.length > 0 && <div><b>AI · {t("what_works")}:</b> {aiDep.works.join(" · ")}</div>}
                  {aiDep.improve?.length > 0 && <div><b>AI · {t("what_to_improve")}:</b> {aiDep.improve.join(" · ")}</div>}
                </div>
              )}
            </section>
          );
        })}

        <h3 id="edit-options">{t("edit_options")}</h3>
        <div className="grid2">
          {review.edit_options.map((o) => (
            <div key={o.id} className="concept" style={{ padding: 14 }}>
              <h4 style={{ color: "var(--text)", marginTop: 0 }}>{o.name}</h4>
              <p className="small">{o.idea}</p>
              <ol className="small">{o.steps.map((s) => <li key={s}>{s}</li>)}</ol>
              <div className="small faint">{t("best_for")}: {o.best_for}</div>
            </div>
          ))}
        </div>

        {review.alternatives.length > 0 && (
          <>
            <h3>{t("alternatives")}</h3>
            <ul className="small">{review.alternatives.map((a) => <li key={a.concept_id}>{a.name} — {t("match", { n: a.match })}</li>)}</ul>
            <a {...linkTo(`/p/${ws.pid}/concepts`)}>{t("open_concepts")}</a>
          </>
        )}
      </div>
    );
  },
  Right() {
    const ws = useWS();
    const { t, lang } = useT();
    const { review, load } = useReview();
    const [busy, setBusy] = useState(false);
    if (BROWSER) return null;
    const again = async () => { setBusy(true); try { await api("POST", `/api/projects/${ws.pid}/review`, { lang }); await ws.reload(); await load(); } finally { setBusy(false); } };
    return (
      <div className="stack">
        <div className="panel-h">{t("sec_review")}</div>
        {review && !review.ai_available && <p className="small muted">{t("ai_off")} <a {...linkTo("/settings")}>{t("ai_set_up")}</a></p>}
        {review?.ai?.status === "failed" && <p className="small" style={{ color: "var(--danger)" }}>{t("ai_failed")} {review.ai.error}</p>}
        {review && <button disabled={busy || ws.activeJobs.length > 0} onClick={again}>{review.ai_available ? t("ai_again") : t("review_again")}</button>}
        <div><a {...linkTo(`/p/${ws.pid}/analysis`)}>{t("sec_analysis")}</a></div>
        <div><a {...linkTo(`/p/${ws.pid}/concepts`)}>{t("sec_concepts")}</a></div>
        <div><a {...linkTo(`/p/${ws.pid}/previs`)}>{t("sec_previs")}</a></div>
      </div>
    );
  },
};
