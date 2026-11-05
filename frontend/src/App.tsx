import { useCallback, useEffect, useMemo, useState } from "react";
import { api, setCsrf, setUnauthenticatedHandler } from "./api";
import { linkTo, navigate, useRoute } from "./router";
import { AuthPage } from "./pages/Auth";
import { Dashboard } from "./pages/Dashboard";
import { NewProject } from "./pages/NewProject";
import { Workspace } from "./pages/Workspace";
import { SecurityPage } from "./pages/Security";
import { BrowserPrivacyPage } from "./pages/BrowserPrivacy";
import { AISettingsPage } from "./pages/AISettings";
import { LangContext, LANGS, makeT } from "./i18n";

export interface Session {
  authenticated: boolean; mfa_required: boolean; registration_open: boolean; mode: string;
  user: { id: string; email: string; mfa_enabled: boolean } | null; csrf_token: string | null;
}

function storedLang(): string {
  try { return localStorage.getItem("cv-lang") || "en"; } catch { return "en"; }
}

export function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [path, navCount] = useRoute();
  const [lang, setLangState] = useState<string>(storedLang);
  const setLang = useCallback((l: string) => {
    setLangState(l);
    try { localStorage.setItem("cv-lang", l); } catch { /* private mode */ }
    if (__EDITION__ !== "browser") api("PUT", "/api/me/preferences", { language: l }).catch(() => {});
  }, []);
  const t = useMemo(() => makeT(lang), [lang]);
  useEffect(() => {
    document.documentElement.lang = lang;
    document.documentElement.dir = LANGS.find((x) => x.code === lang)?.rtl ? "rtl" : "ltr";
  }, [lang]);

  const refresh = useCallback(async () => {
    const s = await api<Session>("GET", "/api/auth/session");
    setCsrf(s.csrf_token);
    setSession(s);
  }, []);

  useEffect(() => { refresh().catch(() => setSession(null)); }, [refresh]);
  useEffect(() => {
    if (!session?.authenticated || __EDITION__ === "browser") return;
    api("GET", "/api/me/preferences").then((p) => {
      // the server remembers the choice across devices; a choice made on this device before signing in wins once
      const local = (() => { try { return localStorage.getItem("cv-lang"); } catch { return null; } })();
      if (local && local !== p.language) api("PUT", "/api/me/preferences", { language: local }).catch(() => {});
      else setLangState(p.language);
    }).catch(() => {});
  }, [session?.authenticated]);
  useEffect(() => { setUnauthenticatedHandler(() => { refresh(); }); }, [refresh]);

  const ctx = { lang, setLang, t };
  if (!session) return <div className="empty">Loading…</div>;
  if (!session.authenticated) return <LangContext.Provider value={ctx}><AuthPage session={session} onDone={refresh} /></LangContext.Provider>;

  const logout = async () => { await api("POST", "/api/auth/logout"); navigate("/", true); refresh(); };
  const m = path.match(/^\/p\/([0-9a-f-]{36})(?:\/([a-z0-9-]+))?/);
  let page: React.ReactNode;
  if (m) page = <Workspace pid={m[1]} section={m[2] || "review"} />;
  else if (path === "/new") page = <NewProject key={navCount} />;
  else if (path === "/settings") page = <AISettingsPage />;
  else if (path === "/security") page = session.mode === "browser" ? <BrowserPrivacyPage /> : <SecurityPage session={session} onChange={refresh} />;
  else page = <Dashboard />;

  return (
    <LangContext.Provider value={ctx}>
    <div className="shell">
      <header className="topbar">
        <a className="brand" {...linkTo("/")}><span className="brand-mark" aria-hidden />CineVision Studio</a>
        <nav className="topnav" aria-label="Main">
          <a {...linkTo("/")} aria-current={path === "/" ? "page" : undefined}>{t("nav_dashboard")}</a>
          <a {...linkTo("/new")} aria-current={path === "/new" ? "page" : undefined}>{t("nav_new")}</a>
          <a {...linkTo("/settings")} aria-current={path === "/settings" ? "page" : undefined}>{t("nav_settings")}</a>
          <a {...linkTo("/security")} aria-current={path === "/security" ? "page" : undefined}>{t("nav_security")}</a>
        </nav>
        <div className="grow" />
        {session.mode === "browser" ? (
          <span className="small muted" title="Everything runs inside this browser tab and is stored only on this device">Browser edition · stays on this device</span>
        ) : (
          <>
            <span className="small muted" title={session.mode === "local" ? "Everything runs on this machine" : "Private server"}>
              {session.mode === "local" ? "Local mode" : "Private server"} · {session.user?.email}
            </span>
            <select className="lang-pick" aria-label={t("language")} value={lang} onChange={(e) => setLang(e.target.value)}>
              {LANGS.map((l) => <option key={l.code} value={l.code}>{l.name}</option>)}
            </select>
            <button className="ghost" onClick={logout}>{t("sign_out")}</button>
          </>
        )}
      </header>
      <main className="main">{page}</main>
    </div>
    </LangContext.Provider>
  );
}
