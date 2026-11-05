// Interface language. The review's findings are translated on the server (same language list);
// this file translates the app's own labels. Missing keys fall back to English.
import { createContext, useContext } from "react";
import { UI } from "./i18n-strings";

export type Lang = keyof typeof UI | string;

export const LANGS: { code: string; name: string; rtl?: boolean }[] = [
  { code: "en", name: "English" }, { code: "hi", name: "हिन्दी" }, { code: "te", name: "తెలుగు" }, { code: "ta", name: "தமிழ்" },
  { code: "kn", name: "ಕನ್ನಡ" }, { code: "ml", name: "മലയാളം" }, { code: "bn", name: "বাংলা" }, { code: "mr", name: "मराठी" },
  { code: "es", name: "Español" }, { code: "fr", name: "Français" }, { code: "de", name: "Deutsch" }, { code: "pt", name: "Português" },
  { code: "ar", name: "العربية", rtl: true }, { code: "ja", name: "日本語" }, { code: "ko", name: "한국어" }, { code: "zh", name: "中文" },
];

export interface LangCtx { lang: string; setLang: (l: string) => void; t: (k: keyof typeof UI.en, vars?: Record<string, string | number>) => string }

export function makeT(lang: string) {
  const dict: Record<string, string> = { ...UI.en, ...((UI as any)[lang] || {}) };
  return (k: keyof typeof UI.en, vars?: Record<string, string | number>) => {
    let s = dict[k] ?? (UI.en as any)[k] ?? String(k);
    if (vars) for (const [n, v] of Object.entries(vars)) s = s.split(`{${n}}`).join(String(v));
    return s;
  };
}

export const LangContext = createContext<LangCtx>({ lang: "en", setLang: () => {}, t: makeT("en") });
export const useT = () => useContext(LangContext);
