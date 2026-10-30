import { useCallback, useEffect, useState } from "react";
import { api, setCsrf, setUnauthenticatedHandler } from "./api";
import { linkTo, navigate, useRoute } from "./router";
import { AuthPage } from "./pages/Auth";
import { Dashboard } from "./pages/Dashboard";
import { NewProject } from "./pages/NewProject";
import { Workspace } from "./pages/Workspace";
import { SecurityPage } from "./pages/Security";
import { BrowserPrivacyPage } from "./pages/BrowserPrivacy";

export interface Session {
  authenticated: boolean; mfa_required: boolean; registration_open: boolean; mode: string;
  user: { id: string; email: string; mfa_enabled: boolean } | null; csrf_token: string | null;
}

export function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [path, navCount] = useRoute();

  const refresh = useCallback(async () => {
    const s = await api<Session>("GET", "/api/auth/session");
    setCsrf(s.csrf_token);
    setSession(s);
  }, []);

  useEffect(() => { refresh().catch(() => setSession(null)); }, [refresh]);
  useEffect(() => { setUnauthenticatedHandler(() => { refresh(); }); }, [refresh]);

  if (!session) return <div className="empty">Loading…</div>;
  if (!session.authenticated) return <AuthPage session={session} onDone={refresh} />;

  const logout = async () => { await api("POST", "/api/auth/logout"); navigate("/", true); refresh(); };
  const m = path.match(/^\/p\/([0-9a-f-]{36})(?:\/([a-z0-9-]+))?/);
  let page: React.ReactNode;
  if (m) page = <Workspace pid={m[1]} section={m[2] || "analysis"} />;
  else if (path === "/new") page = <NewProject key={navCount} />;
  else if (path === "/security") page = session.mode === "browser" ? <BrowserPrivacyPage /> : <SecurityPage session={session} onChange={refresh} />;
  else page = <Dashboard />;

  return (
    <div className="shell">
      <header className="topbar">
        <a className="brand" {...linkTo("/")}><span className="brand-mark" aria-hidden />CineVision Studio</a>
        <nav className="topnav" aria-label="Main">
          <a {...linkTo("/")} aria-current={path === "/" ? "page" : undefined}>Private dashboard</a>
          <a {...linkTo("/new")} aria-current={path === "/new" ? "page" : undefined}>New project</a>
          <a {...linkTo("/security")} aria-current={path === "/security" ? "page" : undefined}>Security & privacy</a>
        </nav>
        <div className="grow" />
        {session.mode === "browser" ? (
          <span className="small muted" title="Everything runs inside this browser tab and is stored only on this device">Browser edition · stays on this device</span>
        ) : (
          <>
            <span className="small muted" title={session.mode === "local" ? "Everything runs on this machine" : "Private server"}>
              {session.mode === "local" ? "Local mode" : "Private server"} · {session.user?.email}
            </span>
            <button className="ghost" onClick={logout}>Sign out</button>
          </>
        )}
      </header>
      <main className="main">{page}</main>
    </div>
  );
}
