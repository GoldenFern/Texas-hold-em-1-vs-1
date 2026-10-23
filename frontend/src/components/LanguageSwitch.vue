<script setup lang="ts">
import { setLocale, t, locale } from "../i18n";

const options: Array<{ value: "zh-CN" | "en-US"; label: string }> = [
  { value: "zh-CN", label: "中" },
  { value: "en-US", label: "EN" },
];
</script>

<template>
  <div class="lang-switch" role="group" :aria-label="t('topbar.language')">
    <button
      v-for="option in options"
      :key="option.value"
      type="button"
      :class="{ active: locale === option.value }"
      :aria-pressed="locale === option.value"
      :title="option.value === 'zh-CN' ? t('topbar.switchToChinese') : t('topbar.switchToEnglish')"
      @click="setLocale(option.value)"
    >
      {{ option.label }}
    </button>
  </div>
</template>

<style scoped>
.lang-switch {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 2px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.035);
  flex: none;
}

.lang-switch button {
  min-width: 26px;
  height: 23px;
  padding: 0 6px;
  border-radius: 6px;
  color: var(--faint);
  font-family: var(--font-mono);
  font-size: calc(9px * var(--type-scale));
  font-weight: 800;
  letter-spacing: 0.04em;
  line-height: 1;
  transition: color 0.16s ease, background 0.16s ease;
}

.lang-switch button:hover { color: var(--text-soft); }

.lang-switch button.active {
  color: #241a08;
  background: var(--gold);
}

@media (max-width: 620px) {
  .lang-switch button {
    min-width: 22px;
    padding: 0 4px;
  }
}
</style>
