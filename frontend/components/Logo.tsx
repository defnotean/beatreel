export function Logo({ size = 28 }: { size?: number }) {
  return (
    <div className="flex items-center gap-3 select-none">
      <div
        className="relative flex items-center justify-center rounded-lg bg-accent-gradient shadow-glow"
        style={{ width: size, height: size }}
      >
        <svg
          width={size * 0.55}
          height={size * 0.55}
          viewBox="0 0 24 24"
          fill="none"
          stroke="white"
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M3 12h3l2-6 4 12 2-6 2 3h5" />
        </svg>
      </div>
      <div className="flex flex-col leading-none">
        <span className="font-semibold tracking-tight text-[17px]">beatreel</span>
        <span className="text-[10px] uppercase tracking-[0.18em] text-white/40">
          auto highlights
        </span>
      </div>
    </div>
  );
}
