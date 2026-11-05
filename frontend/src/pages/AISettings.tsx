import { useEffect, useState } from "react";
import { api, ApiError, BROWSER } from "../api";
import { ErrorNote, Labeled } from "../components/common";
import { LANGS, useT } from "../i18n";

export function AISettingsPage() {
  const { t, lang, setLang } = useT();
  const [st, setSt] = useState<any>(null);
  const [hw, setHw] = useState<any>(null);
  const [cfg, setCfg] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (BROWSER) return;
    api("GET", "/api/ai/local").then((s) => { setSt(s); setCfg(s.config); }).catch(() => {});
    api("GET", "/api/system/hardware").then(setHw).catch(() => {});
  }, []);

  const save = async () => {
    setBusy(true); setError(null);
    try { const s = await api("PUT", "/api/ai/local", cfg); setSt(s); setCfg(s.config); }
    catch (e) { setError(e instanceof ApiError ? e.message : "Could not save."); }
    finally { setBusy(false); }
  };

  return (
    <div className="page" style={{ maxWidth: 820 }}>
      <h1>{t("settings_title")}</h1>
      <h2>{t("settings_lang")}</h2>
      <div className="lang-grid" role="radiogroup" aria-label={t("language")}>
        {LANGS.map((l) => (
          <button key={l.code} role="radio" aria-checked={lang === l.code} className={lang === l.code ? "primary" : ""} onClick={() => setLang(l.code)} lang={l.code}>{l.name}</button>
        ))}
      </div>

      {!BROWSER && (
        <>
          <h2 style={{ marginTop: 32 }}>{t("settings_ai")}</h2>
          <p className="muted">{t("settings_ai_intro")}</p>
          {hw && (
            <div className="concept" style={{ padding: 14 }}>
              <h4 style={{ marginTop: 0, color: "var(--text)" }}>{t("your_computer")}</h4>
              <p className="small">{hw.os} · {hw.cpu_count} CPU threads · {hw.ram_gb ?? "?"} GB RAM{hw.gpus?.length ? ` · ${hw.gpus.map((g: any) => `${g.name} (${g.vram_gb} GB)`).join(", ")}` : " · no NVIDIA GPU found"}</p>
              <h4 style={{ color: "var(--text)" }}>{t("recommended")}</h4>
              <ul className="small">
                {hw.recommendations.map((r: any, i: number) => (
                  <li key={i}>{r.model ? <><b>{r.model}</b> — {r.role}{r.download_gb ? ` · ~${r.download_gb} GB download` : ""}. </> : null}{r.text}
                    {r.colibri && <div className="faint">colibri: <code>{r.colibri}</code>{r.ollama ? <> · Ollama: <code>{r.ollama}</code></> : null}</div>}</li>
                ))}
              </ul>
              <details>
                <summary>{t("how_to_install")}</summary>
                <ol className="small">
                  <li>Download colibri: github.com/JustVugg/colibri → <b>Code → Download ZIP</b>, and unzip it.</li>
                  <li>Double-click <code>START-HERE.bat</code>. It checks your RAM, disk and graphics card and suggests a model. Say yes to the download (it is large).</li>
                  <li>CineVision already uses port 8000, so start colibri on 8800: open PowerShell in the colibri folder and run<br /><code>py -3 c\coli start --port 8800 --background --no-browser</code></li>
                  <li>Check it: <code>py -3 c\coli status</code> should say <i>ready</i>.</li>
                  <li>Here, choose <b>colibri</b>, tick <b>{t("enable_ai")}</b>, then <b>{t("test_connection")}</b>.</li>
                </ol>
                <p className="small faint">Full guide: {hw.guide}</p>
              </details>
            </div>
          )}

          {st && cfg && (
            <div className="stack" style={{ marginTop: 16 }}>
              {!st.can_edit && <p className="notice small">{t("only_owner")}</p>}
              <div className="row">
                {Object.entries(st.presets).map(([k, p]: [string, any]) => (
                  <button key={k} disabled={!st.can_edit} className={cfg.provider === k ? "primary" : ""}
                    onClick={() => setCfg({ ...cfg, provider: k, url: p.url })} title={p.help}>{p.label}</button>
                ))}
              </div>
              <label className="row" style={{ gap: 8 }}><input type="checkbox" disabled={!st.can_edit} checked={cfg.enabled} onChange={(e) => setCfg({ ...cfg, enabled: e.target.checked })} /> {t("enable_ai")}</label>
              <Labeled label={t("ai_server")}><input disabled={!st.can_edit} value={cfg.url} onChange={(e) => setCfg({ ...cfg, url: e.target.value })} /></Labeled>
              <div className="grid2">
                <Labeled label={t("ai_model_text")}>
                  <select disabled={!st.can_edit} value={cfg.model} onChange={(e) => setCfg({ ...cfg, model: e.target.value })}>
                    <option value="">{t("auto")}</option>
                    {[...new Set([cfg.model, ...st.models].filter(Boolean))].map((m: string) => <option key={m} value={m}>{m}</option>)}
                  </select>
                </Labeled>
                <Labeled label={t("ai_model_vision")}>
                  <select disabled={!st.can_edit} value={cfg.vision_model} onChange={(e) => setCfg({ ...cfg, vision_model: e.target.value })}>
                    <option value="">{t("auto")}</option>
                    {[...new Set([cfg.vision_model, ...st.models].filter(Boolean))].map((m: string) => <option key={m} value={m}>{m}</option>)}
                  </select>
                </Labeled>
              </div>
              <div className="row"><button className="primary" disabled={busy || !st.can_edit} onClick={save}>{t("test_connection")}</button>
                <span className={`status ${st.ready ? "ready" : st.config.enabled ? "failed" : ""}`}>{st.message}</span></div>
              <ErrorNote error={error} />
            </div>
          )}
        </>
      )}
    </div>
  );
}
