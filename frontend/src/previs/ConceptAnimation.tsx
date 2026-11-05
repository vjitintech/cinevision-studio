import { useEffect, useRef, useState } from "react";
import type { Concept } from "../types";
import { PrevisCanvas } from "./PrevisCanvas";
import { animatedFaces, blockingLabel } from "./animate";
import { aspect, camAt, lightingOpts } from "./shot";

/** A looping 3D animatic of one concept: camera moves along each shot's path and the people act out the blocking.
 *  Renders ~20 fps and pauses while scrolled out of view (software renderer, so keep it cheap). */
export function ConceptAnimation({ concept, width = "100%" }: { concept: Concept; width?: string }) {
  const shots = concept.data.shots;
  const total = shots.reduce((a, s) => a + s.duration_s, 0) || 1;
  const [time, setTime] = useState(0);
  const [visible, setVisible] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = box.current;
    if (!el || typeof IntersectionObserver === "undefined") { setVisible(true); return; }
    const io = new IntersectionObserver(([e]) => setVisible(e.isIntersecting), { threshold: 0.1 });
    io.observe(el);
    return () => io.disconnect();
  }, []);
  useEffect(() => {
    if (!visible) return;
    let last = performance.now(), raf = 0, acc = 0;
    const tick = (now: number) => {
      const dt = (now - last) / 1000; last = now; acc += dt;
      if (acc >= 0.05) { setTime((t) => (t + acc) % total); acc = 0; }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [visible, total]);
  let acc = 0, shot = shots[0], local = 0;
  for (const s of shots) { if (time < acc + s.duration_s) { shot = s; local = (time - acc) / s.duration_s; break; } acc += s.duration_s; }
  const scene = concept.data.scene;
  const cam = camAt(shot, local);
  const faces = animatedFaces(scene, shot, local, cam.pos);
  return (
    <div ref={box} className="concept-anim" style={{ width }}>
      <PrevisCanvas cam={cam} faces={faces} aspect={aspect(concept.data)} opts={{ sensor: concept.data.sensor, ...lightingOpts(scene, shot) }}
        label={`${concept.data.name}: animated 3D preview, shot ${shot.index + 1}`} />
      <div className="small"><b>{shot.index + 1}/{shots.length}</b> · {shot.size} · {shot.movement.type.split(" (")[0]}</div>
      <div className="small faint">{blockingLabel(shot)}</div>
      <div className="bar"><b style={{ width: `${(time / total) * 100}%` }} /></div>
    </div>
  );
}
