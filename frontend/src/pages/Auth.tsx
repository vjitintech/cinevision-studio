import { useMemo, useState } from "react";
import { api, ApiError } from "../api";
import type { Session } from "../App";
import { ErrorNote, Labeled } from "../components/common";
import { PrevisCanvas } from "../previs/PrevisCanvas";
import { buildScene, worldLights } from "../previs/render";
import type { Scene } from "../types";

const DEMO: Scene = {
  subjects: [{ id: "A", label: "A", x: -0.55, z: 3.2, height: 1.72, facing_deg: 120 }, { id: "B", label: "B", x: 0.6, z: 3.5, height: 1.62, facing_deg: 245 }],
  set: { type: "indoor", time_of_day: "night", practicals: [{ x: 1.8, z: 5.2, height: 1.3, tint: "warm" }], background_z: 7 },
  original_camera: { position: [0, 1.5, 0], target: [0, 1.4, 3] }, note: "", estimated: true,
};

export function AuthPage({ session, onDone }: { session: Session; onDone: () => void }) {
  const [mode, setMode] = useState<"login" | "register">(session.registration_open ? "register" : "login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const faces = useMemo(() => buildScene(DEMO), []);
  const lights = useMemo(() => [
    ...worldLights([{ role: "key", fixture: null, azimuth_deg: -70, elevation_deg: 30, distance_m: 2.4, intensity: 1.1, cct_k: 3000, softness: 0.4 },
      { role: "rim", fixture: null, azimuth_deg: 150, elevation_deg: 35, distance_m: 3, intensity: 0.8, cct_k: 7500, softness: 0.3 }],
      DEMO.subjects[0], true)], []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true); setError(null);
    try {
      if (session.mfa_required) await api("POST", "/api/auth/mfa/verify", { code });
      else await api("POST", mode === "login" ? "/api/auth/login" : "/api/auth/register", { email, password });
      onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong.");
    } finally { setBusy(false); }
  };

  return (
    <div className="auth">
      <div className="auth-art">
        <PrevisCanvas cam={{ pos: [-1.6, 1.35, 0.6], target: [-0.1, 1.45, 3.3], focal: 22, roll: 0 }} faces={faces}
          opts={{ sensor: "super35", lights, ambient: 0.08, haze: true, practicals: [{ pos: [1.8, 1.3, 5.2], color: [1, 0.66, 0.35] }] }}
          aspect={16 / 9} label="Example previsualisation: two figures lit by a warm key and cool rim" />
        <div className="caption">
          <h1>CineVision Studio</h1>
          <p>Private previsualisation. Upload a clip and explore other ways to shoot it — camera, lens, light and movement — before you're on set.</p>
          <p className="small">Your footage stays on this {session.mode === "local" ? "machine" : "server"} and is never used to train models.</p>
        </div>
      </div>
      <form className="auth-form" onSubmit={submit}>
        {session.mfa_required ? (
          <>
            <h2>Two-factor code</h2>
            <p className="muted">Enter the 6-digit code from your authenticator app, or one of your recovery codes.</p>
            <Labeled label="Code"><input autoFocus inputMode="numeric" autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value)} /></Labeled>
          </>
        ) : (
          <>
            <h2>{mode === "login" ? "Sign in" : session.registration_open && mode === "register" ? "Create the owner account" : "Sign in"}</h2>
            {mode === "register" && <p className="muted small">The first account on a private instance becomes its owner. Registration then closes unless the operator reopens it.</p>}
            <Labeled label="Email"><input type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} /></Labeled>
            <Labeled label="Password" hint={mode === "register" ? "At least 12 characters. A passphrase works well." : undefined}>
              <input type="password" autoComplete={mode === "login" ? "current-password" : "new-password"} required value={password} onChange={(e) => setPassword(e.target.value)} />
            </Labeled>
          </>
        )}
        <ErrorNote error={error} />
        <div className="row" style={{ marginTop: 12 }}>
          <button className="primary" type="submit" disabled={busy}>{session.mfa_required ? "Verify" : mode === "login" ? "Sign in" : "Create account"}</button>
          {!session.mfa_required && session.registration_open && (
            <button type="button" className="ghost" onClick={() => setMode(mode === "login" ? "register" : "login")}>
              {mode === "login" ? "Create an account instead" : "I already have an account"}
            </button>
          )}
        </div>
      </form>
    </div>
  );
}
