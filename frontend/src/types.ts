export type Basis = "observed" | "estimated" | "heuristic";
export interface Tagged<T = any> { value: T; confidence: number; basis: Basis; note?: string }

export interface Job {
  id: string; kind: string; status: "queued" | "processing" | "completed" | "failed" | "cancelled";
  progress: number; stage: string | null; error: string | null; attempts: number; max_attempts: number;
  created_at: string; finished_at: string | null;
}

export interface Controls {
  genre?: string | null; scene_purpose?: string | null; desired_emotion?: string | null; visual_style?: string | null;
  treatment: "realistic" | "balanced" | "stylized"; movement_intensity: number; lighting_complexity: number;
  budget: "micro" | "low" | "medium" | "high"; available_equipment: string[]; aspect_ratio: string; frame_rate: number;
  target_duration_s?: number | null; concept_count: number; preservation: "low" | "medium" | "high";
  intent: "practical_shoot" | "animation" | "exploration"; sensor: "super35" | "full_frame" | "micro_four_thirds" | "phone";
  brief?: string | null;
}

export interface Project {
  id: string; title: string; brief: Controls; created_at: string; updated_at: string;
  video: null | { id: string; status: string; scan_status: string; filename: string; original_blob_id: string;
    proxy_blob_id: string | null; metadata: any };
  jobs: Job[]; analysis_id: string | null; concept_count: number;
}

export interface LightSource {
  role: string; fixture: string | null; azimuth_deg: number; elevation_deg: number; distance_m: number;
  intensity: number; cct_k: number | null; softness: number | null; motivation?: string; gel?: string;
}
export interface Lighting {
  setup: string; key_to_fill_ratio: string; ratio: number; sources: LightSource[]; haze: boolean; exposure: string;
  ambient: { level: string }; simulation: string; derived?: boolean;
}
export interface PathKey { t: number; pos: number[]; target: number[]; focal_mm: number }
export interface Shot {
  index: number; source_shot_index: number; start_s: number; duration_s: number; purpose: string; size: string; angle: string;
  dutch_roll_deg: number; composition: string; camera_height_m: number; camera_tilt_deg: number; viewing_direction: string;
  subject_target: string; azimuth_deg: number; distance_m: number;
  lens: { category: string; focal_mm: number; ff_equiv_mm: number; range_mm: number[]; sensor: string; perspective: string };
  dof: { aperture: string; strategy: string; focus: string };
  movement: { type: string; speed: string; speed_m_s: number; rig: string };
  blocking: string; lighting: Lighting; transition_out: string; notes: string;
  camera_path: { keyframes: PathKey[]; estimated: boolean };
}
export interface SceneSubject { id: string; label: string; x: number; z: number; height: number; facing_deg: number }
export interface Scene {
  subjects: SceneSubject[]; set: { type: string; time_of_day: string; practicals: { x: number; z: number; height: number; tint: string }[];
    background_z: number }; original_camera: { position: number[]; target: number[] }; note: string; estimated: boolean;
}
export interface ConceptData {
  archetype: string; name: string; objective: string; intended_emotion: string; rationale: string; aspect_ratio: string;
  frame_rate: number; sensor: string; budget: string;
  color: { palette: string[]; source_palette: string[]; grade: string; direction: string };
  editing: { rhythm: string; transitions: string }; shots: Shot[]; scene: Scene;
  alternative_endings: { name: string; movement: string; size: string; note: string }[];
  production: { complexity: number; complexity_label: string; camera_support: string[]; lenses: string[];
    lighting_equipment: string[]; crew_hint: string; setups: number };
  continuity_risks: string[]; differs_from_original: string[];
}
export interface Concept {
  id: string; lineage_id: string; parent_id: string | null; revision: number; is_current: boolean; is_selected: boolean;
  batch_id: string; created_at: string; engine: any; change_note: string | null; data: ConceptData;
}
export interface Frame { id: string; blob_id: string; idx: number; t_s: number; width: number; height: number;
  is_keyframe: number; shot_index: number | null; url: string }
