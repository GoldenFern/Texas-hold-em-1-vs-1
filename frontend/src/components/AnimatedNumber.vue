<script setup lang="ts">
import { onUnmounted, ref, watch } from "vue";
import { chips } from "../format";

const props = withDefaults(defineProps<{ value: number; duration?: number }>(), { duration: 380 });
const display = ref(Number(props.value || 0));
let frame = 0;

function animate(to: number) {
  cancelAnimationFrame(frame);
  const from = display.value;
  if (from === to) return;
  const start = performance.now();
  const duration = Math.max(120, props.duration);
  const tick = (now: number) => {
    const t = Math.min(1, (now - start) / duration);
    const eased = 1 - Math.pow(1 - t, 3);
    display.value = Math.round(from + (to - from) * eased);
    if (t < 1) frame = requestAnimationFrame(tick);
  };
  frame = requestAnimationFrame(tick);
}

watch(
  () => Number(props.value || 0),
  (value) => animate(value),
);

onUnmounted(() => cancelAnimationFrame(frame));
</script>

<template>
  <span class="mono">{{ chips(display) }}</span>
</template>
