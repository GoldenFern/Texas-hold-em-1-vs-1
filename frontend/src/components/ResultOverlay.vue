<script setup lang="ts">
import { computed } from "vue";
import AppIcon from "./AppIcon.vue";
import PlayingCard from "./PlayingCard.vue";
import { chips, signedChips } from "../format";
import {
  dismissResult,
  game,
  nextHand,
  openSetup,
} from "../store";
import type { CardInfo } from "../types";

const result = computed(() => game.lastResult);
const match = computed(() => game.snapshot?.match || null);
const players = computed(() => game.snapshot?.players || []);
const seats = computed(() => match.value?.seats || []);

function seatName(seat: number): string {
  return players.value.find((player) => player.seat === seat)?.name
    || seats.value.find((item) => item.seat === seat)?.name
    || `座位 ${seat}`;
}

function cardFromCode(code: string): CardInfo {
  const rank = code.slice(0, -1);
  const suit = code.slice(-1);
  const symbol = ({ c: "♣", d: "♦", h: "♥", s: "♠" } as Record<string, string>)[suit] || suit;
  return {
    code,
    rank,
    suit,
    symbol,
    red: suit === "h" || suit === "d",
    label: `${rank}${symbol}`,
  };
}

const winnerNames = computed(() => {
  if (!result.value) return [];
  return result.value.winner_seats.map((seat) => seatName(seat));
});
const isFold = computed(() => result.value?.reason === "fold");
const matchOver = computed(() => Boolean(result.value?.match_over || match.value?.match_over));
const title = computed(() => {
  if (!result.value) return "";
  if (matchOver.value) return winnerNames.value.includes(seatName(0)) ? "比赛结束" : "比赛结束";
  if (isFold.value) return "弃牌结束";
  return "摊牌";
});
const boardCards = computed(() => (result.value?.board || []).map(cardFromCode));
const showdown = computed(() => result.value?.showdown || []);
const resultPlayers = computed(() => result.value?.players || []);
const pot = computed(() => result.value?.pot || 0);

function playerResult(seat: number) {
  return resultPlayers.value.find((item) => item.seat === seat) || null;
}

function netTone(seat: number): string {
  const net = playerResult(seat)?.net || 0;
  if (net > 0) return "up";
  if (net < 0) return "down";
  return "flat";
}

function close(): void {
  dismissResult();
}

async function playNext(): Promise<void> {
  dismissResult();
  await nextHand();
}

function newMatch(): void {
  dismissResult();
  openSetup();
}
</script>

<template>
  <div v-if="game.resultOpen && result" class="modal-backdrop result-backdrop" @click.self="close">
    <div class="result-card" role="dialog" aria-modal="true" aria-label="本手结果">
      <div class="result-glow"></div>
      <header class="result-head">
        <div class="result-mark" :class="{ over: matchOver }">
          <AppIcon :name="matchOver ? 'shield' : isFold ? 'x' : 'sparkles'" :size="24" />
        </div>
        <div class="result-heading">
          <span class="eyebrow">{{ matchOver ? "MATCH COMPLETE" : isFold ? "FOLD" : "SHOWDOWN" }}</span>
          <h2>{{ title }}</h2>
          <p>
            <template v-if="winnerNames.length">
              <strong>{{ winnerNames.join(" · ") }}</strong>
              赢下底池 <span class="mono gold">{{ chips(pot) }}</span>
            </template>
            <template v-else>本手结束</template>
          </p>
        </div>
        <button class="btn btn-ghost btn-icon close-btn" aria-label="关闭" @click="close">
          <AppIcon name="x" :size="18" />
        </button>
      </header>

      <div class="result-body">
        <div v-if="boardCards.length" class="board-row">
          <span class="row-label">公共牌</span>
          <div class="board-cards">
            <PlayingCard v-for="(card, index) in boardCards" :key="card.code" :card="card" size="mini" :index="index" />
          </div>
        </div>

        <div class="players-grid" :class="{ single: (seats || []).length === 1 }">
          <div
            v-for="seat in [0, 1]"
            :key="seat"
            class="result-player"
            :class="{ winner: result.winner_seats.includes(seat), folded: result.reason === 'fold' && !result.winner_seats.includes(seat) }"
          >
            <div class="player-head">
              <span class="player-name">{{ seatName(seat) }}</span>
              <span v-if="result.winner_seats.includes(seat)" class="winner-chip">
                <AppIcon name="sparkles" :size="10" /> 赢家
              </span>
              <span class="player-net mono" :class="netTone(seat)">
                {{ signedChips(playerResult(seat)?.net || 0) }}
              </span>
            </div>

            <div class="showdown-row" v-if="showdown.length">
              <template v-for="item in showdown.filter((entry) => entry.seat === seat)" :key="item.seat">
                <span class="category-chip">{{ item.category_name }}</span>
                <div class="best-five">
                  <PlayingCard
                    v-for="(code, index) in item.best_five"
                    :key="code"
                    :card="cardFromCode(code)"
                    size="mini"
                    :index="index"
                    :highlight="result.winner_seats.includes(seat)"
                  />
                </div>
              </template>
            </div>
            <div v-else class="fold-note">
              {{ result.winner_seats.includes(seat) ? "对手弃牌，未摊牌" : "已弃牌" }}
            </div>

            <div class="player-foot">
              <span>本手投入 <strong class="mono">{{ chips(playerResult(seat)?.total_bet || 0) }}</strong></span>
              <span>剩余 <strong class="mono">{{ chips(playerResult(seat)?.chips_after || 0) }}</strong></span>
            </div>
          </div>
        </div>

        <div v-if="matchOver" class="match-final">
          <span class="match-final-label">最终筹码</span>
          <div class="final-stacks">
            <span v-for="seat in [0, 1]" :key="seat" class="final-stack">
              <span class="faint">{{ seatName(seat) }}</span>
              <strong class="mono">{{ chips(players.find((player) => player.seat === seat)?.chips || 0) }}</strong>
            </span>
          </div>
        </div>
      </div>

      <footer class="result-foot">
        <button class="btn" @click="close">查看牌桌</button>
        <div class="foot-actions">
          <button v-if="matchOver" class="btn btn-primary" @click="newMatch">
            <AppIcon name="plus" :size="14" />
            新比赛
          </button>
          <button v-else-if="match?.can_next_hand" class="btn btn-primary" @click="playNext">
            下一手
            <AppIcon name="arrowRight" :size="14" />
          </button>
        </div>
      </footer>
    </div>
  </div>
</template>

<style scoped>
.result-backdrop { z-index: 70; }

.result-card {
  position: relative;
  width: min(660px, 100%);
  max-height: calc(100vh - 48px);
  display: flex;
  flex-direction: column;
  border-radius: 22px;
  border: 1px solid rgba(232, 195, 122, 0.22);
  background:
    radial-gradient(600px 260px at 50% -10%, rgba(232, 195, 122, 0.13), transparent 65%),
    linear-gradient(180deg, rgba(20, 36, 29, 0.99), rgba(8, 18, 14, 0.99));
  box-shadow: 0 40px 100px rgba(0, 0, 0, 0.6), 0 0 0 1px rgba(255, 255, 255, 0.04) inset;
  overflow: hidden;
  animation: result-in 0.26s cubic-bezier(0.16, 0.9, 0.3, 1);
}

@keyframes result-in {
  from { opacity: 0; transform: translateY(12px) scale(0.98); }
  to { opacity: 1; transform: none; }
}

.result-glow {
  position: absolute;
  top: -90px;
  left: 50%;
  width: 320px;
  height: 180px;
  transform: translateX(-50%);
  background: radial-gradient(ellipse, rgba(232, 195, 122, 0.18), transparent 70%);
  pointer-events: none;
}

.result-head {
  position: relative;
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 22px 22px 14px;
}

.result-mark {
  width: 50px;
  height: 50px;
  flex: none;
  display: grid;
  place-items: center;
  border-radius: 14px;
  border: 1px solid rgba(232, 195, 122, 0.35);
  background: linear-gradient(150deg, rgba(232, 195, 122, 0.2), rgba(232, 195, 122, 0.04));
  color: var(--gold-bright);
  box-shadow: 0 10px 26px rgba(0, 0, 0, 0.3);
}

.result-mark.over { color: var(--emerald); border-color: rgba(84, 214, 168, 0.35); background: var(--emerald-dim); }

.result-heading { min-width: 0; flex: 1; }
.result-heading h2 { margin: 3px 0 3px; font-size: calc(22px * var(--type-scale)); font-weight: 800; letter-spacing: -0.03em; }
.result-heading p { margin: 0; font-size: calc(11.5px * var(--type-scale)); color: var(--muted); line-height: 1.5; }
.result-heading p strong { color: var(--text); }
.close-btn { position: absolute; top: 14px; right: 14px; }

.result-body {
  padding: 0 22px 8px;
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.board-row { display: flex; align-items: center; gap: 10px; }
.row-label { font-size: calc(9px * var(--type-scale)); letter-spacing: 0.12em; text-transform: uppercase; color: var(--faint); }
.board-cards { display: flex; gap: 4px; }

.players-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 9px;
}

.result-player {
  border: 1px solid var(--line);
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.022);
  padding: 10px;
  display: flex;
  flex-direction: column;
  gap: 7px;
  min-width: 0;
}

.result-player.winner {
  border-color: rgba(232, 195, 122, 0.34);
  background: linear-gradient(160deg, rgba(232, 195, 122, 0.08), rgba(255, 255, 255, 0.018));
}

.result-player.folded { opacity: 0.6; }

.player-head { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.player-name { font-size: calc(11.5px * var(--type-scale)); font-weight: 750; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 130px; }

.winner-chip {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 1px 5px;
  border-radius: 4px;
  border: 1px solid rgba(232, 195, 122, 0.3);
  color: var(--gold);
  font-size: calc(8px * var(--type-scale));
  font-weight: 700;
}

.player-net { margin-left: auto; font-size: calc(11px * var(--type-scale)); font-weight: 700; }
.player-net.up { color: var(--emerald); }
.player-net.down { color: var(--red); }
.player-net.flat { color: var(--muted); }

.showdown-row { display: flex; flex-direction: column; gap: 5px; }
.category-chip {
  align-self: flex-start;
  padding: 2px 7px;
  border-radius: 5px;
  border: 1px solid rgba(232, 195, 122, 0.24);
  background: var(--gold-dim);
  color: var(--gold);
  font-size: calc(9px * var(--type-scale));
  font-weight: 700;
}

.best-five { display: flex; gap: 3px; flex-wrap: wrap; }
.fold-note { font-size: calc(9.5px * var(--type-scale)); color: var(--faint); padding: 6px 0; }

.player-foot {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  padding-top: 5px;
  border-top: 1px solid var(--line);
  font-size: calc(8.5px * var(--type-scale));
  color: var(--faint);
}

.player-foot strong { color: var(--text-soft); font-weight: 600; }

.match-final {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 9px 11px;
  border-radius: 9px;
  border: 1px dashed rgba(232, 195, 122, 0.24);
  background: rgba(232, 195, 122, 0.04);
  flex-wrap: wrap;
}

.match-final-label { font-size: calc(9px * var(--type-scale)); letter-spacing: 0.1em; text-transform: uppercase; color: var(--faint); }
.final-stacks { display: flex; gap: 16px; }
.final-stack { display: inline-flex; align-items: center; gap: 5px; font-size: calc(10px * var(--type-scale)); }
.final-stack strong { color: var(--gold); }

.result-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 14px 22px 18px;
  border-top: 1px solid var(--line);
}

.foot-actions { display: flex; gap: 8px; }

@media (max-width: 640px) {
  .result-head { padding: 18px 14px 10px; }
  .result-body { padding: 0 14px 8px; }
  .result-foot { padding: 12px 14px 16px; }
  .players-grid { grid-template-columns: minmax(0, 1fr); }
  .result-heading h2 { font-size: calc(19px * var(--type-scale)); }
  .best-five { gap: 2px; }
}
</style>
