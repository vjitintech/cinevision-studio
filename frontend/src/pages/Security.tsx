import { useEffect, useState } from "react";
import { api, ApiError } from "../api";
import type { Session } from "../App";
import { ErrorNote, Labeled } from "../components/common";

export function SecurityPage({ session, onChange }: { session: Session; onChange: () => void }) {
  const [privacy, setPrivacy] = useState<any>(null);
  const [adapters, setAdapters] = useState<any[]>([]);
  const [caps, setCaps] = useState<any>(null);
  const [audit, setAudit] = useState<any[]>([]);
  const [sessions, setSessions] = useState<any[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const load = async () => {
    const [p, c, a, s] = await Promise.all([api("GET", "/api/privacy"), api("GET", "/api/system/capabilities"), api("GET", "/api/audit?limit=50"), api("GET", "/api/sessions")]);
    setPrivacy(p); setCaps(c); setAdapters(c.adapters); setAudit(a.events); setSessions(s.sessions);
  };
  useEffect(() => { load().catch((e) => setError(e.message)); }, []);
  const savePrivacy = async (next: any) => {
    setError(null);
    try { setPrivacy(await api("PUT", "/api/privacy", next)); setMsg("Privacy settings saved."); load(); }
    catch (e) { setError(e instanceof ApiError ? e.message : "Save failed."); }
  };
  if (!privacy || !caps) return <div className="page"><ErrorNote error={error} />{!error && <div className="empty">Loading…</div>}</div>;
  const external = adapters.filter((a) => a.external);
  return (
    <div className="page">
      <h1>Security & privacy</h1>
      <p className="muted">This is a {caps.mode === "local" ? "local, single-machine" : "private-server"} deployment. Telemetry: none. External AI calls: off unless you consent below.</p>
      <ErrorNote error={error} />
      {msg && <p className="small" style={{ color: "var(--ok)" }}>{msg}</p>}

      <div className="grid2" style={{ marginTop: 20 }}>
        <section>
          <h2>Sign-in</h2>
          <MfaPanel session={session} onChange={onChange} />
          <PasswordPanel />
          <h3 style={{ marginTop: 20 }}>Active sessions</h3>
          {sessions.map((s, i) => (
            <div key={i} className="fact"><div className="v">{s.browser}{s.current ? " (this browser)" : ""}</div>
              <div className="why">Signed in {new Date(s.created_at).toLocaleString()} · last active {new Date(s.last_seen_at).toLocaleString()}</div></div>
          ))}
          <button onClick={async () => { await api("POST", "/api/auth/logout-all"); onChange(); }}>Sign out everywhere</button>
        </section>
        <section>
          <h2>Your footage</h2>
          <div className="fact"><div className="v">Never used to train models</div><div className="why">There is no training code path in this application.</div></div>
          <div className="fact"><div className="v">Encrypted at rest</div><div className="why">Each file has its own AES-256 key, wrapped by a master key kept outside the data folder. Deleting a project destroys its keys.</div></div>
          <div className="fact"><div className="v">Malware scanning: {caps.malware_scanning === "configured" ? "on (ClamAV)" : "not configured"}</div>
            {caps.malware_scanning !== "configured" && <div className="why">Uploads are still validated by container, codec, duration and resolution, and decoded in a resource-limited sandbox.</div>}</div>
          <h3 style={{ marginTop: 20 }}>Retention</h3>
          <Labeled label="Delete processing scratch files after (hours)">
            <input type="number" min={1} max={720} value={privacy.temp_retention_hours} onChange={(e) => setPrivacy({ ...privacy, temp_retention_hours: +e.target.value })} />
          </Labeled>
          <Labeled label="Automatically delete projects untouched for (days, 0 = never)">
            <input type="number" min={0} max={3650} value={privacy.project_retention_days} onChange={(e) => setPrivacy({ ...privacy, project_retention_days: +e.target.value })} />
          </Labeled>
          <button onClick={() => savePrivacy(privacy)}>Save retention</button>
        </section>
      </div>

      <h2 style={{ marginTop: 32 }}>AI services</h2>
      <p className="muted">Built-in engines run here. Optional services are listed with exactly what they would receive.</p>
      <div className="scroll-x">
        <table className="list">
          <thead><tr><th>Service</th><th>Engine</th><th>Status</th><th>What leaves this server</th><th>Consent</th></tr></thead>
          <tbody>{adapters.map((a) => (
            <tr key={a.id}>
              <td>{a.service_label}</td>
              <td>{a.name}<div className="faint small">{a.note}</div></td>
              <td><span className={`status ${a.status === "available" ? "ready" : a.status === "needs_consent" ? "busy" : ""}`}>{a.status.replace("_", " ")}</span></td>
              <td className="small">{a.data_flow}</td>
              <td>{a.external && a.status !== "not_configured" ? (
                <label className="row" style={{ color: "var(--text)" }}>
                  <input type="checkbox" checked={privacy.consented_providers.includes(a.id)} onChange={(e) => {
                    const set = new Set<string>(privacy.consented_providers);
                    if (e.target.checked) {
                      if (!window.confirm(`Allow ${a.name}? ${a.data_flow}`)) return;
                      set.add(a.id);
                    } else set.delete(a.id);
                    savePrivacy({ ...privacy, external_ai_consent: set.size > 0, consented_providers: [...set] });
                  }} /> Allow
                </label>
              ) : <span className="faint small">{a.external ? "—" : "not needed"}</span>}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>
      {external.every((a) => a.status === "not_configured") && <p className="small faint">No external services are configured on this deployment, so nothing can be sent anywhere.</p>}

      <h2 style={{ marginTop: 32 }}>Activity log</h2>
      <div className="scroll-x">
        <table className="list">
          <thead><tr><th>When</th><th>Event</th><th>Result</th><th>Details</th></tr></thead>
          <tbody>{audit.map((e, i) => (
            <tr key={i}><td className="tc small">{new Date(e.created_at).toLocaleString()}</td><td>{e.event.replace(/[._]/g, " ")}</td>
              <td>{e.outcome}</td><td className="small faint">{e.details ? Object.entries(e.details).map(([k, v]) => `${k}: ${v}`).join(", ") : ""}</td></tr>
          ))}</tbody>
        </table>
      </div>
    </div>
  );
}

function MfaPanel({ session, onChange }: { session: Session; onChange: () => void }) {
  const [password, setPassword] = useState("");
  const [setup, setSetup] = useState<{ secret: string; otpauth_uri: string } | null>(null);
  const [code, setCode] = useState("");
  const [codes, setCodes] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const enabled = session.user?.mfa_enabled;
  const run = async (fn: () => Promise<void>) => { setError(null); try { await fn(); } catch (e) { setError(e instanceof ApiError ? e.message : "Failed."); } };
  if (codes) return (
    <div className="notice warm">
      <strong>Two-factor is on.</strong> Store these recovery codes somewhere safe — each works once if you lose your phone. They won't be shown again.
      <pre className="kbd" style={{ display: "block", padding: 10, marginTop: 8, whiteSpace: "pre-wrap" }}>{codes.join("\n")}</pre>
      <button onClick={() => { setCodes(null); onChange(); }}>I've saved them</button>
    </div>
  );
  return (
    <div className="fact">
      <div className="v">Two-factor authentication: {enabled ? "on" : "off"}</div>
      {!enabled && !setup && (
        <div style={{ marginTop: 8 }}>
          <Labeled label="Confirm your password to start"><input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" /></Labeled>
          <button onClick={() => run(async () => setSetup(await api("POST", "/api/auth/mfa/setup", { password })))}>Set up authenticator app</button>
        </div>
      )}
      {!enabled && setup && (
        <div style={{ marginTop: 8 }}>
          <p className="small">Add this key to your authenticator app (Aegis, 1Password, Google Authenticator…), then enter the current code.</p>
          <p><span className="kbd" style={{ wordBreak: "break-all" }}>{setup.secret}</span></p>
          <Labeled label="6-digit code"><input inputMode="numeric" value={code} onChange={(e) => setCode(e.target.value)} /></Labeled>
          <button className="primary" onClick={() => run(async () => setCodes((await api("POST", "/api/auth/mfa/enable", { code })).recovery_codes))}>Turn on</button>
        </div>
      )}
      {enabled && (
        <details style={{ marginTop: 6 }}>
          <summary>Turn off two-factor</summary>
          <Labeled label="Password"><input type="password" value={password} onChange={(e) => setPassword(e.target.value)} /></Labeled>
          <Labeled label="Current code"><input inputMode="numeric" value={code} onChange={(e) => setCode(e.target.value)} /></Labeled>
          <button className="danger" onClick={() => run(async () => { await api("POST", "/api/auth/mfa/disable", { password, code }); onChange(); })}>Turn off</button>
        </details>
      )}
      <ErrorNote error={error} />
    </div>
  );
}

function PasswordPanel() {
  const [cur, setCur] = useState(""); const [next, setNext] = useState("");
  const [msg, setMsg] = useState<string | null>(null); const [error, setError] = useState<string | null>(null);
  return (
    <details className="fact">
      <summary>Change password</summary>
      <Labeled label="Current password"><input type="password" value={cur} onChange={(e) => setCur(e.target.value)} autoComplete="current-password" /></Labeled>
      <Labeled label="New password" hint="At least 12 characters. Other sessions will be signed out."><input type="password" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" /></Labeled>
      <button onClick={async () => {
        setError(null);
        try { await api("POST", "/api/auth/password", { current_password: cur, new_password: next }); setMsg("Password changed."); setCur(""); setNext(""); }
        catch (e) { setError(e instanceof ApiError ? e.message : "Failed."); }
      }}>Change password</button>
      {msg && <p className="small" style={{ color: "var(--ok)" }}>{msg}</p>}
      <ErrorNote error={error} />
    </details>
  );
}
