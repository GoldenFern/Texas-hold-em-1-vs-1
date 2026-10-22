<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import AppIcon from "./AppIcon.vue";
import AgentTurn from "./AgentTurn.vue";
import PlayingCard from "./PlayingCard.vue";
import { iconUrl } from "../api";
import { describeAction } from "../actionMeta";
import { actionLabel, chips, compactSeatName, shortName, streetShort } from "../format";
import { reasoningDefaultCollapsed, setReasoningDefaultCollapsed } from "../reasoning";
import { agentSeats, followLive, game, humanSeat, openSetup, selectSeat } from "../store";
import type { ActionEntry, BotDecisionView, TurnView } from "../types";

const mode = ref<"stream" | "log">("stream");
const scrollEl = ref<HTMLElement | null>(null);
const stick = ref(true);
const showJump = ref(false);
const anchorTail = ref(0);
let observer: MutationObserver | null = null;
let scrollFrame = 0;
let seenHandId: number | null = null;
let anchorToken = 0;
let anchorGuardUntil = 0;
let followHandId: number | null = null;

const selectedInfo = computed(() => {
  if (game.selectedSeat === null) return null;
  return game.snapshot?.match?.seats.find((seat) => seat.seat === game.selectedSeat) || null;
});

const selectedLabel = computed(() => {
  const info = selectedInfo.value;
  if (!info) return "Agent";
  return info.kind === "llm" ? compactSeatName(info.name, info.model_name) : info.name;
});

const turns = computed<Array<TurnView | BotDecisionView>>(() => {
  if (game.selectedSeat === null) return [];
  return game.turns[game.selectedSeat] || [];
});

function itemHandId(item: TurnView | BotDecisionView): number {
  return "messages" in item ? Number(item.header.handId || 0) : Number(item.handId || 0);
}

const latestHandId = computed<number | null>(() => {
  if (!turns.value.length) return null;
  return itemHandId(turns.value[turns.value.length - 1]);
});

const currentHandId = computed(() => Number(game.snapshot?.hand_id || 0));

// A new hand starts before the selected agent acts. Keep a divider for that
// hand so the stream moves on as soon as the cards are dealt, not when the
// first turn_event arrives.
const pendingHandId = computed<number | null>(() => {
  if (!turns.value.length) return null;
  const latest = latestHandId.value ?? 0;
  if (currentHandId.value <= latest) return null;
  return currentHandId.value;
});

const streamHandId = computed<number | null>(() => {
  const latest = latestHandId.value ?? 0;
  return Math.max(latest, pendingHandId.value ?? 0) || null;
});

function handDividerLabel(handId: number): string {
  return handId > 0 ? `HAND #${handId}` : "HAND";
}

const anyLive = computed(() => {
  for (const seat of agentSeats.value) {
    if (seatLive(seat.seat)) return true;
  }
  return false;
});

const groups = computed(() => {
  const map = new Map<number, ActionEntry[]>();
  for (const action of game.actionFeed) {
    if (!map.has(action.handId)) map.set(action.handId, []);
    map.get(action.handId)!.push(action);
  }
  return [...map.entries()]
    .sort((a, b) => b[0] - a[0])
    .slice(0, 6)
    .map(([handId, actions]) => ({
      handId,
      actions: actions.slice(-80),
      result: game.snapshot?.match?.history?.find((item) => item.hand_id === handId)
        || (game.lastResult?.hand_id === handId ? game.lastResult : null),
    }));
});

function seatLive(seat: number): boolean {
  const list = game.turns[seat] || [];
  const last = list[list.length - 1];
  if (last && "status" in last && last.status === "running") return true;
  return Boolean(
    game.snapshot?.acting_seat === seat &&
      game.snapshot?.street !== "finished" &&
      game.snapshot?.match?.seats.find((item) => item.seat === seat)?.kind !== "human",
  );
}

function tabIcon(seat: (typeof agentSeats.value)[number]) {
  return seat.icon || "";
}

function seatName(seat: number): string {
  return game.snapshot?.players?.find((player) => player.seat === seat)?.name
    || game.snapshot?.match?.seats.find((item) => item.seat === seat)?.name
    || `Seat ${seat}`;
}

function resultLine(group: (typeof groups.value)[number]): string {
  if (!group.result) return "进行中";
  const winners = (group.result.winner_seats || []).map((seat) => seatName(seat)).join(" / ");
  return `${winners || "—"} 赢得 ${chips(group.result.pot || 0)}`;
}

function clickSeat(seat: number): void {
  clearAnchor();
  mode.value = "stream";
  selectSeat(seat, true);
  stick.value = true;
  nextTick(scrollToBottom);
}

function onScroll(): void {
  const el = scrollEl.value;
  if (!el) return;
  if (performance.now() < anchorGuardUntil) return;
  const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
  if (!atBottom) {
    followHandId = null;
  }
  stick.value = atBottom;
  showJump.value = !atBottom && mode.value === "stream";
  if (atBottom && followHandId === null && anchorTail.value > 0) {
    anchorTail.value = 0;
  }
}

function scrollToBottom(): void {
  const el = scrollEl.value;
  if (!el) return;
  el.scrollTop = el.scrollHeight;
  stick.value = true;
  showJump.value = false;
}

function scheduleScroll(): void {
  if (!stick.value) return;
  syncFollowTail();
  cancelAnimationFrame(scrollFrame);
  scrollFrame = requestAnimationFrame(scrollToBottom);
}

function clearAnchor(): void {
  anchorToken += 1;
  anchorTail.value = 0;
  followHandId = null;
}

/**
 * Keep the new hand pinned to the top while it is shorter than the viewport.
 *
 * The spacer below the stream fills the remaining height, so the previous
 * hand stays above the visible area. Once the new hand grows taller than the
 * viewport the spacer becomes zero and normal bottom-follow takes over.
 */
function syncFollowTail(): void {
  const handId = followHandId;
  const el = scrollEl.value;
  if (handId === null || !el) return;
  const target = el.querySelector<HTMLElement>(`[data-hand-id="${handId}"]`);
  if (!target) return;
  const containerRect = el.getBoundingClientRect();
  const targetRect = target.getBoundingClientRect();
  const targetTop = targetRect.top - containerRect.top + el.scrollTop;
  const contentBelow = el.scrollHeight - anchorTail.value - targetTop;
  const nextTail = Math.max(0, Math.ceil(el.clientHeight - contentBelow));
  if (nextTail !== anchorTail.value) {
    anchorTail.value = nextTail;
  }
  if (nextTail === 0) {
    followHandId = null;
  }
}

/**
 * Put the first divider of a fresh hand at the top of the stream.
 *
 * The dynamic tail keeps the previous hand outside the viewport while the new
 * hand is still short, then normal bottom-follow continues as it grows. A
 * manual upward scroll clears followHandId and lets the user read history.
 */
async function anchorNewHand(handId: number): Promise<void> {
  const token = ++anchorToken;
  followHandId = handId;
  anchorGuardUntil = performance.now() + 600;
  stick.value = true;
  showJump.value = false;
  cancelAnimationFrame(scrollFrame);
  await nextTick();
  if (token !== anchorToken) return;
  syncFollowTail();
  await nextTick();
  if (token !== anchorToken) return;
  anchorGuardUntil = performance.now() + 220;
  scrollToBottom();
}

function jumpLatest(): void {
  clearAnchor();
  mode.value = "stream";
  stick.value = true;
  nextTick(scrollToBottom);
}

function setMode(next: "stream" | "log"): void {
  clearAnchor();
  mode.value = next;
  if (next === "log") {
    stick.value = false;
    showJump.value = false;
    nextTick(() => {
      if (scrollEl.value) scrollEl.value.scrollTop = 0;
    });
  } else {
    stick.value = true;
    nextTick(scrollToBottom);
  }
}

watch(
  () => game.selectedSeat,
  () => {
    clearAnchor();
    seenHandId = streamHandId.value;
    stick.value = true;
    nextTick(scrollToBottom);
  },
);

watch(
  () => game.matchId,
  () => {
    clearAnchor();
    seenHandId = null;
    mode.value = agentSeats.value.length ? "stream" : "log";
    stick.value = true;
    nextTick(scrollToBottom);
  },
);

watch(streamHandId, (handId) => {
  if (mode.value !== "stream") {
    seenHandId = handId;
    return;
  }
  if (handId === null) {
    seenHandId = null;
    return;
  }
  if (seenHandId === null) {
    seenHandId = handId;
    return;
  }
  if (handId > seenHandId) {
    seenHandId = handId;
    void anchorNewHand(handId);
    return;
  }
  seenHandId = handId;
});

onMounted(() => {
  const el = scrollEl.value;
  if (el) {
    observer = new MutationObserver(() => {
      scheduleScroll();
    });
    observer.observe(el, { childList: true, subtree: true, characterData: true });
    scrollToBottom();
  }
  mode.value = agentSeats.value.length ? "stream" : "log";
  seenHandId = streamHandId.value;
});

onUnmounted(() => {
  clearAnchor();
  observer?.disconnect();
  cancelAnimationFrame(scrollFrame);
});
</script>

<template>
  <aside class="agent-console">
    <header class="console-head">
      <div class="head-top">
        <div class="head-title">
          <h2>Agent 观测台</h2>
        </div>
        <div v-if="agentSeats.length" class="seat-tabs">
          <button
            v-for="seat in agentSeats"
            :key="seat.seat"
            class="seat-tab"
            :class="{ active: game.selectedSeat === seat.seat && mode === 'stream' }"
            :title="seat.name"
            :aria-label="seat.name"
            @click="clickSeat(seat.seat)"
          >
            <span class="tab-icon logo-tile" :class="{ provider: Boolean(tabIcon(seat)) }">
              <img v-if="tabIcon(seat)" :src="iconUrl(tabIcon(seat))" :alt="seat.name" />
              <AppIcon v-else :name="seat.kind === 'bot' ? 'bot' : 'sparkles'" :size="14" />
            </span>
            <span v-if="agentSeats.length > 1" class="tab-seat mono">{{ seat.seat + 1 }}</span>
            <span v-if="seatLive(seat.seat)" class="dot live"></span>
          </button>
        </div>
        <div class="head-actions">
          <button
            class="btn btn-ghost btn-sm follow-btn"
            :class="{ active: !game.pinned }"
            title="自动跟随当前行动的 Agent"
            @click="followLive"
          >
            <AppIcon name="zap" :size="13" />
            跟随
          </button>
          <div class="segmented mode-switch">
            <button :class="{ active: mode === 'stream' }" @click="setMode('stream')">思考流</button>
            <button :class="{ active: mode === 'log' }" @click="setMode('log')">日志</button>
          </div>
        </div>
      </div>
    </header>

    <div ref="scrollEl" class="console-scroll" @scroll="onScroll">
      <div v-if="mode === 'stream'" class="stream-body">
        <div v-if="!game.snapshot?.has_match" class="empty-state">
          <div class="empty-orb"><AppIcon name="sparkles" :size="28" /></div>
          <h3>还没有开始对局</h3>
          <p>配置两位玩家，LLM 的推理、工具参数和返回结果会逐字出现在这里。</p>
          <button class="btn btn-primary btn-sm" @click="openSetup">
            <AppIcon name="settings" :size="14" />
            配置对局
          </button>
        </div>

        <div v-else-if="!agentSeats.length" class="empty-state">
          <div class="empty-orb"><AppIcon name="user" :size="28" /></div>
          <h3>这是一场人类对局</h3>
          <p>把其中一个座位换成 LLM 或 RLCard，就能在这里看到实时思考与工具调用。</p>
          <button class="btn btn-primary btn-sm" @click="openSetup">打开配置</button>
        </div>

        <div v-else-if="!turns.length" class="empty-state">
          <div class="empty-orb"><AppIcon name="brain" :size="28" /></div>
          <h3>等待 {{ selectedLabel }} 的第一回合</h3>
          <p>轮到它行动时，模型会先阅读牌局，然后调用牌型概率与执行动作工具。</p>
          <div class="empty-flow">
            <span>读取牌局</span>
            <AppIcon name="arrowRight" :size="12" />
            <span>计算牌型</span>
            <AppIcon name="arrowRight" :size="12" />
            <span>提交动作</span>
          </div>
        </div>

        <template v-else>
          <template v-for="(item, index) in turns" :key="item.id">
            <div
              v-if="index === 0 || itemHandId(item) !== itemHandId(turns[index - 1])"
              class="hand-divider"
              role="separator"
              :data-hand-id="itemHandId(item)"
            >
              <span class="hand-divider-label">{{ handDividerLabel(itemHandId(item)) }}</span>
            </div>
            <AgentTurn
              v-if="'messages' in item"
              :turn="item"
              :turn-number="index + 1"
              :seat-name="seatName(game.selectedSeat ?? 0)"
              :live="item.status === 'running' && seatLive(game.selectedSeat ?? 0)"
            />
            <article v-else class="bot-turn">
              <div class="bot-rail"><span class="bot-node"></span></div>
              <div class="bot-card">
                <header class="bot-head">
                  <span class="bot-icon"><AppIcon name="bot" :size="14" /></span>
                  <strong>{{ item.agentType.startsWith("project_") ? "Project RL" : "RLCard" }}</strong>
                  <span class="chip blue">{{ item.agentType }}</span>
                  <span class="mono faint">#{{ item.handId }} · {{ streetShort(item.street) }}</span>
                  <span class="bot-action mono">
                    ID {{ item.actionId }}
                    <template v-if="item.mappedAction">
                      → {{ actionLabel(String(item.mappedAction.type)) }}
                      <template v-if="Number(item.mappedAction.amount || 0)"> {{ chips(Number(item.mappedAction.amount)) }}</template>
                    </template>
                  </span>
                </header>
                <div class="bot-body">
                  <div class="bot-legal">
                    <span class="faint">合法动作 ID</span>
                    <span
                      v-for="id in item.legalIds"
                      :key="id"
                      class="chip"
                      :class="{ chosen: id === item.actionId }"
                    >{{ id }}</span>
                  </div>
                  <div v-if="Object.keys(item.probabilities).length" class="bot-probs">
                    <div v-for="(prob, key) in item.probabilities" :key="key" class="bot-prob-row">
                      <span class="mono">{{ key }}</span>
                      <div class="bot-prob-track"><div class="bot-prob-fill" :style="{ width: `${Math.min(100, Number(prob) * 100)}%` }"></div></div>
                      <span class="mono">{{ (Number(prob) * 100).toFixed(1) }}%</span>
                    </div>
                  </div>
                  <p class="bot-note">
                    {{ item.agentType === "random" ? "RLCard RandomAgent：在合法动作中均匀采样，不产生思考链。" : item.agentType.startsWith("project_") ? "项目规则训练的 73 维 / 10 动作策略，动作由模型推理产生。" : "RLCard 策略已加载，动作由模型推理产生。" }}
                  </p>
                </div>
              </div>
            </article>
          </template>
          <div
            v-if="pendingHandId !== null"
            class="hand-divider"
            role="separator"
            :data-hand-id="pendingHandId"
          >
            <span class="hand-divider-label">{{ handDividerLabel(pendingHandId) }}</span>
          </div>
          <div class="stream-anchor-tail" :style="{ height: `${anchorTail}px` }" aria-hidden="true"></div>
        </template>
      </div>

      <div v-else class="log-body">
        <div v-if="!groups.length" class="empty-state">
          <div class="empty-orb"><AppIcon name="list" :size="28" /></div>
          <h3>还没有行动记录</h3>
          <p>每一次下注、跟注、弃牌都会按手牌归档到这里。</p>
        </div>
        <section v-for="group in groups" :key="group.handId" class="log-group">
          <header class="log-head">
            <span class="log-hand mono">#{{ group.handId }}</span>
            <span class="log-result" :class="{ won: group.result?.winner_seats?.includes(humanSeat?.seat ?? -1) }">
              {{ resultLine(group) }}
            </span>
            <div v-if="group.result?.board?.length" class="log-board">
              <PlayingCard
                v-for="(code, index) in group.result.board"
                :key="code"
                :card="{ code, rank: code[0] === 'T' ? 'T' : code[0], suit: code[1], symbol: ({ c: '♣', d: '♦', h: '♥', s: '♠' } as Record<string, string>)[code[1]] || code[1], red: code[1] === 'h' || code[1] === 'd', label: code }"
                size="mini"
                :index="index"
              />
            </div>
          </header>
          <div class="log-rows">
            <div v-for="action in group.actions" :key="action.id" class="log-row">
              <span class="log-street" :class="action.street">{{ streetShort(action.street) }}</span>
              <span class="log-seat">{{ shortName(seatName(action.seat), 14) }}</span>
              <span class="log-action" :class="action.type">
                {{ describeAction(action) }}
              </span>
              <span v-if="action.auto" class="auto-chip">AUTO</span>
              <span class="log-pot mono">底池 {{ chips(action.potAfter) }}</span>
            </div>
          </div>
        </section>
      </div>
    </div>

    <transition name="jump">
      <button v-if="showJump" class="jump-latest" @click="jumpLatest">
        <AppIcon name="arrowRight" :size="12" class="rotate" />
        回到最新
      </button>
    </transition>

    <footer class="console-foot">
      <span class="foot-dot" :class="{ live: anyLive }"></span>
      <span>{{ anyLive ? "Agent 正在生成" : game.connected ? "空闲" : "连接已断开" }}</span>
      <span class="foot-spacer"></span>
      <span v-if="mode === 'log'" class="mono">{{ game.actionFeed.length }} actions</span>
      <button
        v-if="reasoningDefaultCollapsed"
        class="foot-action"
        type="button"
        title="恢复思考自动折叠"
        @click="setReasoningDefaultCollapsed(false)"
      >
        <AppIcon name="eyeOff" :size="11" />
        思考默认折叠
      </button>
    </footer>
  </aside>
</template>

<style scoped>
.agent-console {
  position: sticky;
  top: 76px;
  display: flex;
  flex-direction: column;
  width: 100%;
  height: calc(100vh - 96px);
  min-height: 520px;
  border-radius: var(--radius-xl);
  border: 1px solid var(--line);
  background:
    radial-gradient(500px 220px at 12% -8%, rgba(48, 112, 86, 0.18), transparent 65%),
    linear-gradient(180deg, rgba(14, 27, 22, 0.96), rgba(7, 16, 12, 0.98));
  box-shadow: var(--shadow-lg);
  overflow: hidden;
}

.console-head {
  flex: none;
  border-bottom: 1px solid var(--line);
  background: rgba(6, 14, 11, 0.4);
}

.head-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 12px 14px 9px;
}

.head-title { min-width: 0; flex: none; }

.head-title h2 {
  margin: 0;
  font-size: calc(14px * var(--type-scale));
  font-weight: 750;
  letter-spacing: -0.015em;
  display: flex;
  align-items: center;
  gap: 7px;
}

.head-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex: none;
}

.follow-btn { padding: 4px 8px; font-size: calc(10px * var(--type-scale)); }
.follow-btn.active { color: var(--emerald); }
.mode-switch button { padding: 4px 8px; font-size: calc(10px * var(--type-scale)); }

.seat-tabs {
  display: flex;
  gap: 5px;
  flex: 1;
  min-width: 0;
  overflow-x: auto;
}

.seat-tab {
  position: relative;
  width: 34px;
  height: 34px;
  flex: none;
  display: grid;
  place-items: center;
  padding: 0;
  border-radius: 9px;
  border: 1px solid transparent;
  transition: background 0.15s ease, border-color 0.15s ease;
}

.seat-tab:hover { background: rgba(255, 255, 255, 0.04); }

.seat-tab.active {
  background: rgba(232, 195, 122, 0.08);
  border-color: rgba(232, 195, 122, 0.22);
}

.tab-icon {
  width: 24px;
  height: 24px;
  flex: none;
  display: grid;
  place-items: center;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.055);
  color: var(--text-soft);
}

.tab-icon.provider { background: #f1ecdf; }
.logo-tile img { width: 17px; height: 17px; object-fit: contain; }

.tab-seat {
  position: absolute;
  right: 1px;
  bottom: 0;
  font-size: calc(6px * var(--type-scale));
  color: var(--faint);
  letter-spacing: 0;
}

.seat-tab .dot.live {
  position: absolute;
  top: 2px;
  right: 2px;
}

.console-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 8px 10px 18px;
  position: relative;
}

.stream-body { display: flex; flex-direction: column; }
.log-body { display: flex; flex-direction: column; gap: 12px; padding: 4px 2px; }

.hand-divider {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 2px 4px;
}

.hand-divider::before,
.hand-divider::after {
  content: "";
  flex: 1;
  height: 1px;
}

.hand-divider::before {
  background: linear-gradient(90deg, transparent, var(--line-strong));
}

.hand-divider::after {
  background: linear-gradient(90deg, var(--line-strong), transparent);
}

.hand-divider-label {
  flex: none;
  padding: 2px 9px;
  border-radius: 999px;
  border: 1px solid rgba(232, 195, 122, 0.2);
  background: rgba(232, 195, 122, 0.06);
  color: var(--gold);
  font-family: var(--font-mono);
  font-size: calc(8px * var(--type-scale));
  font-weight: 700;
  letter-spacing: 0.12em;
  white-space: nowrap;
}

.stream-anchor-tail {
  flex: none;
  width: 100%;
  pointer-events: none;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  gap: 8px;
  padding: 50px 26px;
  min-height: 340px;
}

.empty-orb {
  width: 64px;
  height: 64px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  border: 1px solid rgba(232, 195, 122, 0.22);
  background:
    radial-gradient(circle at 36% 28%, rgba(232, 195, 122, 0.18), transparent 60%),
    rgba(255, 255, 255, 0.025);
  color: var(--gold);
  margin-bottom: 8px;
  box-shadow: 0 0 40px rgba(232, 195, 122, 0.08);
}

.empty-state h3 {
  margin: 0;
  font-size: calc(14px * var(--type-scale));
  font-weight: 750;
  letter-spacing: -0.01em;
}

.empty-state p {
  margin: 0;
  max-width: 300px;
  font-size: calc(11px * var(--type-scale));
  line-height: 1.65;
  color: var(--muted);
}

.empty-flow {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 8px;
  font-size: calc(9px * var(--type-scale));
  color: var(--faint);
}

.empty-flow span {
  padding: 3px 7px;
  border-radius: 5px;
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.025);
}

/* Bot card */

.bot-turn {
  display: flex;
  gap: 10px;
  padding: 4px 0 12px;
}

.bot-rail {
  width: 16px;
  flex: none;
  display: flex;
  justify-content: center;
}

.bot-node {
  width: 9px;
  height: 9px;
  margin-top: 9px;
  border-radius: 50%;
  background: var(--blue);
  border: 2px solid var(--panel-solid);
  box-shadow: 0 0 0 1px var(--line-strong);
}

.bot-card {
  flex: 1;
  min-width: 0;
  border: 1px solid var(--line);
  border-left: 2px solid rgba(121, 169, 255, 0.5);
  border-radius: 0 10px 10px 0;
  background: rgba(121, 169, 255, 0.035);
}

.bot-head {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  padding: 7px 10px;
  border-bottom: 1px solid var(--line);
  font-size: calc(10px * var(--type-scale));
}

.bot-icon { color: var(--blue); display: flex; }
.bot-head strong { font-size: calc(10.5px * var(--type-scale)); }
.bot-action { margin-left: auto; color: var(--blue); font-size: calc(9px * var(--type-scale)); font-weight: 700; }

.bot-body { padding: 7px 10px 8px; display: flex; flex-direction: column; gap: 6px; }

.bot-legal { display: flex; align-items: center; gap: 4px; flex-wrap: wrap; font-size: calc(8.5px * var(--type-scale)); }
.bot-legal .chip { font-size: calc(8px * var(--type-scale)); padding: 1px 5px; }
.bot-legal .chip.chosen { color: var(--blue); border-color: rgba(121, 169, 255, 0.4); background: var(--blue-dim); }

.bot-probs { display: flex; flex-direction: column; gap: 3px; }
.bot-prob-row { display: flex; align-items: center; gap: 6px; font-size: calc(8.5px * var(--type-scale)); color: var(--faint); }
.bot-prob-row > span:first-child { width: 14px; }
.bot-prob-row > span:last-child { width: 40px; text-align: right; }
.bot-prob-track { flex: 1; height: 3px; border-radius: 99px; background: rgba(255, 255, 255, 0.06); overflow: hidden; }
.bot-prob-fill { height: 100%; background: var(--blue); border-radius: 99px; opacity: 0.7; }

.bot-note { margin: 0; font-size: calc(9px * var(--type-scale)); line-height: 1.5; color: var(--faint); }

/* Log */

.log-group {
  border: 1px solid var(--line);
  border-radius: 10px;
  overflow: hidden;
}

.log-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 9px;
  border-bottom: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.025);
  flex-wrap: wrap;
}

.log-hand { font-size: calc(9px * var(--type-scale)); color: var(--faint); }
.log-result { font-size: calc(9.5px * var(--type-scale)); color: var(--muted); }
.log-result.won { color: var(--gold); }
.log-board { display: flex; gap: 2px; margin-left: auto; }

.log-rows { display: flex; flex-direction: column; }

.log-row {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 4px 9px;
  font-size: calc(9px * var(--type-scale));
  border-top: 1px solid rgba(255, 255, 255, 0.025);
}

.log-row:first-child { border-top: 0; }

.log-street {
  width: 38px;
  flex: none;
  font-family: var(--font-mono);
  font-size: calc(7px * var(--type-scale));
  letter-spacing: 0.08em;
  color: var(--faint);
}

.log-street.preflop { color: var(--gold); }
.log-street.flop { color: var(--emerald); }
.log-street.turn { color: var(--blue); }
.log-street.river { color: var(--purple); }

.log-seat { width: 76px; flex: none; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--muted); }
.log-action { font-weight: 700; color: var(--text-soft); }
.log-action.fold { color: var(--red); }
.log-action.raise, .log-action.bet { color: var(--blue); }
.log-action.call { color: var(--emerald); }
.log-action.all_in { color: var(--purple); }
.log-action.check { color: var(--muted); }
.log-pot { margin-left: auto; color: var(--faint); font-size: calc(8px * var(--type-scale)); }

.auto-chip {
  font-family: var(--font-mono);
  font-size: calc(6.5px * var(--type-scale));
  font-weight: 800;
  color: var(--amber);
  border: 1px solid rgba(231, 179, 94, 0.3);
  border-radius: 3px;
  padding: 0 3px;
}

/* Footer */

.console-foot {
  flex: none;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 7px 12px;
  border-top: 1px solid var(--line);
  background: rgba(4, 11, 8, 0.5);
  font-size: calc(8.5px * var(--type-scale));
  color: var(--faint);
}

.foot-dot { width: 5px; height: 5px; border-radius: 50%; background: var(--faint); }
.foot-dot.live { background: var(--emerald); box-shadow: 0 0 8px rgba(84, 214, 168, 0.5); }
.foot-spacer { flex: 1; }

.foot-action {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--faint);
  font-size: calc(8.5px * var(--type-scale));
  transition: color 0.16s ease;
}

.foot-action:hover { color: var(--gold); }

.jump-latest {
  position: absolute;
  right: 16px;
  bottom: 54px;
  z-index: 5;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 5px 10px;
  border-radius: 999px;
  border: 1px solid rgba(232, 195, 122, 0.3);
  background: rgba(12, 26, 20, 0.94);
  color: var(--gold);
  font-size: calc(9.5px * var(--type-scale));
  font-weight: 700;
  box-shadow: 0 8px 22px rgba(0, 0, 0, 0.4);
  backdrop-filter: blur(10px);
}

.rotate { transform: rotate(90deg); }

.jump-enter-active, .jump-leave-active { transition: opacity 0.16s ease, transform 0.16s ease; }
.jump-enter-from, .jump-leave-to { opacity: 0; transform: translateY(5px); }

@media (max-width: 1180px) {
  .agent-console {
    position: static;
    height: 680px;
    min-height: 0;
  }
}

@media (max-width: 720px) {
  .agent-console { height: 620px; border-radius: 16px; }
  .head-top { padding: 12px 12px 8px; }
  .console-scroll { padding: 6px 7px 16px; }
  .mode-switch button { padding: 4px 7px; font-size: calc(9px * var(--type-scale)); }
}
</style>
