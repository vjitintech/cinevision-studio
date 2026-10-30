import { useEffect, useState } from "react";
import { ErrorNote } from "../components/common";
import { navigate } from "../router";

export function BrowserPrivacyPage() {
  const [use, setUse] = useState<{ used: number; quota: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { import("../local/store").then((s) => s.usage()).then(setUse).catch(() => {}); }, []);
  const mb = (b: number) => `${(b / 1024 / 1024).toFixed(b > 1e9 ? 0 : 1)} MB`;
  const erase = async () => {
    if (!window.confirm("Delete every project, clip and plan stored by CineVision in this browser? This cannot be undone.")) return;
    setBusy(true);
    try { const l = await import("../local/server"); await l.eraseEverything(); navigate("/", true); location.reload(); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not delete."); setBusy(false); }
  };
  return (
    <div className="page" style={{ maxWidth: 820 }}>
      <h1>Security & privacy</h1>
      <p className="muted">You're using the browser edition. It's a set of static files served by GitHub Pages; everything else happens on your device.</p>
      <div className="stack" style={{ marginTop: 20 }}>
        <div className="fact"><div className="v">Your clips never leave this device</div>
          <div className="why">Video is decoded and analysed inside this browser tab. The page's security policy blocks it from sending data to any server, and it has no analytics or tracking.</div></div>
        <div className="fact"><div className="v">Stored only in this browser</div>
          <div className="why">Projects, clips, frames and plans are kept in this browser's local storage (IndexedDB) for this site. Another browser, device or private window won't see them, and clearing site data removes them.
            {use && <> Currently using about {mb(use.used)} of {mb(use.quota)} available.</>}</div></div>
        <div className="fact"><div className="v">Never used to train models</div><div className="why">There is no AI service involved in this edition at all.</div></div>
        <div className="fact estimated"><div className="v">Not encrypted at rest, no sign-in</div>
          <div className="why">Anyone who can use this browser profile can open these projects. For confidential footage on a shared computer, use the self-hosted edition, which adds accounts, two-factor sign-in and encryption.</div></div>
        <div className="fact estimated"><div className="v">Fewer analysis features than the self-hosted edition</div>
          <div className="why">People and faces aren't detected, and camera tilt, leading lines and lens perspective aren't measured. Optional local AI models aren't available here.</div></div>
      </div>
      <h2 style={{ marginTop: 32 }}>Delete everything</h2>
      <p className="muted">Removes every CineVision project, clip, frame, analysis, concept and plan from this browser.</p>
      <button className="danger" disabled={busy} onClick={erase}>Delete all CineVision data from this browser</button>
      <ErrorNote error={error} />
    </div>
  );
}
