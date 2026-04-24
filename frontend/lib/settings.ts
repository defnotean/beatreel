"use client";

const MEDAL_KEY = "beatreel.medalKey";
const MEDAL_USER_KEY = "beatreel.medalUserId";
const GEMINI_KEYS = "beatreel.geminiKeys"; // NEW: newline-separated list
const GEMINI_KEY_LEGACY = "beatreel.geminiKey"; // legacy single-key storage

export interface MedalSettings {
  apiKey: string;
  userId: string;
}

export function loadMedalSettings(): MedalSettings {
  if (typeof window === "undefined") return { apiKey: "", userId: "" };
  return {
    apiKey: window.localStorage.getItem(MEDAL_KEY) ?? "",
    userId: window.localStorage.getItem(MEDAL_USER_KEY) ?? "",
  };
}

export function saveMedalSettings(s: MedalSettings) {
  if (typeof window === "undefined") return;
  if (s.apiKey) window.localStorage.setItem(MEDAL_KEY, s.apiKey);
  else window.localStorage.removeItem(MEDAL_KEY);
  if (s.userId) window.localStorage.setItem(MEDAL_USER_KEY, s.userId);
  else window.localStorage.removeItem(MEDAL_USER_KEY);
}

function parseKeys(raw: string | null): string[] {
  if (!raw) return [];
  const seen = new Set<string>();
  const out: string[] = [];
  for (const line of raw.replace(/\r/g, "\n").replace(/,/g, "\n").split("\n")) {
    // Strip anything outside printable ASCII. This both sanitizes smart-quotes
    // / bullets / zero-width chars that could sneak in from copy-paste AND
    // self-heals any keys that got corrupted by a previous masking bug.
    const cleaned = line.replace(/[^\x20-\x7E]/g, "").trim().replace(/^["']|["']$/g, "");
    if (cleaned && !seen.has(cleaned)) {
      seen.add(cleaned);
      out.push(cleaned);
    }
  }
  return out;
}

/** Returns all configured Gemini keys. Reads the new multi-key store AND
 * migrates a legacy single-key value forward. */
export function loadGeminiKeys(): string[] {
  if (typeof window === "undefined") return [];
  const multi = parseKeys(window.localStorage.getItem(GEMINI_KEYS));
  if (multi.length > 0) return multi;
  const legacy = (window.localStorage.getItem(GEMINI_KEY_LEGACY) || "").trim();
  if (legacy) {
    // Migrate forward on next save; for now just return it.
    return [legacy];
  }
  return [];
}

export function saveGeminiKeys(keys: string[]) {
  if (typeof window === "undefined") return;
  const cleaned = parseKeys(keys.join("\n"));
  if (cleaned.length > 0) {
    window.localStorage.setItem(GEMINI_KEYS, cleaned.join("\n"));
  } else {
    window.localStorage.removeItem(GEMINI_KEYS);
  }
  // Wipe legacy once migration has persisted.
  window.localStorage.removeItem(GEMINI_KEY_LEGACY);
}

/** @deprecated Use loadGeminiKeys. Kept for any residual callers; returns
 *  the first key or "". */
export function loadGeminiKey(): string {
  return loadGeminiKeys()[0] ?? "";
}

/** @deprecated Use saveGeminiKeys([key]). Kept for any residual callers. */
export function saveGeminiKey(key: string) {
  saveGeminiKeys(key ? [key] : []);
}
