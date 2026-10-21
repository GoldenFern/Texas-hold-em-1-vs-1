<script setup lang="ts">
import { computed, ref } from "vue";
import AppIcon from "./AppIcon.vue";
import { actionLabel, chips, compactPercent, duration } from "../format";
import type { ToolView } from "../types";

const props = defineProps<{ tool: ToolView }>();

const isEstimate = computed(() => props.tool.name === "estimate_hand_probabilities");
const isAction = computed(() => props.tool.name === "apply_action");
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
const distribution = computed<any[]>(() => {
  const value = result.value.distribution;
  return Array.isArray(value) ? value : [];
});
const currentBest = computed(() => result.value.current_best || null);
const method = computed(() => result.value.method || "");
const samples = computed(() => Number(result.value.total || result.value.samples || 0));
const elapsed = computed(() => Number(result.value.elapsed_ms || 0));
const bestKey = computed(() => currentBest.value?.category || "");
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
const icon = computed(() => (isEstimate.value ? "chart" : isAction.value ? "zap" : "wrench"));
const title = computed(() => (isEstimate.value ? "牌型概率" : isAction.value ? "执行动作" : props.tool.name || "工具"));
const detail = computed(() => {
  if (isEstimate.value) {
    if (method.value === "exact") return `精确枚举 · ${samples.value.toLocaleString()} 种`;
    if (method.value === "monte_carlo") return `蒙特卡洛 · ${samples.value.toLocaleString()} 次`;
    return "计算中…";
  }
  if (isAction.value) {
    const label = actionLabel(action.value) || "动作";
    return amount.value ? `${label} → ${chips(amount.value)}` : label;
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
</script>

<template>
  <details class="tool-card" :class="[isEstimate ? 'estimate' : isAction ? 'action' : 'generic', tool.status]" open>
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
.tool-card.action { border-left-color: rgba(84, 214, 168, 0.5); }
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

.tool-card.estimate .tool-icon { color: var(--gold); }
.tool-card.action .tool-icon { color: var(--emerald); }
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
