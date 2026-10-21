<script setup lang="ts">
import { computed } from "vue";
import type { CardInfo } from "../types";

const props = withDefaults(
  defineProps<{
    card?: CardInfo | null;
    faceDown?: boolean;
    size?: "community" | "hole" | "mini" | "slot";
    index?: number;
    animate?: boolean;
    dim?: boolean;
    highlight?: boolean;
  }>(),
  {
    card: null,
    faceDown: false,
    size: "community",
    index: 0,
    animate: false,
    dim: false,
    highlight: false,
  },
);

const displayRank = computed(() => {
  if (!props.card) return "";
  return props.card.rank === "T" ? "10" : props.card.rank;
});

const label = computed(() => {
  if (props.faceDown) return "暗牌";
  if (!props.card) return "空牌位";
  return `${displayRank.value}${props.card.symbol}`;
});
</script>

<template>
  <div
    class="pcard"
    :class="[
      `size-${size}`,
      {
        red: card?.red,
        'face-down': faceDown,
        empty: !card && !faceDown,
        animate,
        dim,
        highlight,
      },
    ]"
    :style="{ '--card-index': index }"
    :aria-label="label"
    role="img"
  >
    <template v-if="card && !faceDown">
      <div class="corner tl">
        <span class="rank">{{ displayRank }}</span>
        <span class="suit">{{ card.symbol }}</span>
      </div>
      <div class="center-suit">{{ card.symbol }}</div>
      <div class="corner br">
        <span class="rank">{{ displayRank }}</span>
        <span class="suit">{{ card.symbol }}</span>
      </div>
      <div class="card-sheen"></div>
    </template>
    <template v-else-if="faceDown">
      <div class="back">
        <div class="back-lines"></div>
        <div class="back-mark">♠</div>
        <div class="back-ring"></div>
      </div>
    </template>
    <template v-else>
      <div class="empty-mark">·</div>
    </template>
  </div>
</template>

<style scoped>
.pcard {
  --w: 64px;
  --h: 90px;
  position: relative;
  width: var(--w);
  height: var(--h);
  flex: none;
  border-radius: calc(var(--w) * 0.095);
  background: linear-gradient(155deg, #fffdf6 0%, #f5efdf 55%, #e9e0cb 100%);
  color: #1b201d;
  box-shadow:
    0 10px 22px rgba(0, 0, 0, 0.32),
    0 1px 0 rgba(255, 255, 255, 0.7) inset,
    0 0 0 1px rgba(20, 28, 24, 0.18);
  overflow: hidden;
  user-select: none;
  transition: transform 0.18s ease, box-shadow 0.18s ease, opacity 0.18s ease;
  transform-style: preserve-3d;
}

.pcard.size-community { --w: 64px; --h: 90px; }
.pcard.size-hole { --w: 56px; --h: 79px; }
.pcard.size-mini { --w: 30px; --h: 42px; border-radius: 4px; }
.pcard.size-slot { --w: 64px; --h: 90px; }

.pcard.red { color: #b33a36; }
.pcard.red .center-suit { color: rgba(179, 58, 54, 0.19); }

.pcard.animate {
  animation: card-deal 0.42s cubic-bezier(0.16, 0.9, 0.3, 1) both;
  animation-delay: calc(var(--card-index, 0) * 55ms);
}

@keyframes card-deal {
  from { opacity: 0; transform: translateY(-14px) rotate(-4deg) scale(0.94); }
  to { opacity: 1; transform: none; }
}

.pcard.face-down {
  background: linear-gradient(155deg, #7d2626 0%, #521616 55%, #3a0f10 100%);
  box-shadow:
    0 10px 22px rgba(0, 0, 0, 0.36),
    0 0 0 1px rgba(255, 255, 255, 0.08) inset,
    0 0 0 1px rgba(0, 0, 0, 0.4);
}

.pcard.empty {
  background: rgba(255, 255, 255, 0.026);
  border: 1px dashed rgba(232, 226, 210, 0.24);
  box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.015) inset;
}

.pcard.dim { opacity: 0.38; filter: grayscale(0.6); }
.pcard.highlight {
  box-shadow:
    0 0 0 2px rgba(232, 195, 122, 0.85),
    0 0 26px rgba(232, 195, 122, 0.28),
    0 10px 22px rgba(0, 0, 0, 0.32);
}

.corner {
  position: absolute;
  display: flex;
  flex-direction: column;
  align-items: center;
  line-height: 0.92;
  font-weight: 750;
  letter-spacing: -0.04em;
}

.corner.tl { top: 7px; left: 7px; }
.corner.br { bottom: 7px; right: 7px; transform: rotate(180deg); }

.corner .rank { font-size: calc(var(--w) * 0.28); }
.corner .suit { font-size: calc(var(--w) * 0.19); margin-top: 1px; }

.center-suit {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  font-size: calc(var(--w) * 0.66);
  opacity: 0.95;
  color: rgba(27, 32, 29, 0.16);
  pointer-events: none;
}

.card-sheen {
  position: absolute;
  inset: 0;
  background: linear-gradient(120deg, rgba(255, 255, 255, 0.5) 0%, transparent 38%, transparent 72%, rgba(255, 255, 255, 0.22) 100%);
  pointer-events: none;
}

.back {
  position: absolute;
  inset: 3px;
  border-radius: calc(var(--w) * 0.07);
  border: 1px solid rgba(232, 195, 122, 0.3);
  overflow: hidden;
  display: grid;
  place-items: center;
}

.back-lines {
  position: absolute;
  inset: -20%;
  opacity: 0.5;
  background-image:
    repeating-linear-gradient(45deg, rgba(232, 195, 122, 0.22) 0, rgba(232, 195, 122, 0.22) 1px, transparent 1px, transparent 7px),
    repeating-linear-gradient(-45deg, rgba(232, 195, 122, 0.16) 0, rgba(232, 195, 122, 0.16) 1px, transparent 1px, transparent 7px);
}

.back-mark {
  position: relative;
  z-index: 1;
  color: rgba(245, 218, 160, 0.86);
  font-size: calc(var(--w) * 0.38);
  text-shadow: 0 2px 8px rgba(0, 0, 0, 0.4);
}

.back-ring {
  position: absolute;
  width: 62%;
  height: 62%;
  border-radius: 50%;
  border: 1px solid rgba(232, 195, 122, 0.28);
}

.empty-mark {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  color: rgba(232, 226, 210, 0.2);
  font-size: calc(22px * var(--type-scale));
}

.size-mini .corner.tl { top: 3px; left: 3px; }
.size-mini .corner.br { bottom: 3px; right: 3px; }
.size-mini .corner .rank { font-size: calc(10px * var(--type-scale)); }
.size-mini .corner .suit { font-size: calc(7px * var(--type-scale)); }
.size-mini .center-suit { font-size: calc(16px * var(--type-scale)); }
.size-mini .back-mark { font-size: calc(12px * var(--type-scale)); }

@media (max-width: 720px) {
  .pcard.size-community { --w: 48px; --h: 68px; }
  .pcard.size-slot { --w: 48px; --h: 68px; }
  .pcard.size-hole { --w: 46px; --h: 65px; }
}
</style>
