import { useEffect, useState } from "react";
import { api, BROWSER } from "../api";
import { useT } from "../i18n";

export function LearnPage() {
  const { t, lang } = useT();
  const [d, setD] = useState<any>(null);
  const [open, setOpen] = useState<string | null>(null);
  useEffect(() => { if (!BROWSER) api("GET", `/api/learn?lang=${lang}`).then(setD).catch(() => {}); }, [lang]);
  if (BROWSER) return <div className="page"><p>{t("asm_full_only")}</p></div>;
  if (!d) return <div className="empty">…</div>;
  return (
    <div className="page learn" style={{ maxWidth: 900 }} dir={lang === "ar" ? "rtl" : undefined}>
      <h1>{t("learn_title")}</h1>
      <p className="muted">{t("learn_intro")}</p>
      {d.focus.length > 0 ? (
        <div className="notice"><b>{t("learn_focus")}</b> {d.focus.map((id: string) => d.chapters.find((c: any) => c.id === id)?.title).join(" · ")}</div>
      ) : <p className="small faint">{t("learn_none")}</p>}
      {d.chapters.map((c: any) => (
        <section key={c.id} className="concept chapter">
          <button className="ghost chapter-h" aria-expanded={open === c.id} onClick={() => setOpen(open === c.id ? null : c.id)}>
            <h2 style={{ margin: 0 }}>{c.title}</h2>
            {c.to_practise > 0 && <span className="chip warm">{t("learn_seen", { n: c.to_practise })}</span>}
          </button>
          {open === c.id && (
            <div className="stack">
              <div className="exercise"><b>{t("learn_exercise")}</b> {c.exercise}</div>
              {c.lessons.map((l: any) => (
                <div key={l.code} className={`lesson ${l.to_practise ? "practise" : ""}`}>
                  <div className="small faint">{t("learn_symptom")}: {l.symptom}</div>
                  <div><b>{t("learn_why")}</b> {l.why}</div>
                  {l.do && <div className="small"><b>{t("learn_do")}</b> {l.do}</div>}
                  {l.to_practise > 0 && <div className="small" style={{ color: "var(--tungsten)" }}>{t("learn_seen", { n: l.to_practise })}</div>}
                </div>
              ))}
            </div>
          )}
        </section>
      ))}
    </div>
  );
}
