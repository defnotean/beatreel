import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        bg: {
          DEFAULT: "#0a0a0f",
          elevated: "#12121a",
          panel: "#171721",
        },
        border: {
          DEFAULT: "#23232f",
          hover: "#2e2e3e",
        },
        accent: {
          DEFAULT: "#a855f7",
          glow: "#c084fc",
          secondary: "#06b6d4",
        },
      },
      fontFamily: {
        sans: ["ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "sans-serif"],
        display: ["ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      keyframes: {
        pulseGlow: {
          "0%, 100%": { opacity: "1", filter: "brightness(1)" },
          "50%": { opacity: "0.7", filter: "brightness(1.4)" },
        },
        float: {
          "0%, 100%": { transform: "translateY(0)" },
          "50%": { transform: "translateY(-8px)" },
        },
        shimmer: {
          "0%": { backgroundPosition: "-200% 0" },
          "100%": { backgroundPosition: "200% 0" },
        },
        beatPulse: {
          "0%, 100%": { transform: "scaleY(0.4)" },
          "50%": { transform: "scaleY(1)" },
        },
      },
      animation: {
        "pulse-glow": "pulseGlow 2s ease-in-out infinite",
        float: "float 6s ease-in-out infinite",
        shimmer: "shimmer 2.5s linear infinite",
        "beat-pulse": "beatPulse 0.6s ease-in-out infinite",
      },
      backgroundImage: {
        "grid-fade":
          "radial-gradient(circle at center, rgba(168,85,247,0.12) 0%, transparent 60%), linear-gradient(rgba(255,255,255,0.03) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.03) 1px, transparent 1px)",
        "accent-gradient":
          "linear-gradient(135deg, #a855f7 0%, #ec4899 50%, #06b6d4 100%)",
      },
      boxShadow: {
        glow: "0 0 40px -5px rgba(168, 85, 247, 0.4)",
        "glow-lg": "0 0 80px -10px rgba(168, 85, 247, 0.5)",
      },
    },
  },
  plugins: [],
};

export default config;
