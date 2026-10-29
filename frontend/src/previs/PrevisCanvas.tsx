import { useEffect, useRef } from "react";
import { render, RenderOpts, Face } from "./render";
import type { CamState } from "./math";

/** Renders a view from `cam`. Pixel size follows the element's CSS width × devicePixelRatio. */
export function PrevisCanvas({ cam, faces, opts, aspect = 16 / 9, className, label, onPointer }: {
  cam: CamState; faces: Face[]; opts: RenderOpts; aspect?: number; className?: string; label?: string;
  onPointer?: (e: React.PointerEvent<HTMLCanvasElement>) => void;
}) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const c = ref.current;
    if (!c) return;
    const draw = () => {
      const w = Math.max(160, Math.round(c.clientWidth * Math.min(2, window.devicePixelRatio || 1)));
      const h = Math.round(w / aspect);
      if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
      const ctx = c.getContext("2d");
      if (ctx) render(ctx, cam, faces, opts);
    };
    draw();
    const ro = new ResizeObserver(draw);
    ro.observe(c);
    return () => ro.disconnect();
  }, [cam, faces, opts, aspect]);
  return <canvas ref={ref} className={className} role="img" aria-label={label || "previsualisation frame"}
    style={{ aspectRatio: String(aspect) }} onPointerDown={onPointer} onPointerMove={onPointer} />;
}
