"use client";

import { useEffect, useRef } from "react";

export function BeatVisualizer({
  active,
  bars = 48,
  intensity = 1,
}: {
  active: boolean;
  bars?: number;
  intensity?: number;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const seedsRef = useRef<number[]>([]);
  const frameRef = useRef<number>(0);

  useEffect(() => {
    seedsRef.current = Array.from({ length: bars }, () => Math.random());
  }, [bars]);

  useEffect(() => {
    if (!active) return;
    const el = containerRef.current;
    if (!el) return;

    let start = performance.now();

    const tick = (now: number) => {
      const t = (now - start) / 1000;
      const children = el.children;
      for (let i = 0; i < children.length; i++) {
        const child = children[i] as HTMLElement;
        const seed = seedsRef.current[i] ?? 0.5;
        const phase = seed * Math.PI * 2;
        const wave = Math.sin(t * 4 + phase + i * 0.15);
        const envelope = 0.5 + 0.5 * Math.sin(t * 1.2 + seed * 3);
        const h = 18 + (wave * wave) * 60 * intensity * envelope;
        child.style.height = `${h}%`;
      }
      frameRef.current = requestAnimationFrame(tick);
    };
    frameRef.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frameRef.current);
  }, [active, intensity]);

  return (
    <div
      ref={containerRef}
      className="flex items-end justify-center gap-[3px] h-24 w-full px-4"
      aria-hidden
    >
      {Array.from({ length: bars }).map((_, i) => (
        <div
          key={i}
          className="w-1 rounded-full transition-[background] duration-500"
          style={{
            height: "20%",
            background:
              "linear-gradient(to top, #a855f7 0%, #ec4899 60%, #22d3ee 100%)",
            opacity: active ? 0.85 : 0.25,
          }}
        />
      ))}
    </div>
  );
}
