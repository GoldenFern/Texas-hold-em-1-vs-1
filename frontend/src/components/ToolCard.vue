<script setup lang="ts">
import { computed, ref } from "vue";
import AppIcon from "./AppIcon.vue";
import { actionLabel, chips, compactPercent, duration } from "../format";
import type { ToolView } from "../types";

const props = defineProps<{ tool: ToolView }>();

const isEstimate = computed(() => props.tool.name === "estimate_hand_probabilities");
const isEquity = computed(() => props.tool.name === "estimate_equity");
const isAction = computed(() => props.tool.name === "apply_action");
const isRandom = computed(() => props.tool.name === "get_random_number");
const isCallEv = computed(() => props.tool.name === "call_ev");
const parsed = computed<Record<string, unknown>>(() => {
  if (props.tool.parsed) return props.tool.parsed;
  try {
    const value = JSON.parse(props.tool.argsText || "{}");
    return value && typeof value === "object" ? value : {};
  } catch {
    return {};
  }
});
const result = computed<Record<string, any>>(() => props.tool.result || {});
const ok = computed(() => props.tool.status === "done" && result.value.ok !== false);
const ignored = computed(() => Boolean(props.tool.ignored));
const randomValue = computed<number | null>(() => {
  const raw = result.value.value;
  const value = Number(raw);
  return raw !== undefined && raw !== null && Number.isFinite(value) ? value : null;
});
const randomPercent = computed(() => `${Math.min(100, Math.max(0, (randomValue.value ?? 0) * 100))}%`);
const distribution = computed<any[]>(() => {
  const value = result.value.distribution;
  return Array.isArray(value) ? value : [];
});
const currentBest = computed(() => result.value.current_best || null);
const method = computed(() => result.value.method || "");
const samples = computed(() => Number(result.value.total || result.value.samples || 0));
const elapsed = computed(() => Number(result.value.elapsed_ms || 0));
const bestKey = computed(() => currentBest.value?.category || "");
const equityRate = computed(() => Number(result.value.equity || 0));
const winRate = computed(() => Number(result.value.win || 0));
const tieRate = computed(() => Number(result.value.tie || 0));
const loseRate = computed(() => Number(result.value.lose || 0));
const stdError = computed(() => Number(result.value.std_error || 0));
const evCall = computed(() => Number(result.value.ev_call || 0));
const breakeven = computed(() => Number(result.value.breakeven_equity || 0));
const callAmount = computed(() => Number(result.value.call_amount || 0));
const effectivePot = computed(() => Number(result.value.effective_pot || 0));
const equitySource = computed(() => String(result.value.equity_source || "internal"));
const equityMethod = computed(() => String(result.value.equity_method || result.value.method || ""));
const streetLabel = computed(() => {
  const labels: Record<string, string> = {
    preflop: "翻前",
    flop: "翻牌",
    turn: "转牌",
    river: "河牌",
  };
  return labels[String(result.value.street || "")] || "";
});
const action = computed(() => String(parsed.value.action || result.value.action || ""));
const amount = computed(() => Number(parsed.value.amount || result.value.amount || 0));
const errorText = computed(() => String(result.value.error || props.tool.summary || ""));
const constraint = computed(() => {
  if (!isAction.value || !result.value || result.value.ok !== false) return null;
  if (result.value.legal_actions === undefined && result.value.to_call === undefined) return null;
  return result.value as Record<string, unknown>;
});
const legalActions = computed<string[]>(() => {
  const value = constraint.value?.legal_actions;
  return Array.isArray(value) ? (value as string[]) : [];
});
const statusLabel = computed(() => {
  if (ignored.value) return "已忽略";
  if (props.tool.status === "running") return "运行中";
  if (props.tool.status === "error") return "失败";
  return "完成";
});
const variant = computed(() => {
  if (isEstimate.value) return "estimate";
  if (isEquity.value) return "equity";
  if (isAction.value) return "action";
  if (isRandom.value) return "random";
  if (isCallEv.value) return "call-ev";
  return "generic";
});
const icon = computed(() =>
  isEstimate.value || isEquity.value
    ? "chart"
    : isAction.value
      ? "zap"
      : isRandom.value
        ? "dice"
        : isCallEv.value
          ? "coins"
          : "wrench",
);
const title = computed(() =>
  isEstimate.value
    ? "牌型概率"
    : isEquity.value
      ? "胜率估算"
      : isAction.value
        ? "执行动作"
        : isRandom.value
          ? "随机数"
          : isCallEv.value
            ? "跟注 EV"
            : props.tool.name || "工具",
);
const detail = computed(() => {
  if (isEstimate.value || isEquity.value) {
    if (method.value === "exact") return `精确枚举 · ${samples.value.toLocaleString()} 种`;
    if (method.value === "monte_carlo") return `蒙特卡洛 · ${samples.value.toLocaleString()} 次`;
    return "计算中…";
  }
  if (isAction.value) {
    const label = actionLabel(action.value) || "动作";
    return amount.value ? `${label} → ${chips(amount.value)}` : label;
  }
  if (isRandom.value) {
    return randomValue.value !== null ? randomValue.value.toFixed(4) : "0 ≤ x < 1";
  }
  if (isCallEv.value) {
    if (result.value.applicable === false) return "当前不适用";
    return `EV ${evCall.value > 0 ? "+" : ""}${evCall.value.toFixed(1)}`;
  }
  return props.tool.name || "";
});
const argsPretty = computed(() => {
  if (props.tool.parsed) return JSON.stringify(props.tool.parsed, null, 2);
  if (props.tool.argsText) {
    try {
      return JSON.stringify(JSON.parse(props.tool.argsText), null, 2);
    } catch {
      return props.tool.argsText;
    }
  }
  return "{}";
});
const resultPretty = computed(() => {
  try {
    return JSON.stringify(result.value, null, 2);
  } catch {
    return String(result.value);
  }
});
const copied = ref<string>("");

async function copy(text: string, key: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
    copied.value = key;
    window.setTimeout(() => {
      if (copied.value === key) copied.value = "";
    }, 1400);
  } catch {
    copied.value = "";
  }
}

function barWidth(item: any): string {
  const value = Number(item?.probability || 0);
  return `${Math.max(value > 0 ? 2 : 0, Math.min(100, value * 100))}%`;
}

function pctWidth(value: number): string {
  if (!Number.isFinite(value)) return "0%";
  return `${Math.max(0, Math.min(100, value * 100))}%`;
}

function percent(value: number): string {
  if (!Number.isFinite(value)) return "—";
  return `${(value * 100).toFixed(1)}%`;
}
</script>

<template>
  <details class="tool-card" :class="[variant, tool.status]" open>
    <summary class="tool-head">
      <span class="tool-icon"><AppIcon :name="icon" :size="13" /></span>
      <span class="tool-title">{{ title }}</span>
      <span class="tool-detail">{{ detail }}</span>
      <span class="tool-status" :class="tool.status">
        <span v-if="tool.status === 'running'" class="spinner"></span>
        <AppIcon v-else-if="tool.status === 'error'" name="alert" :size="12" />
        <AppIcon v-else-if="ignored" name="x" :size="12" />
        <AppIcon v-else name="check" :size="12" />
        <span>{{ statusLabel }}</span>
        <span v-if="tool.durationMs" class="mono duration">{{ duration(tool.durationMs) }}</span>
      </span>
      <AppIcon name="chevronDown" :size="12" class="tool-chevron" />
    </summary>

    <div class="tool-body">
      <!-- Estimate result -->
      <div v-if="isEstimate" class="estimate-block">
        <div v-if="tool.status === 'error'" class="generic-error">
          <AppIcon name="alert" :size="13" />
          {{ errorText || "牌型概率计算失败" }}
        </div>
        <div class="estimate-meta">
          <span class="chip gold">{{ method === "exact" ? "精确枚举" : method === "monte_carlo" ? "蒙特卡洛" : "计算中" }}</span>
          <span v-if="samples" class="mono">{{ samples.toLocaleString() }} {{ method === "exact" ? "种" : "次" }}</span>
          <span v-if="elapsed" class="mono faint">{{ elapsed.toFixed(1) }}ms</span>
          <span v-if="currentBest" class="best-inline">
            当前最佳 <strong>{{ currentBest.category_name }}</strong>
          </span>
          <span v-else class="faint">翻牌前 · 尚未成牌</span>
        </div>

        <div v-if="distribution.length" class="dist">
          <div
            v-for="item in distribution"
            :key="item.key"
            class="dist-row"
            :class="{ best: bestKey === item.key }"
          >
            <span class="dist-name">{{ item.short || item.name }}</span>
            <div class="dist-track">
              <div
                class="dist-fill"
                :style="{ width: barWidth(item), background: item.color }"
              ></div>
            </div>
            <span class="dist-pct mono">{{ compactPercent(item.probability) }}</span>
          </div>
        </div>
        <div v-else-if="tool.status === 'running'" class="dist-skeleton">
          <div v-for="n in 5" :key="n" class="skeleton-row">
            <span class="skeleton-name"></span>
            <span class="skeleton-bar" :style="{ width: `${70 - n * 8}%` }"></span>
          </div>
        </div>
        <div v-else class="empty-result">没有可展示的分布。</div>
      </div>

      <!-- Equity result -->
      <div v-else-if="isEquity" class="equity-block">
        <div v-if="tool.status === 'error'" class="generic-error">
          <AppIcon name="alert" :size="13" />
          {{ errorText || "胜率计算失败" }}
        </div>
        <template v-else>
          <div class="equity-readout">
            <div class="equity-main">
              <span class="equity-value mono">{{ percent(equityRate) }}</span>
              <span class="equity-caption">摊牌 equity</span>
            </div>
            <div class="equity-meta">
              <span class="chip gold">{{ method === "exact" ? "精确枚举" : method === "monte_carlo" ? "蒙特卡洛" : "计算中" }}</span>
              <span v-if="samples" class="mono faint">{{ samples.toLocaleString() }} {{ method === "exact" ? "种" : "次" }}</span>
              <span v-if="result.cached" class="chip">缓存</span>
            </div>
          </div>
          <div class="equity-bar">
            <span class="equity-seg win" :style="{ width: pctWidth(winRate) }"></span>
            <span class="equity-seg tie" :style="{ width: pctWidth(tieRate) }"></span>
            <span class="equity-seg lose" :style="{ width: pctWidth(loseRate) }"></span>
          </div>
          <div class="equity-legend">
            <span><i class="dot win"></i>胜 {{ percent(winRate) }}</span>
            <span><i class="dot tie"></i>平 {{ percent(tieRate) }}</span>
            <span><i class="dot lose"></i>负 {{ percent(loseRate) }}</span>
            <span class="faint">对手随机 · {{ streetLabel }}</span>
          </div>
          <div v-if="stdError > 0" class="equity-note">
            采样误差约 ±{{ percent(stdError * 1.96) }}（95%）
          </div>
        </template>
      </div>

      <!-- Call EV result -->
      <div v-else-if="isCallEv" class="callev-block">
        <div v-if="tool.status === 'error'" class="generic-error">
          <AppIcon name="alert" :size="13" />
          {{ errorText || "跟注 EV 计算失败" }}
        </div>
        <div v-else-if="result.applicable === false" class="callev-na">
          <AppIcon name="alert" :size="13" />
          <span>{{ result.reason || "当前不适用" }}</span>
        </div>
        <template v-else>
          <div class="callev-readout">
            <span class="callev-value mono" :class="{ positive: evCall > 0, negative: evCall < 0 }">
              {{ evCall > 0 ? "+" : "" }}{{ evCall.toFixed(1) }}
            </span>
            <span class="callev-caption">跟注 EV</span>
            <span class="chip" :class="evCall > 0 ? 'gold' : evCall < 0 ? 'red' : ''">
              {{ evCall > 0 ? "正期望" : evCall < 0 ? "负期望" : "盈亏平衡" }}
            </span>
            <span class="chip">{{ equitySource === "model" ? "模型传入" : "内部估算" }}</span>
            <span v-if="streetLabel" class="chip">{{ streetLabel }}</span>
          </div>
          <div class="callev-grid">
            <span>底池 <strong class="mono">{{ chips(Number(result.pot || 0)) }}</strong></span>
            <span>跟注 <strong class="mono">{{ chips(Number(callAmount)) }}</strong></span>
            <span>有效底池 <strong class="mono">{{ chips(Number(effectivePot)) }}</strong></span>
            <span>临界胜率 <strong class="mono">{{ percent(breakeven) }}</strong></span>
            <span>你的胜率 <strong class="mono">{{ percent(equityRate) }}</strong></span>
            <span>底池赔率 <strong class="mono">{{ result.pot_to_call_ratio ? `${Number(result.pot_to_call_ratio).toFixed(2)} : 1` : "—" }}</strong></span>
          </div>
          <div class="callev-compare">
            <div class="compare-track">
              <span class="compare-fill" :style="{ width: pctWidth(equityRate) }"></span>
              <span class="compare-marker" :style="{ left: pctWidth(breakeven) }"></span>
            </div>
            <div class="compare-scale mono">
              <span>0</span>
              <span>临界 {{ percent(breakeven) }}</span>
              <span>100%</span>
            </div>
          </div>
          <div v-if="equityMethod && equityMethod !== 'model'" class="callev-note mono">
            {{ equityMethod === "exact" ? "精确" : "采样" }} {{ Number(result.equity_samples || 0).toLocaleString() }}
          </div>
          <div class="callev-note">{{ result.note || "静态跟注 EV，假设后续不再下注。" }}</div>
        </template>
      </div>

      <!-- Action result -->
      <div v-else-if="isAction" class="action-block">
        <div v-if="tool.status === 'running'" class="running-line">
          <span class="spinner"></span>
          <span>正在提交动作…</span>
        </div>
        <div v-else-if="ok && result.ok !== false" class="action-ok">
          <span class="action-pill" :class="action">{{ actionLabel(action) || action }}</span>
          <span v-if="amount" class="mono amount">→ {{ chips(amount) }}</span>
          <span class="ok-text"><AppIcon name="check" :size="12" /> 动作已接受</span>
        </div>
        <div v-else class="action-fail">
          <div class="fail-line">
            <AppIcon name="alert" :size="13" />
            <span>{{ errorText || "动作被拒绝" }}</span>
          </div>
          <div v-if="constraint" class="constraint-grid">
            <span v-if="constraint.to_call !== undefined" class="chip">需跟注 <strong class="mono">{{ chips(Number(constraint.to_call)) }}</strong></span>
            <span v-if="constraint.min_raise_to !== undefined" class="chip">最小加注 <strong class="mono">{{ chips(Number(constraint.min_raise_to)) }}</strong></span>
            <span v-if="constraint.max_raise_to !== undefined" class="chip">最大 <strong class="mono">{{ chips(Number(constraint.max_raise_to)) }}</strong></span>
            <span v-if="constraint.pot !== undefined" class="chip">底池 <strong class="mono">{{ chips(Number(constraint.pot)) }}</strong></span>
          </div>
          <div v-if="legalActions.length" class="legal-row">
            <span class="faint">合法动作</span>
            <span v-for="item in legalActions" :key="item" class="chip">{{ actionLabel(item) }}</span>
          </div>
        </div>
      </div>

      <!-- Random number result -->
      <div v-else-if="isRandom" class="random-block">
        <div v-if="tool.status === 'error'" class="generic-error">
          <AppIcon name="alert" :size="13" />
          {{ errorText || "随机数生成失败" }}
        </div>
        <template v-else>
          <div class="random-readout">
            <span class="random-value mono">{{ randomValue !== null ? randomValue.toFixed(4) : "—" }}</span>
            <span class="random-unit">均匀随机 · 0 ≤ x &lt; 1</span>
          </div>
          <template v-if="randomValue !== null">
            <div class="random-track">
              <span class="random-marker" :style="{ left: randomPercent }"></span>
            </div>
            <div class="random-scale mono">
              <span>0</span>
              <span>0.5</span>
              <span>1</span>
            </div>
          </template>
        </template>
      </div>

      <!-- Generic result -->
      <div v-else-if="tool.status === 'error'" class="generic-error">
        <AppIcon name="alert" :size="13" />
        {{ errorText || "工具调用失败" }}
      </div>

      <div v-if="tool.status === 'running'" class="running-line">
        <span class="spinner"></span>
        <span>正在执行 {{ tool.name }}…</span>
      </div>

      <details class="tool-section">
        <summary>
          <span>参数</span>
          <button class="copy-btn" type="button" @click.stop.prevent="copy(argsPretty, 'args')">
            {{ copied === "args" ? "已复制" : "复制" }}
          </button>
        </summary>
        <pre class="tool-pre">{{ argsPretty }}</pre>
      </details>

      <details class="tool-section" v-if="tool.result">
        <summary>
          <span>原始结果</span>
          <button class="copy-btn" type="button" @click.stop.prevent="copy(resultPretty, 'result')">
            {{ copied === "result" ? "已复制" : "复制" }}
          </button>
        </summary>
        <pre class="tool-pre result-pre">{{ resultPretty }}</pre>
      </details>
    </div>
  </details>
</template>

<style scoped>
.tool-card {
  margin: 8px 0;
  border: 1px solid var(--line);
  border-left: 2px solid var(--line-strong);
  border-radius: 0 10px 10px 0;
  background: rgba(255, 255, 255, 0.022);
  overflow: hidden;
  transition: border-color 0.18s ease, background 0.18s ease;
}

.tool-card.estimate { border-left-color: rgba(232, 195, 122, 0.55); }
.tool-card.equity { border-left-color: rgba(232, 195, 122, 0.55); }
.tool-card.action { border-left-color: rgba(84, 214, 168, 0.5); }
.tool-card.random { border-left-color: rgba(121, 169, 255, 0.55); }
.tool-card.call-ev { border-left-color: rgba(121, 169, 255, 0.55); }
.tool-card.action.error { border-left-color: rgba(226, 112, 95, 0.6); }
.tool-card.running { border-left-color: rgba(121, 169, 255, 0.55); }
.tool-card:hover { background: rgba(255, 255, 255, 0.035); }

.tool-head {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 7px 10px;
  cursor: pointer;
  list-style: none;
  user-select: none;
}

.tool-head::-webkit-details-marker { display: none; }

.tool-icon {
  display: flex;
  color: var(--muted);
  flex: none;
}

.tool-card.estimate .tool-icon,
.tool-card.equity .tool-icon { color: var(--gold); }
.tool-card.action .tool-icon { color: var(--emerald); }
.tool-card.random .tool-icon,
.tool-card.call-ev .tool-icon { color: var(--blue); }
.tool-card.action.error .tool-icon { color: var(--red); }

.tool-title {
  font-size: calc(11px * var(--type-scale));
  font-weight: 750;
  color: var(--text-soft);
  white-space: nowrap;
}

.tool-detail {
  font-family: var(--font-mono);
  font-size: calc(9px * var(--type-scale));
  color: var(--faint);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}

.tool-status {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: calc(9px * var(--type-scale));
  color: var(--muted);
  white-space: nowrap;
  flex: none;
}

.tool-status.done { color: var(--emerald); }
.tool-status.error { color: var(--red); }
.tool-status.running { color: var(--blue); }

.duration { color: var(--faint); font-size: calc(8.5px * var(--type-scale)); }
.tool-chevron { color: var(--faint); flex: none; }

.tool-body {
  padding: 0 10px 9px;
  display: flex;
  flex-direction: column;
  gap: 7px;
}

.estimate-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  font-size: calc(9px * var(--type-scale));
  color: var(--faint);
  padding-top: 2px;
}

.estimate-meta .chip { padding: 2px 6px; font-size: calc(8.5px * var(--type-scale)); }
.best-inline { color: var(--muted); font-size: calc(9.5px * var(--type-scale)); }
.best-inline strong { color: var(--gold); }

.dist {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 6px 0 2px;
}

.dist-row {
  display: flex;
  align-items: center;
  gap: 7px;
  min-height: 14px;
}

.dist-name {
  width: 48px;
  flex: none;
  font-size: calc(9.5px * var(--type-scale));
  color: var(--muted);
  white-space: nowrap;
}

.dist-row.best .dist-name { color: var(--gold); font-weight: 700; }

.dist-track {
  flex: 1;
  height: 4px;
  border-radius: 99px;
  background: rgba(255, 255, 255, 0.055);
  overflow: hidden;
}

.dist-fill {
  height: 100%;
  border-radius: 99px;
  opacity: 0.82;
  transition: width 0.45s cubic-bezier(0.2, 0.8, 0.3, 1);
}

.dist-row.best .dist-fill { opacity: 1; }

.dist-pct {
  width: 42px;
  flex: none;
  text-align: right;
  font-size: calc(9px * var(--type-scale));
  color: var(--muted);
}

.dist-row.best .dist-pct { color: var(--gold); }

.dist-skeleton { display: flex; flex-direction: column; gap: 6px; padding: 6px 0; }

.skeleton-row { display: flex; align-items: center; gap: 8px; }
.skeleton-name { width: 40px; height: 6px; border-radius: 99px; background: rgba(255, 255, 255, 0.06); }
.skeleton-bar {
  height: 5px;
  border-radius: 99px;
  background: linear-gradient(90deg, rgba(255, 255, 255, 0.04), rgba(232, 195, 122, 0.14), rgba(255, 255, 255, 0.04));
  background-size: 200% 100%;
  animation: shimmer 1.4s linear infinite;
}

@keyframes shimmer { to { background-position: -200% 0; } }

.empty-result { font-size: calc(10px * var(--type-scale)); color: var(--faint); }

.random-block {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 4px 0 1px;
}

.random-readout {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.random-value {
  font-size: calc(20px * var(--type-scale));
  font-weight: 700;
  color: var(--text);
  letter-spacing: 0.02em;
  line-height: 1.1;
}

.random-unit {
  font-size: calc(9px * var(--type-scale));
  color: var(--faint);
}

.random-track {
  position: relative;
  height: 5px;
  margin: 2px 4px 0;
  border-radius: 99px;
  background: rgba(255, 255, 255, 0.06);
}

.random-marker {
  position: absolute;
  top: 50%;
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: var(--blue);
  box-shadow: 0 0 8px rgba(121, 169, 255, 0.6);
  transform: translate(-50%, -50%);
  transition: left 0.4s cubic-bezier(0.2, 0.8, 0.3, 1);
}

.random-scale {
  display: flex;
  justify-content: space-between;
  padding: 0 1px;
  font-size: calc(8px * var(--type-scale));
  color: var(--faint);
}

.equity-block,
.callev-block {
  display: flex;
  flex-direction: column;
  gap: 7px;
  padding: 4px 0 1px;
}

.equity-readout,
.callev-readout {
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
}

.equity-value {
  font-size: calc(22px * var(--type-scale));
  font-weight: 700;
  color: var(--text);
  letter-spacing: 0.01em;
  line-height: 1.05;
}

.equity-caption,
.callev-caption {
  font-size: calc(9px * var(--type-scale));
  color: var(--faint);
}

.equity-meta {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: calc(8.5px * var(--type-scale));
}

.equity-meta .chip {
  padding: 2px 6px;
  font-size: calc(8px * var(--type-scale));
}

.equity-bar {
  display: flex;
  height: 6px;
  border-radius: 99px;
  overflow: hidden;
  background: rgba(255, 255, 255, 0.055);
}

.equity-seg {
  height: 100%;
  transition: width 0.45s cubic-bezier(0.2, 0.8, 0.3, 1);
}

.equity-seg.win { background: var(--emerald); }
.equity-seg.tie { background: var(--gold); }
.equity-seg.lose { background: rgba(226, 112, 95, 0.75); }

.equity-legend {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 9px;
  font-size: calc(9px * var(--type-scale));
  color: var(--muted);
}

.equity-legend .dot {
  display: inline-block;
  width: 5px;
  height: 5px;
  border-radius: 50%;
  margin-right: 3px;
  vertical-align: middle;
}

.dot.win { background: var(--emerald); }
.dot.tie { background: var(--gold); }
.dot.lose { background: rgba(226, 112, 95, 0.75); }

.equity-note,
.callev-note {
  font-size: calc(8.5px * var(--type-scale));
  color: var(--faint);
  line-height: 1.45;
}

.callev-value {
  font-size: calc(22px * var(--type-scale));
  font-weight: 700;
  color: var(--text);
  letter-spacing: 0.01em;
  line-height: 1.05;
}

.callev-value.positive { color: var(--emerald); }
.callev-value.negative { color: var(--red); }

.callev-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 10px;
  font-size: calc(9px * var(--type-scale));
  color: var(--faint);
}

.callev-grid strong {
  color: var(--text-soft);
  font-weight: 600;
}

.callev-compare {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding-top: 1px;
}

.compare-track {
  position: relative;
  height: 5px;
  margin: 2px 4px 0;
  border-radius: 99px;
  background: rgba(255, 255, 255, 0.06);
}

.compare-fill {
  position: absolute;
  inset: 0 auto 0 0;
  border-radius: 99px;
  background: rgba(84, 214, 168, 0.55);
}

.compare-marker {
  position: absolute;
  top: 50%;
  width: 3px;
  height: 11px;
  border-radius: 2px;
  background: var(--gold);
  box-shadow: 0 0 6px rgba(232, 195, 122, 0.55);
  transform: translate(-50%, -50%);
}

.compare-scale {
  display: flex;
  justify-content: space-between;
  padding: 0 1px;
  font-size: calc(8px * var(--type-scale));
  color: var(--faint);
}

.callev-na {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  font-size: calc(10.5px * var(--type-scale));
  color: var(--gold);
  line-height: 1.5;
}

.action-ok {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 5px 0 1px;
}

.action-pill {
  display: inline-flex;
  align-items: center;
  padding: 3px 9px;
  border-radius: 6px;
  font-family: var(--font-mono);
  font-size: calc(10px * var(--type-scale));
  font-weight: 800;
  letter-spacing: 0.04em;
  background: rgba(84, 214, 168, 0.12);
  border: 1px solid rgba(84, 214, 168, 0.3);
  color: var(--emerald);
}

.action-pill.fold { color: var(--red); border-color: rgba(226, 112, 95, 0.3); background: var(--red-dim); }
.action-pill.raise, .action-pill.bet { color: var(--blue); border-color: rgba(121, 169, 255, 0.3); background: var(--blue-dim); }
.action-pill.all_in { color: var(--purple); border-color: rgba(196, 140, 255, 0.35); background: var(--purple-dim); }
.action-pill.check { color: var(--muted); border-color: var(--line); background: rgba(255, 255, 255, 0.04); }

.amount { font-size: calc(10px * var(--type-scale)); color: var(--text-soft); }

.ok-text {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: calc(9.5px * var(--type-scale));
  color: var(--emerald);
  margin-left: auto;
}

.action-fail {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 5px 0 1px;
}

.fail-line {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  font-size: calc(10.5px * var(--type-scale));
  color: #ffb3a8;
  line-height: 1.5;
}

.constraint-grid { display: flex; flex-wrap: wrap; gap: 4px; }
.constraint-grid .chip { font-size: calc(8.5px * var(--type-scale)); padding: 2px 6px; }
.constraint-grid strong { color: var(--text-soft); }

.legal-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
  font-size: calc(9px * var(--type-scale));
}

.legal-row .chip { font-size: calc(8.5px * var(--type-scale)); padding: 2px 6px; }

.generic-error {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: calc(10.5px * var(--type-scale));
  color: var(--red);
}

.running-line {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: calc(9.5px * var(--type-scale));
  color: var(--blue);
}

.tool-section {
  border-top: 1px solid var(--line);
  padding-top: 5px;
}

.tool-section summary {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: calc(9px * var(--type-scale));
  color: var(--faint);
  cursor: pointer;
  list-style: none;
}

.tool-section summary::-webkit-details-marker { display: none; }

.copy-btn {
  padding: 1px 6px;
  border-radius: 4px;
  border: 1px solid var(--line);
  color: var(--faint);
  font-size: calc(8.5px * var(--type-scale));
}

.copy-btn:hover { color: var(--text); border-color: var(--line-strong); }

.tool-pre {
  margin: 6px 0 0;
  padding: 7px 8px;
  max-height: 220px;
  overflow: auto;
  border-radius: 6px;
  border: 1px solid var(--line);
  background: rgba(3, 9, 7, 0.55);
  font-family: var(--font-mono);
  font-size: calc(9.5px * var(--type-scale));
  line-height: 1.55;
  color: #a9b8af;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.result-pre { max-height: 260px; color: #93a49a; }
</style>
