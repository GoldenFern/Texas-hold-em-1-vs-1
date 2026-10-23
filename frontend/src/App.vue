<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import AgentConsole from "./components/AgentConsole.vue";
import AnalysisPanel from "./components/AnalysisPanel.vue";
import AppIcon from "./components/AppIcon.vue";
import SetupModal from "./components/SetupModal.vue";
import TableView from "./components/TableView.vue";
import ToastHost from "./components/ToastHost.vue";
import TopBar from "./components/TopBar.vue";
import { board, boardLocked } from "./boardReveal";
import { t } from "./i18n";
import { boot, game, refreshAnalysis } from "./store";

const analysisTimer = ref(0);

onMounted(async () => {
  await boot();
  if (game.snapshot?.has_match) {
    window.setTimeout(() => void refreshAnalysis(), 220);
  }
});

watch(
  () => [
    game.snapshot?.hand_id,
    board.community.length,
    game.selectedAnalysisSeat,
    game.godView,
    boardLocked.value,
  ],
  () => {
    if (!game.snapshot?.has_match) return;
    window.clearTimeout(analysisTimer.value);
    if (boardLocked.value) return;
    analysisTimer.value = window.setTimeout(() => void refreshAnalysis(), 320);
  },
);

function reload(): void {
  window.location.reload();
}
</script>

<template>
  <div class="app">
    <TopBar />

    <div v-if="game.fatalError" class="global-banner">
      <AppIcon name="alert" :size="14" />
      <span>{{ game.fatalError }}</span>
      <button class="btn btn-sm" @click="reload">{{ t("app.reload") }}</button>
    </div>

    <main class="app-main">
      <div class="stage-column">
        <TableView />
        <AnalysisPanel v-if="game.snapshot?.has_match" />
      </div>
      <div class="console-column">
        <AgentConsole />
      </div>
    </main>

    <SetupModal />
    <ToastHost />

    <div v-if="game.loading" class="loading-cover">
      <div class="loading-mark">♠</div>
      <div class="loading-text">{{ t("app.loading") }}</div>
      <div class="loading-bar"><span></span></div>
    </div>
  </div>
</template>

<style scoped>
.global-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 10px 22px 0;
  padding: 8px 12px;
  border-radius: 9px;
  border: 1px solid rgba(226, 112, 95, 0.28);
  background: var(--red-dim);
  color: #ffb3a8;
  font-size: calc(11px * var(--type-scale));
}

.global-banner .btn { margin-left: auto; }

.loading-cover {
  position: fixed;
  inset: 0;
  z-index: 90;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  background: rgba(4, 11, 8, 0.88);
  backdrop-filter: blur(10px);
}

.loading-mark {
  width: 48px;
  height: 48px;
  display: grid;
  place-items: center;
  border-radius: 14px;
  border: 1px solid rgba(232, 195, 122, 0.35);
  color: var(--gold);
  font-size: calc(24px * var(--type-scale));
  animation: mark-pulse 1.5s ease-in-out infinite;
}

@keyframes mark-pulse {
  0%, 100% { opacity: 0.5; transform: scale(0.96); }
  50% { opacity: 1; transform: scale(1); }
}

.loading-text { font-size: calc(10px * var(--type-scale)); letter-spacing: 0.24em; color: var(--muted); text-transform: uppercase; }

.loading-bar {
  width: 140px;
  height: 2px;
  border-radius: 99px;
  background: rgba(255, 255, 255, 0.08);
  overflow: hidden;
}

.loading-bar span {
  display: block;
  width: 40%;
  height: 100%;
  border-radius: 99px;
  background: var(--gold);
  animation: loading-slide 1.1s ease-in-out infinite;
}

@keyframes loading-slide {
  0% { transform: translateX(-110%); }
  100% { transform: translateX(360%); }
}

@media (max-width: 720px) {
  .global-banner { margin: 8px 12px 0; }
}
</style>
