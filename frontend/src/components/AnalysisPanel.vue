<script setup lang="ts">
import { computed } from "vue";
import AppIcon from "./AppIcon.vue";
import PlayingCard from "./PlayingCard.vue";
import { compactPercent } from "../format";
import {
  game,
  humanSeat,
  refreshAnalysis,
  selectAnalysisSeat,
} from "../store";

const snapshot = computed(() => game.snapshot);
const match = computed(() => snapshot.value?.match || null);
const seats = computed(() => match.value?.seats || []);
const analysis = computed(() => {
  const seat = game.selectedAnalysisSeat;
  return seat === null ? null : game.analysis[seat] || null;
});
const selectedSeat = computed(() => game.selectedAnalysisSeat);
const selectedInfo = computed(() => seats.value.find((seat) => seat.seat === selectedSeat.value) || null);
const selectedPlayer = computed(() => snapshot.value?.players?.find((player) => player.seat === selectedSeat.value) || null);
const visibleSeats = computed(() => {
  if (game.godView) return seats.value;
  const human = humanSeat.value;
  return human ? [human] : [];
});
const canAnalyze = computed(() => Boolean(selectedPlayer.value?.hole_cards?.length === 2 && snapshot.value?.has_match));
const distribution = computed(() => analysis.value?.distribution || []);
const bestKey = computed(() => analysis.value?.current_best?.category || "");
const methodText = computed(() => {
  if (!analysis.value) return "";
  if (analysis.value.method === "exact") return `精确枚举 · ${analysis.value.total.toLocaleString("en-US")} 种`;
  return `蒙特卡洛 · ${analysis.value.total.toLocaleString("en-US")} 次`;
});
const stale = computed(() => {
  if (!analysis.value || !snapshot.value) return false;
  return analysis.value.hand_id !== snapshot.value.hand_id || analysis.value.street !== snapshot.value.street;
});
const maxProbability = computed(() => {
  const values = distribution.value.map((item) => item.probability);
  return Math.max(0.0001, ...values);
});
const holeCards = computed(() => selectedPlayer.value?.hole_cards || []);
const boardCards = computed(() => snapshot.value?.community || []);
const summaryName = computed(() => selectedPlayer.value?.name || selectedInfo.value?.name || "—");

function barWidth(value: number): string {
  if (value <= 0) return "0%";
  // Scale relative to the largest category so small probabilities remain visible.
  const relative = value / maxProbability.value;
  return `${Math.max(2, relative * 100)}%`;
}

function pickSeat(seat: number): void {
  selectAnalysisSeat(seat);
}

function reload(): void {
  void refreshAnalysis(true);
}
</script>

<template>
  <section class="analysis-panel surface">
    <header class="analysis-head">
      <div class="title-block">
        <span class="eyebrow">HAND DISTRIBUTION</span>
        <h2>最终牌型分布</h2>
        <p>在已知底牌与公共牌下，你最终最佳五张牌成为各牌型的概率。</p>
      </div>
      <div class="head-controls">
        <div v-if="visibleSeats.length > 1" class="segmented seat-switch">
          <button
            v-for="seat in visibleSeats"
            :key="seat.seat"
            :class="{ active: selectedSeat === seat.seat }"
            @click="pickSeat(seat.seat)"
          >
            {{ seat.name }}
          </button>
        </div>
        <button class="btn btn-ghost btn-sm" :disabled="game.analysisLoading || !canAnalyze" @click="reload">
          <AppIcon name="refresh" :size="13" :class="{ spin: game.analysisLoading }" />
          重算
        </button>
      </div>
    </header>

    <div v-if="!match || !canAnalyze" class="analysis-empty">
      <div class="empty-icon"><AppIcon name="chart" :size="24" /></div>
      <div>
        <strong>{{ !match ? "开始一局后可用" : "当前没有可分析的底牌" }}</strong>
        <p>
          {{ !match ? "配置两位玩家并开始对局，这里会展示手牌分布。" : game.godView ? "等待发牌。" : "开启上帝视角后，可以分析所有玩家。" }}
        </p>
      </div>
    </div>

    <div v-else class="analysis-body">
      <div class="hero-strip">
        <div class="hero-cards">
          <PlayingCard
            v-for="(card, index) in holeCards"
            :key="card.code"
            :card="card"
            size="mini"
            :index="index"
          />
          <span class="hero-name">{{ summaryName }}</span>
        </div>
        <div class="hero-stat">
          <span class="hero-key">当前最佳</span>
          <strong :class="{ gold: Boolean(analysis?.current_best) }">
            {{ analysis?.current_best?.category_name || (analysis ? "未成牌" : "—") }}
          </strong>
        </div>
        <div class="hero-stat" v-if="analysis">
          <span class="hero-key">方法</span>
          <strong class="mono">{{ methodText }}</strong>
        </div>
        <div class="hero-stat" v-if="analysis">
          <span class="hero-key">耗时</span>
          <strong class="mono">{{ analysis.elapsed_ms.toFixed(1) }}ms</strong>
        </div>
        <div class="hero-stat" v-if="analysis">
          <span class="hero-key">还需公共牌</span>
          <strong class="mono">{{ analysis.needed_board_cards }} 张</strong>
        </div>
        <span v-if="stale" class="chip gold stale-chip">
          <span class="spinner"></span>
          牌面更新，重算中
        </span>
        <span v-if="analysis && !stale" class="chip emerald">
          <AppIcon name="check" :size="10" />
          已同步
        </span>
      </div>

      <div v-if="game.analysisLoading && !analysis" class="analysis-loading">
        <span class="spinner"></span>
        正在计算牌型分布…
      </div>

      <div v-else-if="game.analysisError" class="analysis-error">
        <AppIcon name="alert" :size="14" />
        <span>{{ game.analysisError }}</span>
        <button class="btn btn-sm" @click="reload">重试</button>
      </div>

      <div v-else-if="distribution.length" class="chart" :class="{ stale }">
        <div
          v-for="(item, index) in distribution"
          :key="item.key"
          class="chart-row"
          :class="{ best: bestKey === item.key, zero: item.probability === 0 }"
          :style="{ '--delay': `${index * 28}ms`, '--bar-color': item.color }"
        >
          <span class="chart-index mono">{{ String(index + 1).padStart(2, "0") }}</span>
          <span class="chart-name">{{ item.name }}</span>
          <div class="chart-track">
            <div class="chart-fill" :style="{ width: barWidth(item.probability) }"></div>
            <div v-if="bestKey === item.key" class="chart-marker">
              <AppIcon name="sparkles" :size="9" />
            </div>
          </div>
          <span class="chart-pct mono">{{ compactPercent(item.probability) }}</span>
          <span class="chart-count mono">{{ item.count.toLocaleString("en-US") }}</span>
        </div>
      </div>

      <div v-else class="analysis-loading">
        <span class="spinner"></span>
        等待牌型数据…
      </div>

      <footer class="analysis-foot">
        <span class="foot-note">
          <AppIcon name="alert" :size="11" />
          这是「最终牌型」分布，不是胜率；条形按本次最大概率归一化，百分比为绝对概率。
        </span>
        <span v-if="analysis" class="mono faint">
          已知 {{ analysis.known_cards.length }} 张 · 牌堆 {{ 52 - analysis.known_cards.length }} 张未知
        </span>
      </footer>
    </div>
  </section>
</template>

<style scoped>
.analysis-panel {
  padding: 16px 18px 14px;
}

.analysis-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
  flex-wrap: wrap;
}

.title-block { min-width: 0; }

.title-block h2 {
  margin: 3px 0 2px;
  font-size: 16px;
  font-weight: 750;
  letter-spacing: -0.015em;
}

.title-block p {
  margin: 0;
  font-size: 10.5px;
  color: var(--muted);
  line-height: 1.5;
}

.head-controls {
  display: flex;
  align-items: center;
  gap: 7px;
  flex-wrap: wrap;
}

.seat-switch button { font-size: 10px; padding: 4px 8px; }
.seat-switch { max-width: 100%; overflow-x: auto; }

.analysis-body {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 12px;
}

.hero-strip {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
  padding: 8px 10px;
  border-radius: 10px;
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.022);
}

.hero-cards {
  display: flex;
  align-items: center;
  gap: 4px;
  flex: none;
}

.hero-name {
  margin-left: 6px;
  font-size: 11px;
  font-weight: 700;
  color: var(--text-soft);
  max-width: 150px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.hero-stat {
  display: flex;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}

.hero-key { font-size: 8.5px; color: var(--faint); letter-spacing: 0.05em; }
.hero-stat strong { font-size: 10.5px; color: var(--text-soft); font-weight: 650; white-space: nowrap; }
.hero-stat strong.gold { color: var(--gold); }

.stale-chip { margin-left: auto; }
.stale-chip .spinner { width: 9px; height: 9px; }

.chart {
  display: flex;
  flex-direction: column;
  gap: 2px;
  transition: opacity 0.2s ease;
}

.chart.stale { opacity: 0.55; }

.chart-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 21px;
  padding: 1px 4px;
  border-radius: 5px;
  animation: row-in 0.35s ease-out both;
  animation-delay: var(--delay);
}

@keyframes row-in {
  from { opacity: 0; transform: translateX(-4px); }
  to { opacity: 1; transform: none; }
}

.chart-row:hover { background: rgba(255, 255, 255, 0.025); }
.chart-row.zero { opacity: 0.38; }

.chart-index {
  width: 18px;
  flex: none;
  font-size: 8px;
  color: var(--faint);
}

.chart-name {
  width: 68px;
  flex: none;
  font-size: 10.5px;
  color: var(--muted);
  white-space: nowrap;
}

.chart-row.best .chart-name {
  color: var(--gold);
  font-weight: 750;
}

.chart-track {
  position: relative;
  flex: 1;
  height: 8px;
  border-radius: 99px;
  background: rgba(255, 255, 255, 0.045);
  overflow: hidden;
}

.chart-fill {
  height: 100%;
  border-radius: 99px;
  background: linear-gradient(90deg, color-mix(in srgb, var(--bar-color) 65%, transparent), var(--bar-color));
  opacity: 0.72;
  transition: width 0.5s cubic-bezier(0.2, 0.8, 0.3, 1);
}

.chart-row.best .chart-fill {
  opacity: 1;
  box-shadow: 0 0 14px color-mix(in srgb, var(--bar-color) 40%, transparent);
}

.chart-marker {
  position: absolute;
  right: 4px;
  top: 50%;
  transform: translateY(-50%);
  color: #20170a;
  display: flex;
}

.chart-pct {
  width: 48px;
  flex: none;
  text-align: right;
  font-size: 10px;
  font-weight: 700;
  color: var(--text-soft);
}

.chart-row.best .chart-pct { color: var(--gold); }

.chart-count {
  width: 52px;
  flex: none;
  text-align: right;
  font-size: 8px;
  color: var(--faint);
}

.analysis-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
  padding-top: 6px;
  border-top: 1px solid var(--line);
}

.foot-note {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 9px;
  color: var(--faint);
  line-height: 1.5;
}

.analysis-empty {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 18px 4px 8px;
  color: var(--muted);
}

.empty-icon {
  width: 42px;
  height: 42px;
  display: grid;
  place-items: center;
  border-radius: 12px;
  border: 1px solid var(--line);
  color: var(--faint);
  flex: none;
}

.analysis-empty strong { font-size: 12px; color: var(--text-soft); }
.analysis-empty p { margin: 3px 0 0; font-size: 10px; color: var(--faint); line-height: 1.5; }

.analysis-loading {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 16px 4px;
  font-size: 10.5px;
  color: var(--muted);
}

.analysis-error {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 10px 12px;
  border-radius: 8px;
  border: 1px solid rgba(226, 112, 95, 0.28);
  background: var(--red-dim);
  color: #ffb3a8;
  font-size: 10.5px;
}

.analysis-error button { margin-left: auto; }

.spin { animation: spin 0.9s linear infinite; }

@media (max-width: 720px) {
  .analysis-panel { padding: 13px 12px 12px; }
  .chart-name { width: 54px; font-size: 10px; }
  .chart-count { display: none; }
  .chart-index { width: 14px; }
  .hero-strip { gap: 10px; }
}
</style>
