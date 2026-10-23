import { readonly, ref, watch } from "vue";
import { enUS } from "./locales/en-US";
import { zhCN, type MessageKey } from "./locales/zh-CN";

export type Locale = "zh-CN" | "en-US";
export type { MessageKey };

export const DEFAULT_LOCALE: Locale = "en-US";
export const LOCALE_STORAGE_KEY = "heads-up-lab.locale.v1";

const CATALOGS: Record<Locale, Record<MessageKey, string>> = {
  "zh-CN": zhCN,
  "en-US": enUS,
};

const localeRef = ref<Locale>(detectLocale());

export const locale = readonly(localeRef);

export function normalizeLocale(raw: unknown): Locale | null {
  const value = String(raw ?? "").trim().toLowerCase();
  if (!value) return null;
  if (value === "zh" || value === "zh-cn" || value === "zh_cn" || value.startsWith("zh-")) {
    return "zh-CN";
  }
  if (value === "en" || value === "en-us" || value === "en_us" || value.startsWith("en-")) {
    return "en-US";
  }
  return null;
}

function readStoredLocale(): Locale | null {
  try {
    return normalizeLocale(window.localStorage.getItem(LOCALE_STORAGE_KEY));
  } catch {
    return null;
  }
}

function readQueryLocale(): Locale | null {
  try {
    return normalizeLocale(new URLSearchParams(window.location.search).get("lang"));
  } catch {
    return null;
  }
}

/** Pick the initial locale: URL override, saved preference, then English. */
export function detectLocale(): Locale {
  return readQueryLocale() || readStoredLocale() || DEFAULT_LOCALE;
}

export function setLocale(next: Locale, persist = true): void {
  localeRef.value = next;
  if (!persist) return;
  try {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, next);
  } catch {
    // Storage can be unavailable in private mode; the session still switches.
  }
}

export function toggleLocale(): void {
  setLocale(localeRef.value === "zh-CN" ? "en-US" : "zh-CN");
}

export function t(
  key: MessageKey,
  params?: Record<string, string | number>,
): string {
  const catalog = CATALOGS[localeRef.value] || CATALOGS[DEFAULT_LOCALE];
  const template = catalog[key] ?? CATALOGS[DEFAULT_LOCALE][key] ?? key;
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (match, name: string) => {
    const value = params[name];
    return value === undefined || value === null ? match : String(value);
  });
}

/** Pick the field for the active interface language, falling back to the other. */
export function localizedText(zh: string | null | undefined, en: string | null | undefined): string {
  if (localeRef.value === "zh-CN") return String(zh || en || "");
  return String(en || zh || "");
}

export function languageName(target: Locale = localeRef.value): string {
  return target === "zh-CN" ? t("setup.agentLanguageZh") : t("setup.agentLanguageEn");
}

function applyDocumentLocale(): void {
  if (typeof document === "undefined") return;
  document.documentElement.lang = localeRef.value;
  document.title = t("app.title");
  const meta = document.querySelector<HTMLMetaElement>('meta[name="description"]');
  if (meta) meta.content = t("app.description");
}

watch(localeRef, applyDocumentLocale, { immediate: true });
