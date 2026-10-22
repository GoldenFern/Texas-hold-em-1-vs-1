<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import PlayingCard from "./PlayingCard.vue";
import { prefersReducedMotion } from "../boardReveal";
import type { CardInfo } from "../types";

const props = withDefaults(
  defineProps<{
    card?: CardInfo | null;
    faceDown?: boolean;
    size?: "community" | "hole" | "mini" | "slot";
    index?: number;
    dim?: boolean;
    highlight?: boolean;
    autoReveal?: boolean;
    flipDelay?: number;
    instant?: boolean;
  }>(),
  {
    card: null,
    faceDown: false,
    size: "community",
    index: 0,
    dim: false,
    highlight: false,
    autoReveal: false,
    flipDelay: 50,
    instant: false,
  },
);

const revealed = ref(true);
let timer: ReturnType<typeof setTimeout> | null = null;

function clearTimer(): void {
  if (timer !== null) {
    clearTimeout(timer);
    timer = null;
  }
}

function scheduleReveal(): void {
  clearTimer();
  if (!props.card) {
    revealed.value = false;
    return;
  }
  if (props.instant || prefersReducedMotion()) {
    revealed.value = true;
    return;
  }
  if (!props.autoReveal) {
    revealed.value = !props.faceDown;
    return;
  }
  revealed.value = false;
  timer = setTimeout(() => {
    timer = null;
    revealed.value = true;
  }, Math.max(0, props.flipDelay));
}

watch(() => props.card?.code, scheduleReveal, { immediate: true });

watch(
  () => props.faceDown,
  (down) => {
    if (props.autoReveal) return;
    clearTimer();
    revealed.value = !down;
  },
);

onBeforeUnmount(clearTimer);

const showInner = computed(() => Boolean(props.card || props.faceDown));
</script>

<template>
  <div
    class="flip-card"
    :class="{
      revealed,
      instant,
      'auto-reveal': autoReveal && !instant,
    }"
    :style="{ '--card-index': index }"
  >
    <div v-if="showInner" class="flip-inner">
      <div class="flip-face back" :aria-hidden="revealed">
        <PlayingCard :face-down="true" :size="size" :index="index" :dim="dim" />
      </div>
      <div class="flip-face front" :aria-hidden="!revealed">
        <PlayingCard
          v-if="card"
          :card="card"
          :size="size"
          :index="index"
          :dim="dim"
          :highlight="highlight && !faceDown"
        />
      </div>
    </div>
    <PlayingCard v-else :size="size" :index="index" :dim="dim" />
  </div>
</template>

<style scoped>
.flip-card {
  position: relative;
  display: grid;
  flex: none;
  perspective: 900px;
}

.flip-card.auto-reveal {
  animation: card-settle 0.38s cubic-bezier(0.16, 0.9, 0.3, 1) both;
}

.flip-inner {
  display: grid;
  transform-style: preserve-3d;
  transition: transform 0.48s cubic-bezier(0.2, 0.8, 0.2, 1);
}

.flip-card.revealed .flip-inner {
  transform: rotateY(180deg);
}

.flip-card.instant .flip-inner {
  transition: none;
}

.flip-face {
  grid-area: 1 / 1;
  backface-visibility: hidden;
  -webkit-backface-visibility: hidden;
}

.flip-face.front {
  transform: rotateY(180deg);
}

@keyframes card-settle {
  from {
    transform: translateY(5px) scale(0.97);
    filter: brightness(0.92);
  }
  to {
    transform: none;
    filter: none;
  }
}
</style>
