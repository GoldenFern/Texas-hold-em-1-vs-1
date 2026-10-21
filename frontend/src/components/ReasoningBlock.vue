<script setup lang="ts">
import { computed, ref, watch } from "vue";
import AppIcon from "./AppIcon.vue";

const props = withDefaults(
  defineProps<{
    text: string;
    streaming?: boolean;
    collapse?: boolean;
  }>(),
  { streaming: false, collapse: false },
);

const open = ref(!props.collapse);
const userToggled = ref(false);
const charCount = computed(() => props.text.length);

watch(
  () => props.collapse,
  (collapse) => {
    if (userToggled.value) return;
    open.value = !collapse;
  },
  { immediate: true },
);

watch(
  () => props.streaming,
  (streaming) => {
    if (streaming && !userToggled.value) open.value = true;
  },
);

function toggle(): void {
  open.value = !open.value;
  userToggled.value = true;
}
</script>

<template>
  <div class="reasoning" :class="{ open, streaming }">
    <button class="reasoning-head" type="button" :aria-expanded="open" @click="toggle">
      <span class="reasoning-icon"><AppIcon name="sparkles" :size="13" /></span>
      <span class="reasoning-title">思考</span>
      <span v-if="streaming" class="live-pill"><span class="dot live"></span>LIVE</span>
      <span class="reasoning-meta mono">{{ charCount }} chars</span>
      <span class="chevron" :class="{ open }"><AppIcon name="chevronDown" :size="13" /></span>
    </button>
    <div v-show="open" class="reasoning-body">
      <div class="reasoning-text">{{ text }}<span v-if="streaming" class="stream-caret"></span></div>
    </div>
  </div>
</template>

<style scoped>
.reasoning {
  border-left: 2px dashed rgba(232, 195, 122, 0.22);
  border-radius: 0 9px 9px 0;
  background: linear-gradient(90deg, rgba(232, 195, 122, 0.035), transparent 80%);
  margin: 7px 0 9px;
  transition: border-color 0.2s ease;
}

.reasoning.streaming { border-left-color: rgba(232, 195, 122, 0.5); }

.reasoning-head {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 6px 9px;
  text-align: left;
  color: var(--muted);
}

.reasoning-icon { color: var(--gold); display: flex; opacity: 0.8; }
.reasoning.streaming .reasoning-icon { animation: sparkle-pulse 1.4s ease-in-out infinite; }

@keyframes sparkle-pulse {
  0%, 100% { opacity: 0.4; transform: scale(0.92); }
  50% { opacity: 1; transform: scale(1.05); }
}

.reasoning-title {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.04em;
  color: var(--muted);
}

.live-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-family: var(--font-mono);
  font-size: 7px;
  font-weight: 800;
  letter-spacing: 0.12em;
  color: var(--emerald);
  border: 1px solid rgba(84, 214, 168, 0.25);
  border-radius: 4px;
  padding: 0 4px;
}

.reasoning-meta {
  margin-left: auto;
  font-size: 9px;
  color: var(--faint);
}

.chevron {
  display: flex;
  color: var(--faint);
  transition: transform 0.16s ease;
}

.chevron.open { transform: rotate(180deg); }

.reasoning-body {
  padding: 0 10px 8px 28px;
  position: relative;
}

.reasoning-text {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: 11.5px;
  line-height: 1.65;
  color: #a9b8af;
  font-weight: 450;
  letter-spacing: 0.005em;
}

.stream-caret {
  display: inline-block;
  width: 6px;
  height: 12px;
  margin-left: 2px;
  background: var(--gold);
  vertical-align: -1px;
  animation: caret-blink 1s steps(2, start) infinite;
}

@keyframes caret-blink {
  0%, 50% { opacity: 1; }
  50.01%, 100% { opacity: 0; }
}
</style>
