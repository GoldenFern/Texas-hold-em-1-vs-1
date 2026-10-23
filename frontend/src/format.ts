import { localizedText, t } from "./i18n";

export {
  actionLabel,
  handCategoryLabel,
  handCategoryShort,
  positionLabel,
  statusLabel,
  streetLabel,
  toolLabel,
} from "./i18n/labels";

export function chips(value: number | null | undefined): string {
  const number = Number(value ?? 0);
  return number.toLocaleString("en-US");
}

export function signedChips(value: number | null | undefined): string {
  const number = Number(value ?? 0);
  if (number > 0) return `+${number.toLocaleString("en-US")}`;
  return number.toLocaleString("en-US");
}

export function percent(value: number, digits = 1): string {
  return `${(value * 100).toFixed(digits)}%`;
}

export function compactPercent(value: number): string {
  const pct = value * 100;
  if (pct === 0) return "0%";
  if (pct < 0.1) return "<0.1%";
  if (pct < 10) return `${pct.toFixed(1)}%`;
  return `${Math.round(pct)}%`;
}

export function duration(ms: number | null | undefined): string {
  const value = Number(ms ?? 0);
  if (!value) return "";
  if (value < 1000) return `${Math.round(value)}ms`;
  if (value < 10000) return `${(value / 1000).toFixed(1)}s`;
  return `${Math.round(value / 1000)}s`;
}

export function secondsSince(start: number, now = Date.now()): string {
  const value = (now - start) / 1000;
  if (value < 10) return `${value.toFixed(1)}s`;
  return `${Math.round(value)}s`;
}

export function streetShort(street?: string): string {
  return (
    {
      preflop: "PRE",
      flop: "FLOP",
      turn: "TURN",
      river: "RIVER",
      showdown: "SHOW",
      finished: "END",
    }[street || ""] || String(street || "").toUpperCase()
  );
}

export function actionTone(type?: string): string {
  return (
    {
      fold: "fold",
      check: "check",
      call: "call",
      bet: "bet",
      raise: "raise",
      all_in: "allin",
    }[type || ""] || "neutral"
  );
}

export function shortName(name: string, max = 18): string {
  if (!name) return "";
  return name.length > max ? `${name.slice(0, max - 1)}…` : name;
}

/**
 * Build a default agent label without repeating the provider name.
 *
 * Some model names already start with the provider (for example
 * "DeepSeek V4.1 Flash"), so prefixing the provider again produces
 * noisy labels such as "DeepSeek · DeepSeek V4.1 Flash".
 */
export function agentNameFor(providerName: string, modelName: string): string {
  const provider = String(providerName || "").trim();
  const model = String(modelName || "").trim();
  if (!provider) return model;
  if (!model) return provider;
  if (model.toLowerCase().startsWith(provider.toLowerCase())) return model;
  return `${provider} · ${model}`;
}

/**
 * Short label for an agent seat. The provider icon already identifies the
 * vendor, so prefer the model part and strip a duplicated provider prefix.
 */
export function compactSeatName(name: string, modelName?: string): string {
  const full = String(name || "").trim();
  const model = String(modelName || "").trim();
  if (!model) return full;
  const provider = full.includes(" · ") ? full.split(" · ")[0].trim() : "";
  if (!provider || !model.toLowerCase().startsWith(provider.toLowerCase())) {
    return model;
  }
  const rest = model.slice(provider.length).replace(/^[\s·:_-]+/, "").trim();
  return rest || model;
}

/** Pick the provider name for the active interface language. */
export function providerDisplayName(name: string, nameEn?: string): string {
  return localizedText(name, nameEn);
}

/** Human seats always use the localized "You" label. */
export function humanSeatLabel(): string {
  return t("common.you");
}

/** Display name for a player seat, normalizing the human label. */
export function seatDisplayName(
  name: string | null | undefined,
  kind: string | null | undefined,
  modelName?: string,
): string {
  if (kind === "human") return humanSeatLabel();
  const value = String(name || "").trim();
  if (!value) return "";
  return kind === "llm" ? compactSeatName(value, modelName) : value;
}

export function tokenText(value: number | undefined): string {
  const number = Number(value ?? 0);
  if (!number) return "0";
  if (number < 1000) return String(number);
  return `${(number / 1000).toFixed(1)}k`;
}

export function scoreLabel(score: number | null | undefined): string {
  if (score === null || score === undefined) return "";
  return String(score);
}
