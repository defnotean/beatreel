"use client";

import { useEffect, useState } from "react";
import { Eye, EyeOff, X } from "lucide-react";
import { cn } from "@/lib/cn";
import {
  loadGeminiKeys,
  loadMedalSettings,
  saveGeminiKeys,
  saveMedalSettings,
  type MedalSettings,
} from "@/lib/settings";

export function SettingsModal({
  open,
  onClose,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  onSaved: (s: MedalSettings) => void;
}) {
  const [apiKey, setApiKey] = useState("");
  const [userId, setUserId] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [geminiKeysText, setGeminiKeysText] = useState("");
  const [showGemini, setShowGemini] = useState(false);

  useEffect(() => {
    if (open) {
      const s = loadMedalSettings();
      setApiKey(s.apiKey);
      setUserId(s.userId);
      setGeminiKeysText(loadGeminiKeys().join("\n"));
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onClose]);

  if (!open) return null;

  function save() {
    const medal = { apiKey: apiKey.trim(), userId: userId.trim() };
    saveMedalSettings(medal);
    const keys = geminiKeysText
      .split(/[\n,]/)
      .map((k) => k.trim().replace(/^["']|["']$/g, ""))
      .filter(Boolean);
    saveGeminiKeys(keys);
    onSaved(medal);
    onClose();
  }

  const keyCount = geminiKeysText
    .split(/[\n,]/)
    .map((k) => k.trim())
    .filter(Boolean).length;

  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-50 flex items-center justify-center p-6 bg-black/70"
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-md border border-border bg-surface-1"
      >
        <header className="flex items-center justify-between px-4 py-3 border-b border-border">
          <h2 className="font-mono text-[11px] uppercase tracking-[0.12em] text-fg-dim">
            Settings
          </h2>
          <button
            onClick={onClose}
            className="text-fg-muted hover:text-fg"
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="p-4 space-y-5">
          <section>
            <div className="flex items-baseline justify-between mb-2">
              <span className="font-mono text-[10.5px] uppercase tracking-[0.14em] text-fg-muted">
                Gemini · AI director
              </span>
              <span className="font-mono text-[10.5px] text-fg-dim tabular-nums">
                {keyCount} {keyCount === 1 ? "key" : "keys"}
              </span>
            </div>
            <Field
              label="API keys — one per line"
              hint="Each key runs one clip in parallel. More keys = faster analysis. Stored only in your browser."
            >
              <div className="relative">
                <textarea
                  value={geminiKeysText}
                  onChange={(e) => setGeminiKeysText(e.target.value)}
                  placeholder="AIza...&#10;AIza...&#10;AIza..."
                  autoComplete="off"
                  rows={4}
                  spellCheck={false}
                  style={{
                    // Visual-only masking — the raw value stays ASCII so we
                    // don't corrupt the Latin-1 HTTP header when sending.
                    WebkitTextSecurity: showGemini ? "none" : "disc",
                    textSecurity: showGemini ? "none" : "disc",
                  } as React.CSSProperties}
                  className={cn(
                    "w-full bg-bg border border-border px-3 py-2 pr-10",
                    "font-mono text-[12px] text-fg placeholder:text-fg-muted",
                    "focus:border-accent focus:outline-none",
                    "resize-none",
                  )}
                />
                <button
                  type="button"
                  onClick={() => setShowGemini((v) => !v)}
                  className="absolute right-2 top-2 p-1 text-fg-muted hover:text-fg"
                  aria-label={showGemini ? "Hide keys" : "Show keys"}
                >
                  {showGemini ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                </button>
              </div>
            </Field>
          </section>

          <section>
            <div className="font-mono text-[10.5px] uppercase tracking-[0.14em] text-fg-muted mb-2">
              Medal · Clip library
            </div>
            <Field
              label="API key"
              hint="Private key (priv_…). Stored only in your browser."
            >
              <div className="relative">
                <input
                  type={showKey ? "text" : "password"}
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder="priv_..."
                  autoComplete="off"
                  className={cn(
                    "w-full bg-bg border border-border px-3 py-2 pr-10",
                    "font-mono text-[12px] text-fg placeholder:text-fg-muted",
                    "focus:border-accent focus:outline-none",
                  )}
                />
                <button
                  type="button"
                  onClick={() => setShowKey((v) => !v)}
                  className="absolute right-2 top-1/2 -translate-y-1/2 p-1 text-fg-muted hover:text-fg"
                  aria-label={showKey ? "Hide key" : "Show key"}
                >
                  {showKey ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                </button>
              </div>
            </Field>

            <div className="h-3" />

            <Field
              label="User ID"
              hint="Optional. Leave empty to list all clips the key can access."
            >
              <input
                type="text"
                value={userId}
                onChange={(e) => setUserId(e.target.value)}
                placeholder="12345"
                className={cn(
                  "w-full bg-bg border border-border px-3 py-2",
                  "font-mono text-[12px] text-fg placeholder:text-fg-muted",
                  "focus:border-accent focus:outline-none",
                )}
              />
            </Field>
          </section>
        </div>

        <div className="grid grid-cols-2 border-t border-border">
          <button
            onClick={onClose}
            className="py-3 border-r border-border font-mono text-[12px] uppercase tracking-[0.1em] text-fg-dim hover:text-fg hover:bg-surface-2"
          >
            Cancel
          </button>
          <button
            onClick={save}
            className="py-3 bg-accent text-white font-mono text-[12px] uppercase tracking-[0.1em] hover:brightness-110"
          >
            Save
          </button>
        </div>
      </div>
    </div>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <label className="font-mono text-[11px] uppercase tracking-[0.12em] text-fg-muted">
        {label}
      </label>
      {children}
      {hint && <p className="text-[11px] text-fg-muted leading-relaxed">{hint}</p>}
    </div>
  );
}
