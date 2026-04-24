import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        bg: "#0b0b0c",
        surface: {
          1: "#131315",
          2: "#1a1a1c",
          3: "#222226",
        },
        border: {
          DEFAULT: "#2a2a2e",
          strong: "#3a3a40",
        },
        fg: {
          DEFAULT: "#eae8e3",
          dim: "#9a9aa0",
          muted: "#60606a",
        },
        accent: {
          DEFAULT: "#ff4655",
          dim: "#c2323d",
        },
        ok: "#7eb66e",
        warn: "#e0a848",
        err: "#e05858",
      },
      fontFamily: {
        sans: ["ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "Inter", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
      borderRadius: {
        DEFAULT: "4px",
        md: "4px",
        lg: "6px",
        xl: "6px",
      },
    },
  },
  plugins: [],
};

export default config;
