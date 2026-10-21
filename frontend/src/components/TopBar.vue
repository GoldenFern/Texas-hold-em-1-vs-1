<script setup lang="ts">
import { computed } from "vue";
import AppIcon from "./AppIcon.vue";
import { game, openSetup, toggleGodView } from "../store";
import { chips, shortName, streetLabel } from "../format";

const match = computed(() => game.snapshot?.match || null);
const seats = computed(() => match.value?.seats || []);
const matchup = computed(() => {
  if (seats.value.length !== 2) return "";
  return `${shortName(seats.value[0].name, 16)}  vs  ${shortName(seats.value[1].name, 16)}`;
});
const street = computed(() => game.snapshot?.street || "");
const pot = computed(() => game.snapshot?.pot || 0);
const handId = computed(() => game.snapshot?.hand_id || 0);
</script>

<template>
  <header class="topbar">
    <div class="brand">
      <div class="brand-mark"><span>♠</span></div>
      <div class="brand-text">
        <div class="brand-name">HEADS-UP <span class="brand-lab">LAB</span></div>
        <div class="brand-sub">二人德州扑克 · 实验室</div>
      </div>
    </div>

    <div class="top-info" v-if="match">
      <span class="matchup">{{ matchup }}</span>
      <span class="chip mono">#{{ handId }}</span>
      <span class="chip mono">{{ match.rules.small_blind }}/{{ match.rules.big_blind }}</span>
      <span class="chip gold">{{ match.match_over ? "比赛结束" : streetLabel(street) }}</span>
      <span class="chip mono pot-chip"><AppIcon name="coins" :size="12" /> {{ chips(pot) }}</span>
    </div>
    <div class="top-info" v-else>
      <span class="chip">尚未开局</span>
    </div>

    <div class="top-actions">
      <div class="conn" :class="{ ok: game.connected }">
        <span class="dot" :class="{ live: game.connected }"></span>
        <span>{{ game.connected ? "LIVE" : "OFFLINE" }}</span>
      </div>
      <button
        class="btn btn-ghost btn-sm god-btn"
        :class="{ active: game.godView }"
        :disabled="game.godViewBusy || !match"
        @click="toggleGodView"
        title="显示双方手牌与牌堆剩余牌"
      >
        <AppIcon :name="game.godView ? 'eye' : 'eyeOff'" :size="15" />
        <span class="god-label">上帝视角</span>
      </button>
      <button class="btn btn-sm new-btn" @click="openSetup">
        <AppIcon name="plus" :size="15" />
        <span>新比赛</span>
      </button>
    </div>
  </header>
</template>

<style scoped>
.topbar {
  position: sticky;
  top: 0;
  z-index: 30;
  display: flex;
  align-items: center;
  gap: 18px;
  padding: 12px 22px;
  border-bottom: 1px solid var(--line);
  background: rgba(6, 16, 12, 0.72);
  backdrop-filter: blur(18px);
  -webkit-backdrop-filter: blur(18px);
}

.brand {
  display: flex;
  align-items: center;
  gap: 11px;
  flex: none;
}

.brand-mark {
  width: 38px;
  height: 38px;
  display: grid;
  place-items: center;
  border-radius: 12px;
  border: 1px solid rgba(232, 195, 122, 0.32);
  background:
    radial-gradient(circle at 35% 25%, rgba(246, 216, 156, 0.22), transparent 60%),
    linear-gradient(150deg, rgba(232, 195, 122, 0.16), rgba(232, 195, 122, 0.03));
  color: var(--gold-bright);
  font-size: calc(20px * var(--type-scale));
  box-shadow: 0 8px 22px rgba(0, 0, 0, 0.25);
}

.brand-name {
  font-size: calc(14px * var(--type-scale));
  font-weight: 800;
  letter-spacing: 0.12em;
  color: var(--text);
  line-height: 1.15;
}

.brand-lab {
  color: var(--gold);
  font-weight: 700;
  letter-spacing: 0.2em;
  font-size: calc(9px * var(--type-scale));
  margin-left: 4px;
  padding: 1px 5px;
  border: 1px solid rgba(232, 195, 122, 0.34);
  border-radius: 4px;
  vertical-align: 1px;
}

.brand-sub {
  font-size: calc(10px * var(--type-scale));
  color: var(--faint);
  letter-spacing: 0.08em;
  margin-top: 2px;
}

.top-info {
  display: flex;
  align-items: center;
  gap: 7px;
  min-width: 0;
  flex: 1;
  overflow: hidden;
}

.matchup {
  max-width: 300px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: calc(11.5px * var(--type-scale));
  color: var(--text-soft);
  padding-right: 4px;
  border-right: 1px solid var(--line);
  margin-right: 3px;
}

.pot-chip { color: var(--gold); }

.top-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: none;
}

.conn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-family: var(--font-mono);
  font-size: calc(9px * var(--type-scale));
  letter-spacing: 0.12em;
  color: var(--faint);
  padding: 5px 8px;
  border-radius: 7px;
  border: 1px solid transparent;
}

.conn.ok {
  color: var(--emerald);
  border-color: rgba(84, 214, 168, 0.18);
  background: rgba(84, 214, 168, 0.06);
}

.god-btn.active {
  color: var(--gold);
  border-color: rgba(232, 195, 122, 0.28);
  background: var(--gold-dim);
}

.god-btn.active:disabled { opacity: 0.38; }

.new-btn {
  background: rgba(255, 255, 255, 0.05);
}

@media (max-width: 1180px) {
  .matchup { max-width: 200px; }
  .top-info .chip:nth-child(4) { display: none; }
}

@media (max-width: 860px) {
  .topbar { padding: 10px 14px; gap: 10px; }
  .brand-sub { display: none; }
  .matchup { display: none; }
  .top-info { gap: 5px; }
  .god-label { display: none; }
}

@media (max-width: 620px) {
  .brand-name { font-size: calc(12px * var(--type-scale)); letter-spacing: 0.08em; }
  .brand-mark { width: 32px; height: 32px; font-size: calc(17px * var(--type-scale)); }
  .top-info .chip:nth-child(2) { display: none; }
  .conn { display: none; }
  .new-btn span { display: none; }
}
</style>
