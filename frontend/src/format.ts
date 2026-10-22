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

export function streetLabel(street?: string): string {
  return (
    {
      waiting: "等待",
      preflop: "翻牌前",
      flop: "翻牌",
      turn: "转牌",
      river: "河牌",
      showdown: "摊牌",
      finished: "本手结束",
    }[street || ""] || street || ""
  );
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

export function actionLabel(type?: string): string {
  return (
    {
      fold: "弃牌",
      check: "过牌",
      call: "跟注",
      bet: "下注",
      raise: "加注",
      all_in: "全下",
    }[type || ""] || type || ""
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

export function positionLabel(position?: string): string {
  if (position === "button") return "按钮 / 小盲";
  if (position === "big_blind") return "大盲";
  return position || "";
}

export function statusLabel(status?: string): string {
  return (
    {
      active: "行动中",
      folded: "已弃牌",
      all_in: "全下",
      out: "出局",
    }[status || ""] || status || ""
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
