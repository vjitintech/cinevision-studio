export type Basis = "observed" | "estimated" | "heuristic";
export function tag<T>(value: T, confidence: number, basis: Basis, note?: string) {
  const out: { value: T; confidence: number; basis: Basis; note?: string } = { value, confidence: Math.round(Math.max(0, Math.min(1, confidence)) * 100) / 100, basis };
  if (note) out.note = note;
  return out;
}
