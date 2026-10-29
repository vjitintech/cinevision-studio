import type { Controls } from "../types";
import { Labeled } from "./common";

export const DEFAULT_CONTROLS: Controls = {
  genre: "", scene_purpose: "", desired_emotion: "", visual_style: "", treatment: "balanced", movement_intensity: 2,
  lighting_complexity: 2, budget: "low", available_equipment: [], aspect_ratio: "16:9", frame_rate: 24, target_duration_s: null,
  concept_count: 4, preservation: "medium", intent: "practical_shoot", sensor: "super35", brief: "",
};

const MOVE = ["Locked off", "Minimal", "Moderate", "Active", "Restless"];
const LIGHT = ["Available light only", "Simple", "Moderate", "Designed", "Elaborate"];

export function ControlsForm({ value, onChange, compact = false }: { value: Controls; onChange: (c: Controls) => void; compact?: boolean }) {
  const set = <K extends keyof Controls>(k: K, v: Controls[K]) => onChange({ ...value, [k]: v });
  return (
    <div>
      <Labeled label="Director's brief" hint="What should the audience feel? What is the scene about? Plain language is fine.">
        <textarea value={value.brief || ""} maxLength={4000} onChange={(e) => set("brief", e.target.value)}
          placeholder="A reunion that goes wrong. Start warm, end with the realisation that she has already decided to leave." />
      </Labeled>
      <div className={compact ? "" : "grid2"}>
        <Labeled label="Genre"><input value={value.genre || ""} maxLength={60} onChange={(e) => set("genre", e.target.value)} placeholder="Drama, thriller, comedy…" /></Labeled>
        <Labeled label="Desired emotion"><input value={value.desired_emotion || ""} maxLength={80} onChange={(e) => set("desired_emotion", e.target.value)} placeholder="Unease, tenderness, triumph…" /></Labeled>
      </div>
      <Labeled label="Scene purpose"><input value={value.scene_purpose || ""} maxLength={200} onChange={(e) => set("scene_purpose", e.target.value)} placeholder="Reveal that the brother lied" /></Labeled>
      <Labeled label={`Camera movement — ${MOVE[value.movement_intensity]}`}>
        <input type="range" min={0} max={4} value={value.movement_intensity} onChange={(e) => set("movement_intensity", +e.target.value)} />
      </Labeled>
      <Labeled label={`Lighting complexity — ${LIGHT[value.lighting_complexity]}`}>
        <input type="range" min={0} max={4} value={value.lighting_complexity} onChange={(e) => set("lighting_complexity", +e.target.value)} />
      </Labeled>
      <div className={compact ? "" : "grid2"}>
        <Labeled label="Budget">
          <select value={value.budget} onChange={(e) => set("budget", e.target.value as Controls["budget"])}>
            <option value="micro">Micro — friends and a car boot of kit</option><option value="low">Low — small crew, rented basics</option>
            <option value="medium">Medium — full camera and lighting package</option><option value="high">High — studio resources</option>
          </select>
        </Labeled>
        <Labeled label="Number of concepts">
          <select value={value.concept_count} onChange={(e) => set("concept_count", +e.target.value)}>
            {[2, 3, 4, 5, 6].map((n) => <option key={n} value={n}>{n}</option>)}
          </select>
        </Labeled>
      </div>
      <details>
        <summary>More controls: format, treatment, equipment</summary>
        <div className={compact ? "" : "grid2"} style={{ marginTop: 10 }}>
          <Labeled label="Treatment">
            <select value={value.treatment} onChange={(e) => set("treatment", e.target.value as Controls["treatment"])}>
              <option value="realistic">Realistic</option><option value="balanced">Balanced</option><option value="stylized">Stylised</option>
            </select>
          </Labeled>
          <Labeled label="How close to the original">
            <select value={value.preservation} onChange={(e) => set("preservation", e.target.value as Controls["preservation"])}>
              <option value="high">Stay close to the original</option><option value="medium">Reinterpret</option><option value="low">Reinvent freely</option>
            </select>
          </Labeled>
          <Labeled label="Aspect ratio">
            <select value={value.aspect_ratio} onChange={(e) => set("aspect_ratio", e.target.value)}>
              {["16:9", "2.39:1", "1.85:1", "4:3", "1:1", "9:16"].map((a) => <option key={a}>{a}</option>)}
            </select>
          </Labeled>
          <Labeled label="Frame rate">
            <select value={value.frame_rate} onChange={(e) => set("frame_rate", +e.target.value)}>
              {[23.976, 24, 25, 29.97, 30, 48, 50, 60, 120].map((f) => <option key={f} value={f}>{f} fps</option>)}
            </select>
          </Labeled>
          <Labeled label="Camera sensor">
            <select value={value.sensor} onChange={(e) => set("sensor", e.target.value as Controls["sensor"])}>
              <option value="super35">Super 35</option><option value="full_frame">Full frame</option>
              <option value="micro_four_thirds">Micro Four Thirds</option><option value="phone">Phone</option>
            </select>
          </Labeled>
          <Labeled label="Intended for">
            <select value={value.intent} onChange={(e) => set("intent", e.target.value as Controls["intent"])}>
              <option value="practical_shoot">A practical shoot</option><option value="animation">Animation</option><option value="exploration">Exploring ideas</option>
            </select>
          </Labeled>
          <Labeled label="Target scene length (seconds)" hint="Leave empty to keep the clip's timing.">
            <input type="number" min={1} max={600} value={value.target_duration_s ?? ""} onChange={(e) => set("target_duration_s", e.target.value ? +e.target.value : null)} />
          </Labeled>
          <Labeled label="Visual style references">
            <input value={value.visual_style || ""} maxLength={120} onChange={(e) => set("visual_style", e.target.value)} placeholder="Neon noir, pastel, documentary…" />
          </Labeled>
        </div>
        <Labeled label="Equipment you already have" hint="One item per line. Recommendations will mention it where it fits.">
          <textarea value={value.available_equipment.join("\n")} onChange={(e) => set("available_equipment", e.target.value.split("\n").map((x) => x.trim()).filter(Boolean).slice(0, 60))} />
        </Labeled>
      </details>
    </div>
  );
}
