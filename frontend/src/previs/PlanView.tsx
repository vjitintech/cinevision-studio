import { useRef } from "react";
import type { Scene } from "../types";
import { rad } from "./math";

export interface PlanMarker {
  id: string; x: number; z: number; kind: "camera" | "light" | "subject" | "practical" | "point"; color?: string; label?: string;
  heading?: number; fovDeg?: number; draggable?: boolean; size?: number; dashed?: boolean;
}

/** Top-down floor plan (metres). +z points away from the original camera (up on screen). */
export function PlanView({ scene, markers, paths = [], onDrag, onDragEnd, height = 340, extent }: {
  scene: Scene; markers: PlanMarker[]; paths?: { pts: [number, number][]; color: string; dashed?: boolean }[];
  onDrag?: (id: string, x: number, z: number) => void; onDragEnd?: (id: string) => void; height?: number;
  extent?: { x0: number; x1: number; z0: number; z1: number };
}) {
  const svg = useRef<SVGSVGElement>(null);
  const dragging = useRef<string | null>(null);
  const xs = [...scene.subjects.map((s) => s.x), ...markers.map((m) => m.x)];
  const zs = [...scene.subjects.map((s) => s.z), ...markers.map((m) => m.z)];
  const e0 = extent || {
    x0: Math.min(-3, ...xs) - 1.5, x1: Math.max(3, ...xs) + 1.5,
    z0: Math.min(-1, ...zs) - 1.5, z1: Math.max(scene.set.background_z || 6, ...zs) + 1,
  };
  // keep a constant 600-unit-wide drawing so labels stay a consistent size: widen the x range to fit
  const W = 600;
  const scale = Math.min(W / (e0.x1 - e0.x0), height / (e0.z1 - e0.z0));
  const padX = (W / scale - (e0.x1 - e0.x0)) / 2;
  const e = { ...e0, x0: e0.x0 - padX, x1: e0.x1 + padX };
  const px = (x: number) => (x - e.x0) * scale;
  const pz = (z: number) => (e.z1 - z) * scale;
  const H = (e.z1 - e.z0) * scale;
  const toWorld = (cx: number, cy: number): [number, number] => {
    const r = svg.current!.getBoundingClientRect();
    const sx = ((cx - r.left) / r.width) * (e.x1 - e.x0) * scale, sy = ((cy - r.top) / r.height) * H;
    return [+(sx / scale + e.x0).toFixed(2), +(e.z1 - sy / scale).toFixed(2)];
  };
  const down = (id: string) => (ev: React.PointerEvent) => {
    if (!onDrag) return;
    dragging.current = id;
    (ev.target as Element).setPointerCapture(ev.pointerId);
  };
  const move = (ev: React.PointerEvent) => {
    if (!dragging.current || !onDrag) return;
    const [x, z] = toWorld(ev.clientX, ev.clientY);
    onDrag(dragging.current, x, z);
  };
  const up = () => { if (dragging.current) onDragEnd?.(dragging.current); dragging.current = null; };

  const grid = [];
  for (let x = Math.ceil(e.x0); x <= e.x1; x++) grid.push(<line key={`x${x}`} x1={px(x)} x2={px(x)} y1={0} y2={H} />);
  for (let z = Math.ceil(e.z0); z <= e.z1; z++) grid.push(<line key={`z${z}`} y1={pz(z)} y2={pz(z)} x1={0} x2={W} />);
  return (
    <svg ref={svg} className="plan" viewBox={`0 0 ${W} ${H}`} onPointerMove={move} onPointerUp={up} onPointerLeave={up}
      role="img" aria-label="Top-down plan of the set">
      <g className="grid">{grid}</g>
      <line x1={0} x2={(e.x1 - e.x0) * scale} y1={pz(scene.set.background_z || 6)} y2={pz(scene.set.background_z || 6)} stroke="#4a515c" strokeWidth={3} />
      <text x={6} y={pz(scene.set.background_z || 6) - 6}>background</text>
      <text x={6} y={H - 6}>1 m grid</text>
      {paths.map((p, i) => (
        <polyline key={i} points={p.pts.map(([x, z]) => `${px(x)},${pz(z)}`).join(" ")} fill="none" stroke={p.color} strokeWidth={2.5}
          strokeDasharray={p.dashed ? "5 5" : undefined} />
      ))}
      {markers.map((m) => {
        const cx = px(m.x), cy = pz(m.z);
        const size = m.size || 10;
        const heading = m.heading ?? 0;
        const dir = [Math.sin(rad(heading)), Math.cos(rad(heading))];
        const common = { onPointerDown: down(m.id), className: m.draggable ? "drag" : undefined };
        if (m.kind === "camera") {
          const half = rad((m.fovDeg || 40) / 2), L = 70;
          const a1 = rad(heading) - half, a2 = rad(heading) + half;
          return (
            <g key={m.id} {...common}>
              <path d={`M${cx},${cy} L${cx + Math.sin(a1) * L},${cy - Math.cos(a1) * L} L${cx + Math.sin(a2) * L},${cy - Math.cos(a2) * L} Z`}
                fill={m.color || "#86b4e0"} opacity={0.14} />
              <rect x={cx - 9} y={cy - 6} width={18} height={12} rx={2} fill={m.color || "#86b4e0"} transform={`rotate(${heading} ${cx} ${cy})`}
                strokeDasharray={m.dashed ? "3 2" : undefined} stroke={m.dashed ? "#e7e2d8" : "none"} />
              {m.label && <text x={cx + 12} y={cy + 16}>{m.label}</text>}
            </g>
          );
        }
        if (m.kind === "light") {
          return (
            <g key={m.id} {...common}>
              <line x1={cx} y1={cy} x2={cx + dir[0] * 22} y2={cy - dir[1] * 22} stroke={m.color} strokeWidth={2} />
              <circle cx={cx} cy={cy} r={size} fill={m.color} stroke="#15181d" strokeWidth={2} />
              {m.label && <text x={cx + size + 4} y={cy + 4}>{m.label}</text>}
            </g>
          );
        }
        if (m.kind === "subject") {
          return (
            <g key={m.id} {...common}>
              <ellipse cx={cx} cy={cy} rx={0.25 * scale} ry={0.16 * scale} fill="#c8b8a2" transform={`rotate(${heading} ${cx} ${cy})`} />
              <line x1={cx} y1={cy} x2={cx + dir[0] * 0.45 * scale} y2={cy - dir[1] * 0.45 * scale} stroke="#e7e2d8" strokeWidth={2} />
              {m.label && <text x={cx + 0.3 * scale} y={cy - 0.25 * scale}>{m.label}</text>}
            </g>
          );
        }
        return <circle key={m.id} cx={cx} cy={cy} r={size / 2} fill={m.color || "#e3a453"} {...common} />;
      })}
    </svg>
  );
}
