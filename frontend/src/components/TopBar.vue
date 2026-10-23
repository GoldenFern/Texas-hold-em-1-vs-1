<script setup lang="ts">
import { computed } from "vue";
import AppIcon from "./AppIcon.vue";
import LanguageSwitch from "./LanguageSwitch.vue";
import { iconUrl } from "../api";
import { board } from "../boardReveal";
import { seatDisplayName } from "../format";
import { t } from "../i18n";
import { game, openSetup, toggleGodView } from "../store";
import type { SeatInfo } from "../types";

const snapshot = computed(() => (
  (board.busy || board.holeHold) && board.baseSnapshot ? board.baseSnapshot : game.snapshot
));
const match = computed(() => snapshot.value?.match || null);
const seats = computed(() => match.value?.seats || []);
const seatLabel = (seat: SeatInfo) => (
  seatDisplayName(seat.name, seat.kind, seat.model_name) || seat.name
);
const matchupTitle = computed(() => seats.value.map(seatLabel).join("  vs  "));
const handId = computed(() => snapshot.value?.hand_id || 0);
</script>

<template>
  <header class="topbar">
    <div class="brand">
      <div class="brand-mark"><span>♠</span></div>
      <div class="brand-name">HEADS-UP <span class="brand-lab">LAB</span></div>
    </div>

    <div class="top-info" v-if="match" :title="matchupTitle">
      <div class="matchup">
        <template v-for="(seat, index) in seats" :key="seat.seat">
          <span class="matchup-side">
            <span class="matchup-icon" :class="seat.kind">
              <img v-if="seat.icon" :src="iconUrl(seat.icon)" :alt="seat.name" />
              <AppIcon
                v-else
                :name="seat.kind === 'human' ? 'user' : seat.kind === 'bot' ? 'bot' : 'sparkles'"
                :size="12"
              />
            </span>
            <span v-if="seat.kind === 'human'" class="matchup-label">{{ t("common.you") }}</span>
          </span>
          <span v-if="index === 0" class="vs">vs</span>
        </template>
      </div>
      <span class="hand mono">#{{ handId }}</span>
    </div>
    <div class="top-info" v-else>
      <span class="hand faint">{{ t("topbar.noMatch") }}</span>
    </div>

    <div class="top-actions">
      <span v-if="!game.connected" class="conn offline" :title="t('topbar.offlineTitle')">
        <span class="dot"></span>
        {{ t("topbar.offline") }}
      </span>
      <LanguageSwitch />
      <button
        class="btn btn-ghost btn-sm god-btn"
        :class="{ active: game.godView }"
        :disabled="game.godViewBusy || !match"
        @click="toggleGodView"
        :title="t('topbar.godViewTitle')"
      >
        <AppIcon :name="game.godView ? 'eye' : 'eyeOff'" :size="15" />
        <span class="god-label">{{ t("topbar.godView") }}</span>
      </button>
      <button class="btn btn-sm new-btn" @click="openSetup">
        <AppIcon name="plus" :size="15" />
        <span>{{ t("topbar.newMatch") }}</span>
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
  padding: 10px 22px;
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
  width: 34px;
  height: 34px;
  display: grid;
  place-items: center;
  border-radius: 11px;
  border: 1px solid rgba(232, 195, 122, 0.32);
  background:
    radial-gradient(circle at 35% 25%, rgba(246, 216, 156, 0.22), transparent 60%),
    linear-gradient(150deg, rgba(232, 195, 122, 0.16), rgba(232, 195, 122, 0.03));
  color: var(--gold-bright);
  font-size: calc(18px * var(--type-scale));
  box-shadow: 0 8px 22px rgba(0, 0, 0, 0.25);
}

.brand-name {
  font-size: calc(13px * var(--type-scale));
  font-weight: 800;
  letter-spacing: 0.12em;
  color: var(--text);
  line-height: 1.15;
  white-space: nowrap;
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

.top-info {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
  flex: 1;
  overflow: hidden;
}

.matchup {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: none;
}

.matchup-side {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.matchup-icon {
  width: 24px;
  height: 24px;
  flex: none;
  display: grid;
  place-items: center;
  border-radius: 7px;
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.04);
  color: var(--text-soft);
}

.matchup-icon.llm {
  background: #f1ecdf;
  border-color: rgba(255, 255, 255, 0.2);
  padding: 2px;
}

.matchup-icon.bot {
  color: var(--blue);
  border-color: rgba(121, 169, 255, 0.26);
  background: var(--blue-dim);
}

.matchup-icon img {
  width: 18px;
  height: 18px;
  object-fit: contain;
}

.matchup-label {
  font-size: calc(11.5px * var(--type-scale));
  color: var(--text-soft);
}

.vs {
  font-family: var(--font-mono);
  font-size: calc(8.5px * var(--type-scale));
  letter-spacing: 0.1em;
  color: var(--faint);
}

.hand {
  flex: none;
  font-size: calc(10px * var(--type-scale));
  color: var(--faint);
  letter-spacing: 0.06em;
}

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
  letter-spacing: 0.1em;
  color: var(--red);
  padding: 5px 8px;
  border-radius: 7px;
  border: 1px solid rgba(226, 112, 95, 0.28);
  background: var(--red-dim);
}

.conn .dot { background: var(--red); }

.god-btn.active {
  color: var(--gold);
  border-color: rgba(232, 195, 122, 0.28);
  background: var(--gold-dim);
}

.god-btn.active:disabled { opacity: 0.38; }

.new-btn {
  background: rgba(255, 255, 255, 0.05);
}

@media (max-width: 860px) {
  .topbar { padding: 9px 14px; gap: 10px; }
  .god-label { display: none; }
  .conn span:last-child { display: none; }
}

@media (max-width: 620px) {
  .brand-name { display: none; }
  .brand-mark { width: 30px; height: 30px; font-size: calc(16px * var(--type-scale)); }
  .new-btn span { display: none; }
}
</style>
