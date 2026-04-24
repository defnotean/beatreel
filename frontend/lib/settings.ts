"use client";

const KEY = "beatreel.medalKey";
const USER_KEY = "beatreel.medalUserId";

export interface MedalSettings {
  apiKey: string;
  userId: string;
}

export function loadMedalSettings(): MedalSettings {
  if (typeof window === "undefined") return { apiKey: "", userId: "" };
  return {
    apiKey: window.localStorage.getItem(KEY) ?? "",
    userId: window.localStorage.getItem(USER_KEY) ?? "",
  };
}

export function saveMedalSettings(s: MedalSettings) {
  if (typeof window === "undefined") return;
  if (s.apiKey) window.localStorage.setItem(KEY, s.apiKey);
  else window.localStorage.removeItem(KEY);
  if (s.userId) window.localStorage.setItem(USER_KEY, s.userId);
  else window.localStorage.removeItem(USER_KEY);
}
