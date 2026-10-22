<script setup lang="ts">
import { computed } from "vue";
import ActionBar from "./ActionBar.vue";
import AnimatedNumber from "./AnimatedNumber.vue";
import DeckStrip from "./DeckStrip.vue";
import PlayingCard from "./PlayingCard.vue";
import SeatPod from "./SeatPod.vue";
import AppIcon from "./AppIcon.vue";
import { chips, streetLabel } from "../format";
import { game, openSetup } from "../store";
import type { CardInfo, PlayerState } from "../types";

const snapshot = computed(() => game.snapshot);
const match = computed(() => snapshot.value?.match || null);
const players = computed(() => snapshot.value?.players || []);
const seats = computed(() => match.value?.seats || []);

const humanSeatIndex = computed(() => seats.value.find((seat) => seat.kind === "human")?.seat ?? null);
const topSeat = computed(() => {
  if (humanSeatIndex.value === null) return 0;
  return humanSeatIndex.value === 0 ? 1 : 0;
});
const bottomSeat = computed(() => (topSeat.value === 0 ? 1 : 0));

function playerAt(seat: number): PlayerState | null {
  return players.value.find((player) => player.seat === seat) || null;
}

function seatInfoAt(seat: number) {
  return seats.value.find((item) => item.seat === seat) || null;
}

function showCardsAt(seat: number): boolean {
  const player = playerAt(seat);
  return Boolean(player && !player.hole_hidden);
}

const community = computed<CardInfo[]>(() => snapshot.value?.community || []);
const boardSlots = computed(() => Array.from({ length: 5 }, (_, index) => community.value[index] || null));
const pot = computed(() => Number(snapshot.value?.pot || 0));
const currentBet = computed(() => Number(snapshot.value?.current_bet || 0));
const toCall = computed(() => Number(snapshot.value?.to_call || 0));
const street = computed(() => snapshot.value?.street || "waiting");
const buttonSeat = computed(() => snapshot.value?.button ?? 0);
const result = computed(() => (game.lastResult?.hand_id === snapshot.value?.hand_id ? game.lastResult : null));
const winnerSeats = computed(() => result.value?.winner_seats || []);
const deckVisible = computed(() => Boolean(game.godView && match.value));
const deckRemaining = computed(() => snapshot.value?.deck_remaining || []);
const burned = computed(() => snapshot.value?.burned || []);
const deckCount = computed(() => snapshot.value?.deck_remaining_count || 0);

const phases = [
  { key: "preflop", label: "翻牌前", short: "PRE" },
  { key: "flop", label: "翻牌", short: "FLOP" },
  { key: "turn", label: "转牌", short: "TURN" },
  { key: "river", label: "河牌", short: "RIVER" },
];

const streetIndex = computed(() => {
  const order = ["preflop", "flop", "turn", "river", "showdown", "finished"];
  return order.indexOf(street.value);
});

function phaseState(index: number): "done" | "active" | "todo" {
  if (street.value === "finished" || street.value === "showdown") return "done";
  if (index < streetIndex.value) return "done";
  if (index === streetIndex.value) return "active";
  return "todo";
}

const boardCaption = computed(() => {
  if (street.value === "finished" || street.value === "showdown") {
    return result.value?.reason === "fold" ? "弃牌结束" : "摊牌";
  }
  if (street.value === "preflop") return "等待翻牌";
  return streetLabel(street.value);
});

const potChips = computed(() => {
  const amount = pot.value;
  if (amount <= 0) return 0;
  if (amount < 60) return 1;
  if (amount < 200) return 2;
  if (amount < 600) return 3;
  if (amount < 1500) return 4;
  return 5;
});

const tableHint = computed(() => {
  if (!match.value) return "配置两个玩家，开始一局单挑";
  if (match.value.match_over) return "比赛结束";
  if (street.value === "finished") return "本手结束";
  if (snapshot.value?.acting_kind === "human") return "轮到你行动";
  return "";
});

const dealerStyle = computed(() => {
  if (buttonSeat.value === topSeat.value) return { top: "22%", left: "71%" };
  return { bottom: "21%", left: "71%" };
});
</script>

<template>
  <section class="table-panel surface">
    <div class="table-toolbar">
      <div class="phase-track">
        <template v-for="(phase, index) in phases" :key="phase.key">
          <div class="phase-step" :class="phaseState(index)">
            <span class="phase-dot">
              <AppIcon v-if="phaseState(index) === 'done'" name="check" :size="9" :stroke-width="2.4" />
              <span v-else>{{ index + 1 }}</span>
            </span>
            <span class="phase-label">{{ phase.label }}</span>
          </div>
          <div v-if="index < phases.length - 1" class="phase-line" :class="{ done: phaseState(index) === 'done' }"></div>
        </template>
      </div>
      <div class="table-meta">
        <span class="chip">
          <span class="meta-key">底池</span>
          <strong class="mono gold">{{ chips(pot) }}</strong>
        </span>
        <span class="chip" v-if="currentBet > 0">
          <span class="meta-key">当前注</span>
          <strong class="mono">{{ chips(currentBet) }}</strong>
        </span>
        <span class="chip red" v-if="toCall > 0">
          <span class="meta-key">需跟</span>
          <strong class="mono">{{ chips(toCall) }}</strong>
        </span>
        <span class="chip" :class="{ emerald: game.godView }">
          <AppIcon :name="game.godView ? 'eye' : 'eyeOff'" :size="11" />
          {{ game.godView ? "全知" : "隐藏" }}
        </span>
      </div>
    </div>

    <div class="felt-wrap">
      <div class="felt">
        <div class="felt-ring"></div>
        <div class="felt-inner">
          <div class="felt-watermark">♠</div>
          <div class="felt-brand">HEADS-UP <span>LAB</span></div>

          <template v-if="match">
            <SeatPod
              :player="playerAt(topSeat)"
              :seat-info="seatInfoAt(topSeat)"
              position="top"
              :active="snapshot?.acting_seat === topSeat && street !== 'finished'"
              :god-view="game.godView"
              :result="result"
              :show-cards="showCardsAt(topSeat)"
            />
            <SeatPod
              :player="playerAt(bottomSeat)"
              :seat-info="seatInfoAt(bottomSeat)"
              position="bottom"
              :active="snapshot?.acting_seat === bottomSeat && street !== 'finished'"
              :god-view="game.godView"
              :result="result"
              :show-cards="showCardsAt(bottomSeat)"
            />

            <div class="pot-display">
              <div class="pot-label">
                <span>POT</span>
                <span v-if="result" class="pot-result-label">{{ result.reason === "fold" ? "弃牌" : "摊牌" }}</span>
              </div>
              <div class="pot-value"><AnimatedNumber :value="pot" /></div>
              <div class="pot-chips" v-if="potChips">
                <span v-for="n in potChips" :key="n" class="pot-chip" :style="{ '--i': n }"></span>
              </div>
            </div>

            <div class="board-block">
              <div class="board">
                <PlayingCard
                  v-for="(card, index) in boardSlots"
                  :key="card?.code || `slot-${index}`"
                  :card="card"
                  :index="index"
                  :animate="Boolean(card)"
                  :size="'community'"
                  :dim="street === 'finished' && winnerSeats.length === 0"
                />
              </div>
              <div class="board-caption">
                <span class="caption-line"></span>
                <span>{{ boardCaption }}</span>
              </div>
            </div>

            <div class="dealer-token" :style="dealerStyle">
              <span>D</span>
            </div>

            <div v-if="tableHint" class="table-hint">
              <span class="dot" :class="{ live: snapshot?.acting_kind === 'human' }"></span>
              {{ tableHint }}
            </div>
          </template>

          <div v-if="!match" class="empty-overlay">
            <div class="empty-mark">♠</div>
            <p>一张桌子，两位玩家。</p>
            <span>人类 · LLM · RLCard / Project RL，任选其二。</span>
            <button class="btn btn-primary empty-cta" @click="openSetup">
              <AppIcon name="settings" :size="14" />
              配置对局
            </button>
            <div class="empty-legend">
              <span><AppIcon name="user" :size="11" /> 人类</span>
              <span><AppIcon name="sparkles" :size="11" /> LLM</span>
              <span><AppIcon name="bot" :size="11" /> RLCard / Project RL</span>
            </div>
          </div>
        </div>
      </div>
    </div>

    <DeckStrip
      v-if="match"
      :remaining="deckRemaining"
      :burned="burned"
      :visible="deckVisible"
      :count="deckCount"
    />

    <ActionBar />
  </section>
</template>

<style scoped>
.table-panel {
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.table-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  padding: 11px 16px;
  border-bottom: 1px solid var(--line);
  background: rgba(5, 13, 10, 0.35);
  flex-wrap: wrap;
}

.phase-track {
  display: flex;
  align-items: center;
  gap: 0;
  min-width: 0;
}

.phase-step {
  display: flex;
  align-items: center;
  gap: 5px;
  color: var(--faint);
  font-size: calc(10px * var(--type-scale));
  font-weight: 700;
  letter-spacing: 0.02em;
  white-space: nowrap;
}

.phase-step.active { color: var(--gold); }
.phase-step.done { color: var(--emerald); }

.phase-dot {
  width: 17px;
  height: 17px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  border: 1px solid var(--line-strong);
  font-family: var(--font-mono);
  font-size: calc(8px * var(--type-scale));
  color: var(--faint);
  transition: all 0.2s ease;
}

.phase-step.active .phase-dot {
  border-color: rgba(232, 195, 122, 0.55);
  background: var(--gold-dim);
  color: var(--gold);
  box-shadow: 0 0 12px rgba(232, 195, 122, 0.14);
}

.phase-step.done .phase-dot {
  border-color: rgba(84, 214, 168, 0.42);
  background: var(--emerald-dim);
  color: var(--emerald);
}

.phase-line {
  width: clamp(12px, 3vw, 34px);
  height: 1px;
  margin: 0 6px;
  background: var(--line-strong);
  transition: background 0.2s ease;
}

.phase-line.done { background: rgba(84, 214, 168, 0.32); }

.table-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.table-meta .chip { padding: 4px 9px; }
.meta-key { color: var(--faint); font-size: calc(9px * var(--type-scale)); }
.table-meta strong { font-size: calc(10.5px * var(--type-scale)); }

.felt-wrap {
  padding: 14px 14px 8px;
}

.felt {
  position: relative;
  width: 100%;
  aspect-ratio: 1.62 / 1;
  min-height: 430px;
  max-height: 610px;
  border-radius: 46% / 50%;
  padding: 11px;
  background:
    linear-gradient(160deg, rgba(232, 195, 122, 0.16), rgba(232, 195, 122, 0.02) 38%, rgba(0, 0, 0, 0.3)),
    linear-gradient(180deg, #1b2b23, #0c1712);
  box-shadow:
    0 34px 70px rgba(0, 0, 0, 0.46),
    0 0 0 1px rgba(232, 195, 122, 0.14) inset,
    0 2px 0 rgba(255, 255, 255, 0.05) inset;
}

.felt-ring {
  position: absolute;
  inset: 7px;
  border-radius: 46% / 50%;
  border: 1px solid rgba(232, 195, 122, 0.16);
  pointer-events: none;
}

.felt-inner {
  position: relative;
  width: 100%;
  height: 100%;
  border-radius: 46% / 50%;
  overflow: hidden;
  background:
    radial-gradient(ellipse at 50% 42%, rgba(38, 112, 84, 0.72) 0%, rgba(20, 72, 54, 0.7) 38%, rgba(10, 40, 30, 0.92) 70%, #071d15 100%),
    radial-gradient(ellipse at 50% 100%, rgba(0, 0, 0, 0.32), transparent 60%);
  box-shadow:
    inset 0 0 80px rgba(0, 0, 0, 0.42),
    inset 0 0 0 1px rgba(255, 255, 255, 0.04);
}

.felt-inner::before {
  content: "";
  position: absolute;
  inset: 0;
  opacity: 0.3;
  background-image:
    repeating-radial-gradient(ellipse at 50% 50%, transparent 0, transparent 18px, rgba(255, 255, 255, 0.018) 19px, transparent 20px);
  pointer-events: none;
}

.felt-inner::after {
  content: "";
  position: absolute;
  left: 8%;
  right: 8%;
  top: 50%;
  height: 1px;
  background: linear-gradient(90deg, transparent, rgba(232, 195, 122, 0.1), transparent);
  pointer-events: none;
}

.felt-watermark {
  position: absolute;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -52%);
  font-size: clamp(calc(150px * var(--type-scale)), 26vw, calc(280px * var(--type-scale)));
  line-height: 1;
  color: rgba(255, 255, 255, 0.025);
  pointer-events: none;
  user-select: none;
}

.felt-brand {
  position: absolute;
  left: 50%;
  top: 11%;
  transform: translateX(-50%);
  font-size: calc(8px * var(--type-scale));
  font-weight: 700;
  letter-spacing: 0.42em;
  color: rgba(232, 195, 122, 0.24);
  pointer-events: none;
  white-space: nowrap;
}

.felt-brand span { color: rgba(232, 195, 122, 0.42); }

.pot-display {
  position: absolute;
  left: 50%;
  top: 32%;
  transform: translateX(-50%);
  z-index: 5;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  min-width: 120px;
  padding: 6px 16px 8px;
  border-radius: 999px;
  border: 1px solid rgba(232, 195, 122, 0.2);
  background: rgba(5, 14, 10, 0.55);
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.28);
  backdrop-filter: blur(10px);
}

.pot-display.empty { opacity: 0.3; }

.pot-label {
  display: flex;
  align-items: center;
  gap: 8px;
  font-family: var(--font-mono);
  font-size: calc(8px * var(--type-scale));
  font-weight: 700;
  letter-spacing: 0.26em;
  color: var(--gold);
  opacity: 0.85;
}

.pot-result-label {
  font-family: var(--font-ui);
  font-size: calc(8px * var(--type-scale));
  letter-spacing: 0.08em;
  color: var(--muted);
}

.pot-value {
  font-family: var(--font-mono);
  font-size: clamp(calc(19px * var(--type-scale)), 2.2vw, calc(27px * var(--type-scale)));
  font-weight: 700;
  line-height: 1.1;
  color: var(--gold-bright);
  letter-spacing: -0.03em;
  text-shadow: 0 2px 14px rgba(232, 195, 122, 0.2);
}

.pot-chips {
  display: flex;
  gap: 0;
  height: 7px;
  align-items: flex-end;
}

.pot-chip {
  width: 14px;
  height: 6px;
  margin-left: -5px;
  border-radius: 50%;
  border: 1px solid rgba(255, 255, 255, 0.32);
  background: linear-gradient(180deg, #f0d194, #b98a3f);
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.3);
  transform: translateY(calc(var(--i) * -2px));
}

.board-block {
  position: absolute;
  left: 50%;
  top: 45%;
  transform: translateX(-50%);
  z-index: 6;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 7px;
}

.board {
  display: flex;
  gap: 7px;
}

.board-caption {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: calc(9px * var(--type-scale));
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: rgba(232, 226, 210, 0.42);
  white-space: nowrap;
}

.caption-line {
  width: 20px;
  height: 1px;
  background: rgba(232, 195, 122, 0.28);
}

.caption-pot { color: rgba(232, 195, 122, 0.5); }

.dealer-token {
  position: absolute;
  z-index: 7;
  width: 25px;
  height: 25px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  background: linear-gradient(145deg, #f5d89d, #bd8c38);
  color: #241a08;
  font-family: var(--font-mono);
  font-size: calc(10px * var(--type-scale));
  font-weight: 800;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.38), 0 0 0 2px rgba(6, 16, 12, 0.75);
}

.table-hint {
  position: absolute;
  left: 7%;
  bottom: 9%;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 9px;
  border-radius: 999px;
  border: 1px solid rgba(232, 195, 122, 0.14);
  background: rgba(5, 14, 10, 0.42);
  color: rgba(232, 226, 210, 0.55);
  font-size: calc(9.5px * var(--type-scale));
  letter-spacing: 0.06em;
  white-space: nowrap;
  backdrop-filter: blur(6px);
}

.empty-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  background:
    radial-gradient(ellipse at 50% 42%, rgba(16, 44, 33, 0.3), transparent 62%),
    rgba(4, 12, 9, 0.35);
  backdrop-filter: blur(2px);
  z-index: 10;
}

.empty-cta { margin-top: 10px; }

.empty-legend {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 12px;
  font-size: calc(9px * var(--type-scale));
  color: var(--faint);
}

.empty-legend span {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.empty-mark {
  font-size: calc(38px * var(--type-scale));
  color: rgba(232, 195, 122, 0.4);
  line-height: 1;
}

.empty-overlay p {
  margin: 6px 0 0;
  font-size: calc(15px * var(--type-scale));
  font-weight: 750;
  letter-spacing: -0.01em;
}

.empty-overlay span {
  font-size: calc(11px * var(--type-scale));
  color: var(--muted);
}

@media (max-width: 1180px) {
  .felt { min-height: 460px; }
}

@media (max-width: 720px) {
  .table-toolbar { padding: 9px 10px; gap: 8px; }
  .phase-label { display: none; }
  .phase-line { width: 16px; margin: 0 4px; }
  .felt-wrap { padding: 8px 8px 4px; }
  .felt { height: 430px; min-height: 430px; aspect-ratio: auto; border-radius: 42% / 46%; }
  .felt-inner { height: 100%; border-radius: 42% / 46%; }
  .board { gap: 4px; }
  .board-block { top: 45%; }
  .pot-display { top: 33%; min-width: 100px; padding: 5px 12px 6px; }
  .table-hint { left: 5%; bottom: 8%; }
  .felt-brand { top: 8%; }
}
</style>
