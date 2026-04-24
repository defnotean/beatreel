"use client";

import { useEffect, useState } from "react";
import { Eye, EyeOff, X } from "lucide-react";
import { cn } from "@/lib/cn";
import { loadMedalSettings, saveMedalSettings, type MedalSettings } from "@/lib/settings";

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

  useEffect(() => {
    if (open) {
      const s = loadMedalSettings();
      setApiKey(s.apiKey);
      setUserId(s.userId);
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
    const s = { apiKey: apiKey.trim(), userId: userId.trim() };
    saveMedalSettings(s);
    onSaved(s);
    onClose();
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-50 flex items-center justify-center p-6 bg-black/60 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="glass w-full max-w-md rounded-2xl p-6 shadow-glow-lg"
      >
        <div className="flex items-start justify-between mb-5">
          <div>
            <div className="text-[11px] uppercase tracking-[0.18em] text-accent-glow mb-1">
              settings
            </div>
            <h2 className="text-lg font-medium">Medal integration</h2>
          </div>
          <button
            onClick={onClose}
            className="rounded-lg p-1.5 text-white/50 hover:bg-white/5 hover:text-white transition-colors"
            aria-label="close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="space-y-4">
          <Field label="Medal API key" hint="Private key (starts with priv_). Stored locally in your browser — never sent anywhere except Medal.">
            <div className="relative">
              <input
                type={showKey ? "text" : "password"}
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder="priv_..."
                autoComplete="off"
                className={cn(
                  "w-full bg-black/40 border border-border rounded-lg px-3 py-2.5 pr-10",
                  "text-[13.5px] font-mono placeholder:text-white/20",
                  "focus:outline-none focus:border-accent/60 focus:ring-1 focus:ring-accent/30",
                )}
              />
              <button
                type="button"
                onClick={() => setShowKey((v) => !v)}
                className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1.5 text-white/40 hover:text-white/80"
                aria-label={showKey ? "hide key" : "show key"}
              >
                {showKey ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
              </button>
            </div>
          </Field>

          <Field label="Medal user ID (optional)" hint="Leave empty to show the latest clips your key has access to.">
            <input
              type="text"
              value={userId}
              onChange={(e) => setUserId(e.target.value)}
              placeholder="e.g. 12345"
              className={cn(
                "w-full bg-black/40 border border-border rounded-lg px-3 py-2.5",
                "text-[13.5px] font-mono placeholder:text-white/20",
                "focus:outline-none focus:border-accent/60 focus:ring-1 focus:ring-accent/30",
              )}
            />
          </Field>
        </div>

        <div className="mt-6 flex gap-3">
          <button
            onClick={onClose}
            className="flex-1 py-2.5 rounded-lg border border-border text-white/70 hover:bg-white/5 hover:text-white text-[13.5px] transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={save}
            className="flex-1 py-2.5 rounded-lg bg-accent-gradient text-white font-medium text-[13.5px] shadow-glow hover:shadow-glow-lg transition-all"
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
      <label className="text-[12.5px] font-medium text-white/80">{label}</label>
      {children}
      {hint && <p className="text-[11.5px] text-white/40 leading-relaxed">{hint}</p>}
    </div>
  );
}
