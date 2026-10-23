<script setup lang="ts">
import { computed } from "vue";
import AppIcon from "./AppIcon.vue";
import PlayingCard from "./PlayingCard.vue";
import { t } from "../i18n";
import type { CardInfo } from "../types";

const props = withDefaults(
  defineProps<{
    remaining: CardInfo[];
    burned: CardInfo[];
    count: number;
    dealing?: boolean;
  }>(),
  { remaining: () => [], burned: () => [], count: 0, dealing: false },
);

const shown = computed(() => props.remaining.slice(0, 10));
const extra = computed(() => Math.max(0, props.count - shown.value.length));
</script>

<template>
  <div class="deck-strip">
    <div class="deck-head">
      <div class="deck-title">
        <AppIcon name="cards" :size="14" />
        <span>{{ t("table.deck") }}</span>
        <span v-if="dealing" class="mono deck-count">{{ t("table.deckDealing") }}</span>
        <span v-else class="mono deck-count">{{ t("table.deckCount", { count }) }}</span>
      </div>
      <span v-if="dealing" class="deck-hint">{{ t("table.deckHintDealing") }}</span>
      <span v-else class="deck-hint">{{ t("table.deckHint") }}</span>
    </div>

    <div v-if="dealing" class="deck-placeholder">
      <span>{{ t("table.deckShuffling") }}</span>
      <span class="mono">{{ t("table.deckOrder") }}</span>
    </div>

    <div v-else class="deck-body">
      <div class="deck-row">
        <div v-for="(card, index) in shown" :key="`${card.code}-${index}`" class="deck-card" :class="{ next: index === 0 }">
          <PlayingCard :card="card" size="mini" :index="index" />
          <span v-if="index === 0" class="next-tag">NEXT</span>
        </div>
        <div v-if="extra" class="deck-more mono">+{{ extra }}</div>
        <div v-if="!shown.length" class="deck-empty">{{ t("table.deckEmpty") }}</div>
      </div>

      <div v-if="burned.length" class="burn-row">
        <span class="burn-label">{{ t("table.burn") }}</span>
        <div class="burn-cards">
          <PlayingCard v-for="(card, index) in burned" :key="card.code" :card="card" size="mini" :index="index" dim />
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.deck-strip {
  border-top: 1px solid var(--line);
  padding: 10px 14px 11px;
  background: rgba(5, 13, 10, 0.28);
}

.deck-head {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 20px;
}

.deck-title {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: calc(10.5px * var(--type-scale));
  font-weight: 700;
  letter-spacing: 0.08em;
  color: var(--muted);
  text-transform: uppercase;
}

.deck-count {
  color: var(--gold);
  font-size: calc(10px * var(--type-scale));
  letter-spacing: 0;
}

.deck-hint {
  margin-left: auto;
  font-size: calc(9.5px * var(--type-scale));
  color: var(--faint);
}

.deck-body {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-top: 8px;
  min-width: 0;
}

.deck-row {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
  overflow-x: auto;
  padding-bottom: 2px;
}

.deck-card {
  position: relative;
  flex: none;
  opacity: 0.72;
  transition: opacity 0.18s ease;
}

.deck-card.next { opacity: 1; }

.deck-card.next :deep(.pcard) {
  box-shadow:
    0 0 0 1px rgba(232, 195, 122, 0.65),
    0 0 16px rgba(232, 195, 122, 0.18),
    0 6px 14px rgba(0, 0, 0, 0.3);
}

.next-tag {
  position: absolute;
  left: 50%;
  bottom: -7px;
  transform: translateX(-50%);
  font-family: var(--font-mono);
  font-size: calc(6px * var(--type-scale));
  letter-spacing: 0.1em;
  color: #241a08;
  background: var(--gold);
  border-radius: 3px;
  padding: 0 3px;
  line-height: 1.4;
  white-space: nowrap;
}

.deck-more {
  flex: none;
  display: grid;
  place-items: center;
  width: 34px;
  height: 42px;
  border-radius: 5px;
  border: 1px dashed var(--line-strong);
  color: var(--muted);
  font-size: calc(10px * var(--type-scale));
}

.deck-empty { font-size: calc(10.5px * var(--type-scale)); color: var(--faint); }

.burn-row {
  display: flex;
  align-items: center;
  gap: 7px;
  flex: none;
  padding-left: 10px;
  border-left: 1px solid var(--line);
}

.burn-label {
  font-size: calc(9px * var(--type-scale));
  color: var(--faint);
  letter-spacing: 0.08em;
  writing-mode: vertical-rl;
}

.burn-cards { display: flex; gap: 3px; }

.deck-placeholder {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-top: 8px;
  padding: 8px 10px;
  border: 1px dashed var(--line);
  border-radius: 8px;
  font-size: calc(10px * var(--type-scale));
  color: var(--faint);
  letter-spacing: 0.03em;
}

@media (max-width: 720px) {
  .deck-hint { display: none; }
  .burn-row { display: none; }
  .deck-strip { padding: 8px 10px; }
}
</style>
