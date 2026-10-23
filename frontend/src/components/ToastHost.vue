<script setup lang="ts">
import AppIcon from "./AppIcon.vue";
import { t } from "../i18n";
import { dismissToast, game } from "../store";
</script>

<template>
  <div class="toast-host" aria-live="polite">
    <transition-group name="toast">
      <div v-for="item in game.toasts" :key="item.id" class="toast" :class="item.level" @click="dismissToast(item.id)">
        <div class="toast-bar"></div>
        <div class="toast-content">
          <div class="toast-title">{{ item.title }}</div>
          <div v-if="item.detail" class="toast-detail">{{ item.detail }}</div>
        </div>
        <button class="toast-close" :aria-label="t('common.close')" @click.stop="dismissToast(item.id)">
          <AppIcon name="x" :size="12" />
        </button>
      </div>
    </transition-group>
  </div>
</template>

<style scoped>
.toast-content { flex: 1; min-width: 0; }
.toast-close { color: var(--faint); padding: 2px; display: flex; flex: none; }
.toast-close:hover { color: var(--text); }

.toast-enter-active, .toast-leave-active { transition: opacity 0.2s ease, transform 0.2s ease; }
.toast-enter-from, .toast-leave-to { opacity: 0; transform: translateY(8px) scale(0.98); }
.toast-move { transition: transform 0.2s ease; }
</style>
