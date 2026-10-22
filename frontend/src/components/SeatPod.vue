<script setup lang="ts">
import { computed } from "vue";
import AppIcon from "./AppIcon.vue";
import FlipCard from "./FlipCard.vue";
import { iconUrl } from "../api";
import { describeAction } from "../actionMeta";
import { chips, shortName } from "../format";
import type { ActionLog, HandResult, PlayerState, SeatInfo } from "../types";

const props = withDefaults(
  defineProps<{
    player: PlayerState | null;
    seatInfo: SeatInfo | null;
    position: "top" | "bottom";
    active?: boolean;
    godView?: boolean;
    result?: HandResult | null;
    showCards?: boolean;
    dealNonce?: number;
    holeHold?: boolean;
    holdAssessment?: boolean;
    lastAction?: ActionLog | null;
  }>(),
  {
    active: false,
    godView: false,
    result: null,
    showCards: false,
    dealNonce: 0,
    holeHold: false,
    holdAssessment: false,
    lastAction: null,
  },
);

const name = computed(() => props.player?.name || props.seatInfo?.name || "等待入座");
const kind = computed(() => props.player?.kind || props.seatInfo?.kind || "human");
const stack = computed(() => props.player?.chips ?? 0);
const isFolded = computed(() => props.player?.status === "folded");
const isAllIn = computed(() => props.player?.status === "all_in");
const isWinner = computed(() => Boolean(props.result?.winner_seats?.includes(props.player?.seat ?? -1)));
const winnerAmount = computed(() => {
  if (!isWinner.value || !props.result) return 0;
  const key = String(props.player?.seat ?? "");
  return Number(props.result.awarded?.[key] || 0);
});
const cards = computed(() => props.player?.hole_cards || []);
const faceDown = computed(() => props.holeHold || !props.showCards);
const avatarIcon = computed(() => {
  if (kind.value === "llm") return "sparkles";
  if (kind.value === "bot") return "bot";
  return "user";
});
const providerIcon = computed(() => props.seatInfo?.icon || "");
const roleLabel = computed(() => {
  if (kind.value === "llm") return props.seatInfo?.model_name || "LLM Agent";
  if (kind.value === "bot") return props.seatInfo?.agent?.agent_type?.startsWith("project_") ? "Project RL" : "RLCard";
  return "人类玩家";
});
const action = computed(() => props.lastAction || null);
const actionText = computed(() => describeAction(action.value));
const assessment = computed(() => {
  if (props.holdAssessment) return "";
  return props.player?.assessment?.category_name || "";
});

function dealOrder(index: number): number {
  return props.position === "top" ? index * 2 : index * 2 + 1;
}
</script>

<template>
  <div
    class="seat"
    :class="[position, { active, folded: isFolded, winner: isWinner, 'all-in': isAllIn }]"
    :data-seat-pod="player?.seat"
  >
    <div class="seat-cards" v-if="position === 'bottom'">
      <div class="card-wrap" v-if="cards.length || player">
        <FlipCard
          v-for="(card, index) in cards"
          :key="`${dealNonce}-${card.code}`"
          :card="card"
          :face-down="faceDown"
          size="hole"
          :index="index"
          :dim="isFolded"
          :highlight="isWinner && !faceDown"
          :data-deal-card="index"
          :data-deal-order="dealOrder(index)"
        />
        <template v-if="!cards.length">
          <FlipCard
            :key="`${dealNonce}-placeholder-0`"
            :face-down="true"
            size="hole"
            :index="0"
            :data-deal-card="0"
            :data-deal-order="dealOrder(0)"
          />
          <FlipCard
            :key="`${dealNonce}-placeholder-1`"
            :face-down="true"
            size="hole"
            :index="1"
            :data-deal-card="1"
            :data-deal-order="dealOrder(1)"
          />
        </template>
      </div>
      <span v-if="assessment" class="assess">{{ assessment }}</span>
    </div>

    <div class="pod">
      <div class="avatar" :class="[kind, { provider: Boolean(providerIcon) }]">
        <img v-if="providerIcon" :src="iconUrl(providerIcon)" :alt="roleLabel" />
        <AppIcon v-else :name="avatarIcon" :size="17" />
        <span v-if="active" class="active-ring"></span>
      </div>

      <div class="identity">
        <div class="name-row">
          <span class="name">{{ shortName(name, 22) }}</span>
          <span class="kind-tag" :class="kind">{{ kind === "llm" ? "LLM" : kind === "bot" ? "BOT" : "HUMAN" }}</span>
        </div>
        <div class="meta-row">
          <span class="mono stack"><AppIcon name="coins" :size="11" /> {{ chips(stack) }}</span>
          <Transition name="meta-swap" mode="out-in">
            <span
              v-if="actionText"
              :key="`action-${action?.street}-${action?.type}-${action?.amount}`"
              class="pod-action"
              :class="action?.type"
            >
              <span class="action-dot"></span>
              <span class="action-text">{{ actionText }}</span>
              <span v-if="action?.auto" class="auto-tag">AUTO</span>
            </span>
            <span v-else key="role" class="role">{{ roleLabel }}</span>
          </Transition>
        </div>
      </div>

      <div class="badges">
        <span v-if="player?.is_button" class="badge dealer" title="庄家 / 小盲">D</span>
        <span v-if="player?.is_small_blind" class="badge blind">SB</span>
        <span v-if="player?.is_big_blind" class="badge blind">BB</span>
        <span v-if="isAllIn" class="badge allin">ALL-IN</span>
        <span v-if="isFolded" class="badge folded">已弃牌</span>
      </div>

      <div v-if="active" class="turn-flag">
        <span class="dot live"></span>
        <span>{{ kind === "human" ? "你的回合" : "思考中" }}</span>
      </div>
    </div>

    <div class="seat-cards" v-if="position === 'top'">
      <div class="card-wrap" v-if="cards.length || player">
        <FlipCard
          v-for="(card, index) in cards"
          :key="`${dealNonce}-${card.code}`"
          :card="card"
          :face-down="faceDown"
          size="hole"
          :index="index"
          :dim="isFolded"
          :highlight="isWinner && !faceDown"
          :data-deal-card="index"
          :data-deal-order="dealOrder(index)"
        />
        <template v-if="!cards.length">
          <FlipCard
            :key="`${dealNonce}-placeholder-0`"
            :face-down="true"
            size="hole"
            :index="0"
            :data-deal-card="0"
            :data-deal-order="dealOrder(0)"
          />
          <FlipCard
            :key="`${dealNonce}-placeholder-1`"
            :face-down="true"
            size="hole"
            :index="1"
            :data-deal-card="1"
            :data-deal-order="dealOrder(1)"
          />
        </template>
      </div>
      <span v-if="assessment" class="assess">{{ assessment }}</span>
    </div>

    <div v-if="isWinner && result" class="winner-badge">
      <AppIcon name="sparkles" :size="12" />
      赢得 {{ chips(winnerAmount) }}
    </div>
  </div>
</template>

<style scoped>
.seat {
  position: absolute;
  z-index: 4;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  width: min(340px, 46%);
  transition: opacity 0.2s ease;
}

.seat.top { top: 3%; left: 50%; transform: translateX(-50%); }
.seat.bottom { bottom: 3%; left: 50%; transform: translateX(-50%); flex-direction: column; }

.seat.folded { opacity: 0.48; }

.seat-cards {
  display: flex;
  align-items: center;
  gap: 9px;
  min-height: 20px;
}

.card-wrap {
  display: flex;
  gap: 5px;
  filter: drop-shadow(0 12px 20px rgba(0, 0, 0, 0.3));
}

.seat.top .card-wrap { margin-top: -2px; }
.seat.bottom .card-wrap { margin-bottom: -2px; }

.assess {
  padding: 3px 8px;
  border-radius: 999px;
  border: 1px solid rgba(232, 195, 122, 0.24);
  background: rgba(6, 14, 11, 0.65);
  color: var(--gold);
  font-size: calc(10px * var(--type-scale));
  font-weight: 700;
  white-space: nowrap;
  backdrop-filter: blur(8px);
}

.pod {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 9px 11px;
  border-radius: 15px;
  border: 1px solid var(--line-strong);
  background: linear-gradient(160deg, rgba(23, 42, 34, 0.92), rgba(9, 21, 16, 0.94));
  box-shadow: 0 16px 36px rgba(0, 0, 0, 0.32);
  backdrop-filter: blur(14px);
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
}

.seat.active .pod {
  border-color: rgba(232, 195, 122, 0.52);
  box-shadow:
    0 0 0 1px rgba(232, 195, 122, 0.16),
    0 0 34px rgba(232, 195, 122, 0.12),
    0 18px 40px rgba(0, 0, 0, 0.38);
}

.seat.winner .pod {
  border-color: rgba(232, 195, 122, 0.72);
  box-shadow:
    0 0 0 1px rgba(232, 195, 122, 0.28),
    0 0 42px rgba(232, 195, 122, 0.2),
    0 18px 40px rgba(0, 0, 0, 0.38);
}

.avatar {
  position: relative;
  width: 38px;
  height: 38px;
  flex: none;
  display: grid;
  place-items: center;
  border-radius: 11px;
  border: 1px solid var(--line-strong);
  background: rgba(255, 255, 255, 0.045);
  color: var(--text-soft);
}

.avatar.llm { background: rgba(232, 195, 122, 0.08); border-color: rgba(232, 195, 122, 0.24); }
.avatar.bot { background: rgba(121, 169, 255, 0.08); border-color: rgba(121, 169, 255, 0.24); }

.avatar.provider {
  background: #f1ecdf;
  border-color: rgba(255, 255, 255, 0.2);
  padding: 3px;
}

.avatar img {
  width: 24px;
  height: 24px;
  object-fit: contain;
}

.active-ring {
  position: absolute;
  inset: -4px;
  border-radius: 14px;
  border: 1px solid rgba(232, 195, 122, 0.55);
  animation: ring-pulse 1.9s ease-in-out infinite;
}

@keyframes ring-pulse {
  0%, 100% { opacity: 0.35; transform: scale(0.98); }
  50% { opacity: 0.95; transform: scale(1.02); }
}

.identity {
  min-width: 0;
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.name-row {
  display: flex;
  align-items: center;
  gap: 7px;
  min-width: 0;
}

.name {
  font-size: calc(12.5px * var(--type-scale));
  font-weight: 750;
  color: var(--text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.kind-tag {
  flex: none;
  font-family: var(--font-mono);
  font-size: calc(8px * var(--type-scale));
  font-weight: 700;
  letter-spacing: 0.1em;
  padding: 1px 4px;
  border-radius: 4px;
  color: var(--muted);
  border: 1px solid var(--line);
}

.kind-tag.llm { color: var(--gold); border-color: rgba(232, 195, 122, 0.26); }
.kind-tag.bot { color: var(--blue); border-color: rgba(121, 169, 255, 0.26); }

.meta-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.stack {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: calc(11.5px * var(--type-scale));
  font-weight: 700;
  color: var(--gold-bright);
}

.role {
  flex: 1;
  min-width: 0;
  font-size: calc(9.5px * var(--type-scale));
  color: var(--faint);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.pod-action {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: calc(10px * var(--type-scale));
  font-weight: 700;
  white-space: nowrap;
}

.action-dot {
  width: 5px;
  height: 5px;
  flex: none;
  border-radius: 50%;
  background: currentColor;
  box-shadow: 0 0 7px currentColor;
}

.action-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pod-action.raise, .pod-action.bet { color: var(--blue); }
.pod-action.call { color: var(--emerald); }
.pod-action.fold { color: var(--red); }
.pod-action.all_in { color: var(--purple); }
.pod-action.check { color: var(--muted); }

.meta-swap-enter-active, .meta-swap-leave-active {
  transition: opacity 0.16s ease, transform 0.16s ease;
}

.meta-swap-enter-from { opacity: 0; transform: translateY(3px); }
.meta-swap-leave-to { opacity: 0; transform: translateY(-3px); }

.badges {
  display: flex;
  align-items: center;
  gap: 4px;
  flex: none;
}

.badge {
  display: inline-grid;
  place-items: center;
  min-width: 18px;
  height: 18px;
  padding: 0 4px;
  border-radius: 5px;
  font-family: var(--font-mono);
  font-size: calc(8px * var(--type-scale));
  font-weight: 800;
  letter-spacing: 0.04em;
}

.badge.dealer {
  background: linear-gradient(145deg, #f2d28f, #c99a45);
  color: #241a08;
  border-radius: 50%;
  width: 19px;
  height: 19px;
  padding: 0;
}

.badge.blind { color: var(--muted); border: 1px solid var(--line); }
.badge.allin { color: var(--purple); border: 1px solid rgba(196, 140, 255, 0.4); background: var(--purple-dim); }
.badge.folded { color: var(--red); border: 1px solid rgba(226, 112, 95, 0.3); background: var(--red-dim); }

.turn-flag {
  position: absolute;
  right: 10px;
  top: -9px;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 7px;
  border-radius: 999px;
  border: 1px solid rgba(232, 195, 122, 0.28);
  background: #10231b;
  color: var(--gold);
  font-size: calc(9px * var(--type-scale));
  font-weight: 700;
  letter-spacing: 0.03em;
  box-shadow: 0 6px 16px rgba(0, 0, 0, 0.3);
}

.auto-tag {
  flex: none;
  font-family: var(--font-mono);
  font-size: calc(7px * var(--type-scale));
  color: var(--amber);
  border: 1px solid rgba(231, 179, 94, 0.3);
  border-radius: 3px;
  padding: 0 3px;
}

.winner-badge {
  position: absolute;
  left: 50%;
  transform: translateX(-50%);
  bottom: -30px;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 10px;
  border-radius: 999px;
  background: linear-gradient(135deg, rgba(232, 195, 122, 0.2), rgba(232, 195, 122, 0.08));
  border: 1px solid rgba(232, 195, 122, 0.38);
  color: var(--gold-bright);
  font-size: calc(10.5px * var(--type-scale));
  font-weight: 800;
  white-space: nowrap;
  box-shadow: 0 8px 20px rgba(0, 0, 0, 0.28);
}

.seat.bottom .winner-badge { bottom: auto; top: -30px; }

@media (max-width: 720px) {
  .seat { width: min(280px, 60%); }
  .pod { padding: 7px 9px; gap: 8px; }
  .avatar { width: 32px; height: 32px; }
  .name { font-size: calc(11.5px * var(--type-scale)); }
  .role { display: none; }
  .seat.top { top: 1%; }
  .seat.bottom { bottom: 1%; }
}
</style>
