<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from "vue";
import AppIcon from "./AppIcon.vue";
import { iconUrl } from "../api";
import {
  buildDefaultSetup,
  closeSetup,
  defaultSeat,
  game,
  modelFor,
  providerOf,
  pushToast,
  startMatch,
  testProvider,
} from "../store";
import type { MatchSetupPayload, ProviderInfo, ProviderModel, SeatSetup } from "../types";

interface SetupDraft extends MatchSetupPayload {}

const FALLBACK_RULES: SetupDraft["rules"] = {
  starting_chips: 1000,
  small_blind: 5,
  big_blind: 10,
  auto_rebuy: false,
  rebuy_amount: 1000,
  max_hands: 0,
  seed: null,
  god_view: true,
};

const BOT_DELAY_DEFAULT = 3;
const BOT_DELAY_MIN = 0;
const BOT_DELAY_MAX = 10;

const setup = reactive<SetupDraft>({
  seats: [
    { kind: "llm", name: "LLM Agent" },
    { kind: "human", name: "你" },
  ],
  rules: { ...FALLBACK_RULES },
});

const boot = computed(() => game.boot);
const providers = computed<ProviderInfo[]>(() => boot.value?.providers || []);
const rlcard = computed(() => boot.value?.rlcard || null);
const limits = computed(() => boot.value?.game?.limits || {});
const busy = computed(() => game.busy);
const modalRef = ref<HTMLElement | null>(null);

function deepCopy<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function normalizeBotDelay(value: unknown): number {
  const numeric = Number(value ?? BOT_DELAY_DEFAULT);
  if (!Number.isFinite(numeric)) return BOT_DELAY_DEFAULT;
  const clamped = Math.min(BOT_DELAY_MAX, Math.max(BOT_DELAY_MIN, numeric));
  return Math.round(clamped * 2) / 2;
}

function resetSetup(): void {
  const source = buildDefaultSetup();
  const copy = deepCopy(source);
  setup.seats = copy.seats;
  setup.rules = { ...copy.rules };
  if (!setup.rules.rebuy_amount) setup.rules.rebuy_amount = setup.rules.starting_chips;
  for (const config of setup.seats) {
    if (config.kind !== "bot") continue;
    config.bot = {
      agent_type: config.bot?.agent_type || "random",
      model_path: config.bot?.model_path || null,
      delay_seconds: normalizeBotDelay(config.bot?.delay_seconds),
    };
  }
}

watch(
  () => game.setupOpen,
  (open) => {
    if (open) {
      resetSetup();
      void nextTick(() => modalRef.value?.focus());
    }
  },
  { immediate: true },
);

function onGlobalKeydown(event: KeyboardEvent): void {
  if (!game.setupOpen) return;
  if (event.key === "Escape") {
    event.preventDefault();
    close();
    return;
  }
  if (event.key !== "Tab" || !modalRef.value) return;
  const focusable = Array.from(
    modalRef.value.querySelectorAll<HTMLElement>(
      'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ),
  ).filter((element) => element.getClientRects().length > 0);
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  const active = document.activeElement as HTMLElement | null;
  if (event.shiftKey && (active === first || active === modalRef.value)) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && active === last) {
    event.preventDefault();
    first.focus();
  }
}

onMounted(() => window.addEventListener("keydown", onGlobalKeydown));
onUnmounted(() => window.removeEventListener("keydown", onGlobalKeydown));

function seat(index: number): SeatSetup {
  return setup.seats[index];
}

function otherSeat(index: number): SeatSetup {
  return setup.seats[index === 0 ? 1 : 0];
}

function setKind(index: number, kind: SeatSetup["kind"]): void {
  const current = seat(index);
  if (current.kind === kind) return;
  if (kind === "human" && otherSeat(index).kind === "human") {
    pushToast("warn", "不能同时选择两个人类玩家", "请把另一个座位改成 LLM 或 RLCard。");
    return;
  }
  if (kind === "human") {
    setup.seats[index] = { kind: "human", name: "你" };
  } else if (kind === "bot") {
    setup.seats[index] = {
      kind: "bot",
      name: "RLCard",
      bot: { agent_type: "random", model_path: null, delay_seconds: BOT_DELAY_DEFAULT },
    };
  } else {
    const model = modelFor(current.provider_id || "", current.model_id || "")
      || providers.value.flatMap((provider) => provider.models).find((item) => item.key_present)
      || providers.value.flatMap((provider) => provider.models)[0]
      || null;
    setup.seats[index] = defaultSeat("llm", model);
  }
}

function currentModel(index: number): ProviderModel | null {
  const config = seat(index);
  if (config.kind !== "llm" || !config.provider_id || !config.model_id) return null;
  return modelFor(config.provider_id, config.model_id);
}

function selectProvider(index: number, provider: ProviderInfo): void {
  const current = seat(index);
  if (current.kind === "llm" && current.provider_id === provider.id) return;
  const model = provider.models[0];
  if (!model) return;
  setup.seats[index] = {
    ...seat(index),
    kind: "llm",
    name: `${provider.name} · ${model.name}`,
    provider_id: provider.id,
    model_id: model.id,
    params: { ...model.defaults },
  } as SeatSetup;
}

function selectModel(index: number, model: ProviderModel): void {
  setup.seats[index] = {
    ...seat(index),
    kind: "llm",
    name: `${model.provider_name} · ${model.name}`,
    provider_id: model.provider_id,
    model_id: model.id,
    params: { ...model.defaults },
  } as SeatSetup;
}

function paramsOf(index: number): Record<string, any> {
  const config = seat(index);
  if (!config.params) config.params = {};
  return config.params as Record<string, any>;
}

function setParam(index: number, key: string, value: unknown): void {
  paramsOf(index)[key] = value;
  const model = currentModel(index);
  if (model) delete game.testResults[testKey(index, model)];
}

function setBotType(index: number, agentType: string): void {
  const current = seat(index).bot || { agent_type: "random", model_path: null };
  const preferred: Record<string, string> = {
    dqn: "nlh_dqn.pth",
    nfsp: "nlh_nfsp.pth",
    dmc: "nlh_dmc.pth",
    project_dqn: "nlh_dqn_project.pth",
    project_nfsp: "nlh_nfsp_project.pth",
  };
  const candidates = botModels(agentType);
  const preferredName = preferred[agentType];
  const chosen =
    candidates.find((model) => model.name === preferredName) ||
    candidates[0] ||
    null;
  setup.seats[index] = {
    ...seat(index),
    kind: "bot",
    name: agentType.startsWith("project_") ? "Project Bot" : "RLCard",
    bot: {
      agent_type: agentType,
      model_path: agentType === "random" ? null : (chosen?.path || current.model_path || null),
      delay_seconds: normalizeBotDelay(current.delay_seconds),
    },
  } as SeatSetup;
}

function botModels(agentType: string) {
  const models = rlcard.value?.models || [];
  const project = agentType.startsWith("project_");
  return models.filter((model) => model.name.includes("_project") === project);
}

function setBotModel(index: number, path: string): void {
  const bot = seat(index).bot || { agent_type: "random", model_path: null };
  setup.seats[index] = {
    ...seat(index),
    kind: "bot",
    bot: { ...bot, model_path: path || null },
  } as SeatSetup;
}

function botDelay(index: number): number {
  return normalizeBotDelay(seat(index).bot?.delay_seconds);
}

function setBotDelay(index: number, event: Event): void {
  const bot = seat(index).bot || { agent_type: "random", model_path: null };
  setup.seats[index] = {
    ...seat(index),
    kind: "bot",
    bot: { ...bot, delay_seconds: normalizeBotDelay((event.target as HTMLInputElement).value) },
  } as SeatSetup;
}

function setRule(key: keyof SetupDraft["rules"], value: unknown): void {
  (setup.rules as Record<string, unknown>)[key] = value;
}

function numericRule(key: keyof SetupDraft["rules"]): number {
  const value = Number(setup.rules[key]);
  return Number.isFinite(value) ? value : 0;
}

function setNumericRule(key: keyof SetupDraft["rules"], event: Event): void {
  const value = Number((event.target as HTMLInputElement).value);
  setRule(key, Number.isFinite(value) ? Math.round(value) : 0);
}

function setSeed(event: Event): void {
  const raw = (event.target as HTMLInputElement).value.trim();
  if (raw === "") {
    setRule("seed", null);
    return;
  }
  const value = Number(raw);
  setRule("seed", Number.isFinite(value) ? Math.round(value) : null);
}

function setBlinds(small: number, big: number): void {
  setRule("small_blind", small);
  setRule("big_blind", big);
}

function limitError(key: string, label: string): string {
  const limit = limits.value[key];
  if (!limit) return "";
  const value = numericRule(key as keyof SetupDraft["rules"]);
  if (value < limit.min || value > limit.max) {
    return `${label}必须在 ${limit.min} 到 ${limit.max} 之间。`;
  }
  return "";
}

const validation = computed(() => {
  if (setup.seats.filter((item) => item.kind === "human").length >= 2) return "不能同时选择两个人类玩家。";
  for (const config of setup.seats) {
    if (config.kind === "llm") {
      const model = modelFor(config.provider_id || "", config.model_id || "");
      if (!model) return "请为 LLM 座位选择模型。";
      if (!model.key_present) return `缺少环境变量 ${model.api_key_env}，请写入 .env 后重启服务。`;
    }
    if (config.kind === "bot") {
      const agentType = config.bot?.agent_type || "random";
      if (agentType !== "random" && !config.bot?.model_path) {
        return `${agentType.startsWith("project_") ? "Project" : "RLCard"} ${agentType.toUpperCase()} 需要选择模型文件。`;
      }
      if (!agentType.startsWith("project_") && rlcard.value && !rlcard.value.available) {
        return "rlcard 未安装，无法使用 RLCard Bot。";
      }
    }
  }
  for (const [key, label] of [
    ["starting_chips", "起始筹码"],
    ["small_blind", "小盲"],
    ["big_blind", "大盲"],
    ["rebuy_amount", "重购筹码"],
    ["max_hands", "最大手数"],
  ] as const) {
    const error = limitError(key, label);
    if (error) return error;
  }
  if (numericRule("starting_chips") < numericRule("big_blind")) return "起始筹码不能低于大盲。";
  if (numericRule("small_blind") <= 0 || numericRule("big_blind") <= 0) return "盲注必须大于 0。";
  if (numericRule("small_blind") >= numericRule("big_blind")) return "大盲必须大于小盲。";
  if (numericRule("max_hands") < 0) return "最大手数不能为负。";
  return "";
});

const canStart = computed(() => !validation.value && !busy.value);

function applyPreset(kind: "human_deepseek" | "deepseek_glm" | "human_bot"): void {
  const allModels = providers.value.flatMap((provider) => provider.models);
  const deepseek = allModels.find((model) => model.provider_id === "deepseek" && model.key_present) || allModels.find((model) => model.key_present) || null;
  const glm = allModels.find((model) => model.provider_id === "zhipu" && model.key_present) || allModels.find((model) => model.key_present) || deepseek;
  if (kind === "human_deepseek") {
    setup.seats = [defaultSeat("llm", deepseek), defaultSeat("human")];
  } else if (kind === "deepseek_glm") {
    setup.seats = [defaultSeat("llm", deepseek), defaultSeat("llm", glm)];
  } else {
    setup.seats = [defaultSeat("bot"), defaultSeat("human")];
  }
}

function testKey(index: number, model: ProviderModel): string {
  return `${index}:${model.provider_id}:${model.id}`;
}

function testConnection(index: number): void {
  const model = currentModel(index);
  if (!model) return;
  void testProvider(model, paramsOf(index), testKey(index, model));
}

function testResult(index: number) {
  const model = currentModel(index);
  if (!model) return null;
  return game.testResults[testKey(index, model)] || null;
}

function isTesting(index: number): boolean {
  return testResult(index)?.status === "testing";
}

function compactNumber(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(value % 1_000_000 === 0 ? 0 : 1)}M`;
  if (value >= 1_000) return `${Math.round(value / 1_000)}K`;
  return String(value);
}

function rangePct(value: number, min: number, max: number): string {
  const span = max - min;
  if (span <= 0) return "100%";
  return `${Math.max(0, Math.min(100, ((value - min) / span) * 100))}%`;
}

function modelQuirk(model: ProviderModel): string {
  if (model.provider_id === "minimax") return "M3.1 Flash 始终思考，会忽略 temperature / top_p。";
  if (model.provider_id === "zhipu") return "GLM-5.3 Flash 思考常开，reasoning_effort 控制深度。";
  if (model.provider_id === "kimi") return "K2.7 Code 强制思考，使用 think 模式返回 reasoning_content。";
  if (model.provider_id === "qwen") return "Qwen3.8 思考模式默认开启，历史 reasoning_content 会原样回传。";
  if (model.provider_id === "deepseek") return "DeepSeek V4 思考模式通过 thinking + reasoning_effort 控制。";
  if (model.provider_id === "longcat") return "LongCat 开启思考时 temperature 固定为 1.0。";
  return "OpenAI 兼容接口，已按厂商文档适配工具调用参数。";
}

async function submit(): Promise<void> {
  if (!canStart.value) return;
  const payload = deepCopy(setup);
  await startMatch(payload);
}

function close(): void {
  if (!busy.value) closeSetup();
}
</script>

<template>
  <div v-if="game.setupOpen" class="modal-backdrop" @click.self="close" @keydown.esc="close">
    <div ref="modalRef" class="modal setup-modal" role="dialog" aria-modal="true" aria-label="配置对局" tabindex="-1">
      <header class="modal-header">
        <div>
          <div class="eyebrow">MATCH SETUP</div>
          <h2 class="modal-title">配置一局单挑</h2>
          <p class="modal-subtitle">
            人类、LLM、RLCard，任选其二。LLM 会通过两个工具读取牌型概率并提交动作。
          </p>
        </div>
        <button class="btn btn-ghost btn-icon" aria-label="关闭" @click="close">
          <AppIcon name="x" :size="18" />
        </button>
      </header>

      <div class="modal-body setup-body">
        <div class="preset-row">
          <span class="preset-label">快速开始</span>
          <button class="preset" @click="applyPreset('human_deepseek')">
            <AppIcon name="user" :size="12" /> 人 vs DeepSeek
          </button>
          <button class="preset" @click="applyPreset('deepseek_glm')">
            <AppIcon name="sparkles" :size="12" /> DeepSeek vs GLM
          </button>
          <button class="preset" @click="applyPreset('human_bot')">
            <AppIcon name="bot" :size="12" /> 人 vs RLCard
          </button>
          <span class="preset-hint">选择后会覆盖双方配置</span>
        </div>

        <section class="setup-section">
          <div class="section-head">
            <div>
              <span class="section-kicker">01</span>
              <h3>对局双方</h3>
            </div>
            <span class="section-note">不能同时选择两个人类玩家</span>
          </div>

          <div class="seat-grid">
            <article v-for="(config, index) in setup.seats" :key="index" class="seat-config" :class="{ human: config.kind === 'human' }">
              <header class="seat-config-head">
                <div class="seat-label">
                  <span class="seat-tag">SEAT {{ index === 0 ? "A" : "B" }}</span>
                  <span class="seat-position">{{ index === 0 ? "上座" : "下座" }}</span>
                </div>
                <div class="segmented kind-switch">
                  <button
                    :class="{ active: config.kind === 'human' }"
                    :disabled="config.kind !== 'human' && otherSeat(index).kind === 'human'"
                    :title="otherSeat(index).kind === 'human' && config.kind !== 'human' ? '不能同时选择两个人类玩家' : ''"
                    @click="setKind(index, 'human')"
                  >
                    人类
                  </button>
                  <button :class="{ active: config.kind === 'llm' }" @click="setKind(index, 'llm')">LLM</button>
                  <button :class="{ active: config.kind === 'bot' }" @click="setKind(index, 'bot')">RLCard</button>
                </div>
              </header>

              <!-- Human -->
              <div v-if="config.kind === 'human'" class="seat-body human-body">
                <div class="human-orb"><AppIcon name="user" :size="24" /></div>
                <h4>你亲自上桌</h4>
                <p>轮到你时，底部的行动栏会亮起。支持快捷键：F 弃牌、C 过牌/跟注、R 加注、A 全下。</p>
                <div class="human-features">
                  <span class="chip"><AppIcon name="eye" :size="10" /> 自己的底牌可见</span>
                  <span class="chip"><AppIcon name="clock" :size="10" /> 无限思考时间</span>
                  <span class="chip"><AppIcon name="cards" :size="10" /> 上帝视角可选</span>
                </div>
                <blockquote class="human-quote">
                  单挑是最纯粹的德州扑克：位置、范围与心理，每一手都在对话。
                </blockquote>
              </div>

              <!-- LLM -->
              <div v-else-if="config.kind === 'llm'" class="seat-body">
                <div class="provider-grid">
                  <button
                    v-for="provider in providers"
                    :key="provider.id"
                    class="provider-button"
                    :class="{ active: config.provider_id === provider.id, missing: !provider.models.some((model) => model.key_present) }"
                    :style="{ '--provider-accent': provider.accent }"
                    @click="selectProvider(index, provider)"
                  >
                    <span class="provider-icon logo-tile">
                      <img :src="iconUrl(provider.icon)" :alt="provider.name" />
                    </span>
                    <span class="provider-name">{{ provider.name }}</span>
                    <span v-if="provider.models.some((model) => model.key_present)" class="key-dot ok"></span>
                    <span v-else class="key-dot missing"></span>
                  </button>
                </div>

                <template v-if="currentModel(index)">
                  <div class="model-card" :style="{ '--provider-accent': currentModel(index)!.accent }">
                    <div class="model-head">
                      <span class="model-icon logo-tile"><img :src="iconUrl(currentModel(index)!.icon)" :alt="currentModel(index)!.provider_name" /></span>
                      <div class="model-title">
                        <strong>{{ currentModel(index)!.name }}</strong>
                        <span>{{ currentModel(index)!.tagline }}</span>
                      </div>
                      <div class="model-specs">
                        <span class="chip mono">CTX {{ compactNumber(currentModel(index)!.context_window) }}</span>
                        <span class="chip mono">OUT {{ compactNumber(currentModel(index)!.max_output) }}</span>
                      </div>
                    </div>

                    <div class="key-row">
                      <span v-if="currentModel(index)!.key_present" class="key-state ok">
                        <span class="dot live"></span>
                        KEY 已配置 · {{ currentModel(index)!.api_key_env }}
                      </span>
                      <span v-else class="key-state missing">
                        <AppIcon name="alert" :size="11" />
                        缺少 {{ currentModel(index)!.api_key_env }}
                      </span>
                      <a class="docs-link" :href="currentModel(index)!.docs_url" target="_blank" rel="noopener">
                        文档 <AppIcon name="external" :size="10" />
                      </a>
                    </div>

                    <div class="param-grid">
                      <label v-if="currentModel(index)!.controls.temperature?.show" class="param">
                        <div class="param-head">
                          <span>温度 <span class="param-en">temperature</span></span>
                          <strong class="mono">{{ Number(paramsOf(index).temperature ?? 0).toFixed(2) }}</strong>
                        </div>
                        <input
                          class="range"
                          type="range"
                          :min="currentModel(index)!.controls.temperature!.min ?? 0"
                          :max="currentModel(index)!.controls.temperature!.max ?? 2"
                          :step="currentModel(index)!.controls.temperature!.step ?? 0.1"
                          :value="paramsOf(index).temperature"
                          :style="{ '--pct': rangePct(Number(paramsOf(index).temperature ?? 0), currentModel(index)!.controls.temperature!.min ?? 0, currentModel(index)!.controls.temperature!.max ?? 1) }"
                          @input="setParam(index, 'temperature', Number(($event.target as HTMLInputElement).value))"
                        />
                        <span class="param-hint">{{ currentModel(index)!.controls.temperature!.hint }}</span>
                      </label>

                      <label v-if="currentModel(index)!.controls.top_p?.show" class="param">
                        <div class="param-head">
                          <span>Top P <span class="param-en">top_p</span></span>
                          <strong class="mono">{{ Number(paramsOf(index).top_p ?? 0).toFixed(2) }}</strong>
                        </div>
                        <input
                          class="range"
                          type="range"
                          :min="currentModel(index)!.controls.top_p!.min ?? 0"
                          :max="currentModel(index)!.controls.top_p!.max ?? 1"
                          :step="currentModel(index)!.controls.top_p!.step ?? 0.05"
                          :value="paramsOf(index).top_p"
                          :style="{ '--pct': rangePct(Number(paramsOf(index).top_p ?? 0), currentModel(index)!.controls.top_p!.min ?? 0, currentModel(index)!.controls.top_p!.max ?? 1) }"
                          @input="setParam(index, 'top_p', Number(($event.target as HTMLInputElement).value))"
                        />
                        <span class="param-hint">{{ currentModel(index)!.controls.top_p!.hint }}</span>
                      </label>

                      <label v-if="currentModel(index)!.controls.max_tokens?.show" class="param span-2">
                        <div class="param-head">
                          <span>最大输出 <span class="param-en">max_tokens</span></span>
                          <strong class="mono">{{ Number(paramsOf(index).max_tokens ?? 0).toLocaleString("en-US") }}</strong>
                        </div>
                        <input
                          class="range"
                          type="range"
                          :min="currentModel(index)!.controls.max_tokens!.min ?? 1024"
                          :max="currentModel(index)!.controls.max_tokens!.max ?? 131072"
                          :step="currentModel(index)!.controls.max_tokens!.step ?? 1024"
                          :value="paramsOf(index).max_tokens"
                          :style="{ '--pct': rangePct(Number(paramsOf(index).max_tokens ?? 0), currentModel(index)!.controls.max_tokens!.min ?? 1024, currentModel(index)!.controls.max_tokens!.max ?? 8192) }"
                          @input="setParam(index, 'max_tokens', Number(($event.target as HTMLInputElement).value))"
                        />
                        <span class="param-hint">{{ currentModel(index)!.controls.max_tokens!.hint }}</span>
                      </label>
                    </div>

                    <div class="mode-row" v-if="currentModel(index)!.controls.thinking">
                      <div class="mode-label">
                        <span>{{ currentModel(index)!.controls.thinking!.label || "深度思考" }}</span>
                        <span class="param-hint">{{ currentModel(index)!.controls.thinking!.hint }}</span>
                      </div>
                      <button
                        v-if="currentModel(index)!.controls.thinking!.mode === 'toggle'"
                        class="toggle"
                        :class="{ on: Boolean(paramsOf(index).thinking) }"
                        @click="setParam(index, 'thinking', !paramsOf(index).thinking)"
                      ></button>
                      <span v-else-if="currentModel(index)!.controls.thinking!.mode === 'always'" class="chip gold">
                        始终开启
                      </span>
                      <span v-else class="chip">不支持</span>
                    </div>

                    <div
                      v-if="currentModel(index)!.controls.reasoning_effort?.show && (currentModel(index)!.controls.reasoning_effort!.options || []).length"
                      class="mode-row"
                    >
                      <div class="mode-label">
                        <span>推理深度 <span class="param-en">reasoning_effort</span></span>
                        <span class="param-hint">{{ currentModel(index)!.controls.reasoning_effort!.hint }}</span>
                      </div>
                      <div class="segmented effort-switch">
                        <button
                          v-for="option in currentModel(index)!.controls.reasoning_effort!.options || []"
                          :key="option"
                          :class="{ active: paramsOf(index).reasoning_effort === option }"
                          @click="setParam(index, 'reasoning_effort', option)"
                        >
                          {{ option }}
                        </button>
                      </div>
                    </div>

                    <div class="quirk-row">
                      <AppIcon name="zap" :size="11" />
                      <span>{{ modelQuirk(currentModel(index)!) }}</span>
                    </div>

                    <div class="test-row">
                      <button class="btn btn-sm" :disabled="!currentModel(index)!.key_present || isTesting(index)" @click="testConnection(index)">
                        <span v-if="isTesting(index)" class="spinner"></span>
                        <AppIcon v-else name="play" :size="12" />
                        测试连接
                      </button>
                      <div v-if="testResult(index)" class="test-result" :class="testResult(index)!.status">
                        <span v-if="testResult(index)!.status === 'testing'" class="spinner"></span>
                        <AppIcon v-else-if="testResult(index)!.status === 'ok'" name="check" :size="12" />
                        <AppIcon v-else name="alert" :size="12" />
                        <span class="test-text">{{ testResult(index)!.text }}</span>
                        <span v-if="testResult(index)!.latency" class="mono">{{ testResult(index)!.latency }}ms</span>
                      </div>
                    </div>
                  </div>
                </template>
                <div v-else class="seat-placeholder">选择一个 Provider 和模型。</div>
              </div>

              <!-- Bot -->
              <div v-else class="seat-body">
                <div class="bot-grid">
                  <button
                    v-for="agent in rlcard?.agents || []"
                    :key="agent.id"
                    class="bot-option"
                    :class="{ active: config.bot?.agent_type === agent.id }"
                    @click="setBotType(index, agent.id)"
                  >
                    <span class="bot-icon"><AppIcon :name="agent.id === 'random' ? 'zap' : 'brain'" :size="16" /></span>
                    <span class="bot-text">
                      <strong>{{ agent.name }}</strong>
                      <small>{{ agent.description }}</small>
                    </span>
                  </button>
                </div>

                <label class="field">
                  <div class="param-head">
                    <span>行动延迟 <span class="param-en">delay</span></span>
                    <strong class="mono">{{ botDelay(index).toFixed(1) }}s</strong>
                  </div>
                  <input
                    class="range"
                    type="range"
                    :min="BOT_DELAY_MIN"
                    :max="BOT_DELAY_MAX"
                    step="0.5"
                    :value="botDelay(index)"
                    :style="{ '--pct': rangePct(botDelay(index), BOT_DELAY_MIN, BOT_DELAY_MAX) }"
                    @input="setBotDelay(index, $event)"
                  />
                  <span class="field-hint">每次行动前暂停，方便你看清牌桌变化；0 秒表示立即出手。</span>
                </label>

                <label v-if="config.bot?.agent_type !== 'random'" class="field">
                  <span class="field-label">模型文件</span>
                  <select class="input" :value="config.bot?.model_path || ''" @change="setBotModel(index, ($event.target as HTMLSelectElement).value)">
                    <option value="">选择 {{ rlcard?.model_dir }} 下的模型</option>
                    <option v-for="model in botModels(config.bot?.agent_type || 'random')" :key="model.path" :value="model.path">
                      {{ model.name }} · {{ model.size_kb }} KB
                    </option>
                  </select>
                  <span class="field-hint" v-if="!(rlcard?.models || []).length">
                    还没有模型文件。可以先使用 RandomAgent 验证链路，或运行 scripts/train_rlcard.py 训练。
                  </span>
                </label>

                <div class="bot-note">
                  <AppIcon name="bot" :size="13" />
                  <span>RLCard Bot 不产生思考链；它的动作会以紧凑卡片出现在右侧 Agent 面板。</span>
                </div>

                <div v-if="rlcard && !rlcard.available" class="inline-warning">
                  <AppIcon name="alert" :size="12" />
                  rlcard 未安装，当前无法使用 RLCard Bot。
                </div>
              </div>
            </article>
          </div>
        </section>

        <section class="setup-section">
          <div class="section-head">
            <div>
              <span class="section-kicker">02</span>
              <h3>牌局规则</h3>
            </div>
            <span class="section-note">上帝视角只影响观战界面，Agent 永远看不到对手底牌</span>
          </div>

          <div class="rules-grid">
            <label class="field">
              <span class="field-label">起始筹码</span>
              <input class="input" type="number" :min="limits.starting_chips?.min || 100" :max="limits.starting_chips?.max || 100000" :value="numericRule('starting_chips')" @input="setNumericRule('starting_chips', $event)" />
            </label>
            <label class="field">
              <span class="field-label">小盲 / 大盲</span>
              <div class="blind-row">
                <input class="input" type="number" :min="1" :value="numericRule('small_blind')" @input="setNumericRule('small_blind', $event)" />
                <span class="blind-sep">/</span>
                <input class="input" type="number" :min="2" :value="numericRule('big_blind')" @input="setNumericRule('big_blind', $event)" />
              </div>
            </label>
            <label class="field">
              <span class="field-label">最大手数 <span class="field-hint-inline">0 = 不限</span></span>
              <input class="input" type="number" min="0" :max="limits.max_hands?.max || 1000" :value="numericRule('max_hands')" @input="setNumericRule('max_hands', $event)" />
            </label>
            <label class="field">
              <span class="field-label">随机种子 <span class="field-hint-inline">留空 = 随机</span></span>
              <input class="input" type="number" placeholder="random" :value="setup.rules.seed ?? ''" @input="setSeed" />
            </label>
          </div>

          <div class="rules-row">
            <div class="rule-toggle">
              <div class="rule-text">
                <strong>自动重购</strong>
                <span>输光筹码后，下一手自动补回起始筹码。</span>
              </div>
              <button class="toggle" :class="{ on: setup.rules.auto_rebuy }" @click="setRule('auto_rebuy', !setup.rules.auto_rebuy)"></button>
            </div>
            <label v-if="setup.rules.auto_rebuy" class="field rebuy-field">
              <span class="field-label">重购筹码</span>
              <input class="input" type="number" :min="limits.rebuy_amount?.min || 100" :max="limits.rebuy_amount?.max || 100000" :value="numericRule('rebuy_amount')" @input="setNumericRule('rebuy_amount', $event)" />
            </label>
            <div class="rule-toggle">
              <div class="rule-text">
                <strong>上帝视角</strong>
                <span>显示双方底牌、烧牌与完整牌堆。Agent 不受影响。</span>
              </div>
              <button class="toggle" :class="{ on: setup.rules.god_view }" @click="setRule('god_view', !setup.rules.god_view)"></button>
            </div>
          </div>

          <div class="blind-presets">
            <span class="faint">快速盲注</span>
            <button class="chip" @click="setBlinds(5, 10)">5 / 10</button>
            <button class="chip" @click="setBlinds(10, 20)">10 / 20</button>
            <button class="chip" @click="setBlinds(25, 50)">25 / 50</button>
          </div>
        </section>
      </div>

      <footer class="modal-footer">
        <div class="footer-note">
          <span v-if="validation" class="validation error">
            <AppIcon name="alert" :size="12" />
            {{ validation }}
          </span>
          <span v-else class="validation ok">
            <AppIcon name="shield" :size="12" />
            配置有效 · 每个 Agent 只读取自己的底牌
          </span>
          <span class="footer-hint">
            <template v-if="game.snapshot?.has_match">开始后将终止当前对局 · </template>
            快捷键 F / C / R / A
          </span>
        </div>
        <div class="footer-actions">
          <button class="btn" :disabled="busy" @click="close">取消</button>
          <button class="btn btn-primary start-btn" :disabled="!canStart" @click="submit">
            <span v-if="busy" class="spinner"></span>
            <AppIcon v-else name="play" :size="14" />
            {{ busy ? "正在开局…" : "开始对局" }}
          </button>
        </div>
      </footer>
    </div>
  </div>
</template>

<style scoped>
.setup-modal {
  width: min(1120px, 100%);
  max-height: min(960px, calc(100vh - 28px));
}

.setup-body { gap: 16px; }

.preset-row {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  padding: 8px 10px;
  border-radius: 10px;
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.022);
}

.preset-label {
  font-size: calc(9px * var(--type-scale));
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: var(--faint);
  margin-right: 4px;
}

.preset {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 9px;
  border-radius: 6px;
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.03);
  color: var(--text-soft);
  font-size: calc(10px * var(--type-scale));
  font-weight: 600;
}

.preset:hover { border-color: rgba(232, 195, 122, 0.3); color: var(--gold); }

.preset-hint { margin-left: auto; font-size: calc(9px * var(--type-scale)); color: var(--faint); }

.setup-section { display: flex; flex-direction: column; gap: 10px; }

.section-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
}

.section-head > div { display: flex; align-items: baseline; gap: 8px; }

.section-kicker {
  font-family: var(--font-mono);
  font-size: calc(9px * var(--type-scale));
  color: var(--gold);
  border: 1px solid rgba(232, 195, 122, 0.25);
  border-radius: 4px;
  padding: 1px 5px;
}

.section-head h3 { margin: 0; font-size: calc(14px * var(--type-scale)); font-weight: 750; letter-spacing: -0.01em; }
.section-note { font-size: calc(9.5px * var(--type-scale)); color: var(--faint); }

.seat-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.seat-config {
  min-width: 0;
  border: 1px solid var(--line);
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.018);
  overflow: hidden;
}

.seat-config.human { border-color: rgba(84, 214, 168, 0.16); }

.seat-config-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 9px 10px;
  border-bottom: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.022);
}

.seat-label { display: flex; align-items: center; gap: 6px; }

.seat-tag {
  font-family: var(--font-mono);
  font-size: calc(8px * var(--type-scale));
  font-weight: 800;
  letter-spacing: 0.12em;
  color: var(--gold);
  border: 1px solid rgba(232, 195, 122, 0.28);
  border-radius: 4px;
  padding: 1px 5px;
}

.seat-position { font-size: calc(10px * var(--type-scale)); color: var(--faint); }

.kind-switch button { padding: 4px 8px; font-size: calc(10px * var(--type-scale)); }

.seat-body {
  padding: 9px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-height: 200px;
}

.human-body {
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 18px 22px;
  min-height: 260px;
}

.human-orb {
  width: 54px;
  height: 54px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  border: 1px solid rgba(84, 214, 168, 0.24);
  background: radial-gradient(circle at 35% 28%, rgba(84, 214, 168, 0.18), transparent 60%);
  color: var(--emerald);
  margin-bottom: 2px;
}

.human-body h4 { margin: 0; font-size: calc(14px * var(--type-scale)); font-weight: 750; }
.human-body p { margin: 0; max-width: 280px; font-size: calc(10.5px * var(--type-scale)); line-height: 1.65; color: var(--muted); }
.human-features { display: flex; gap: 5px; flex-wrap: wrap; justify-content: center; margin-top: 3px; }

.human-quote {
  margin: 10px 0 0;
  padding: 9px 12px;
  max-width: 300px;
  border-left: 2px solid rgba(232, 195, 122, 0.3);
  background: linear-gradient(90deg, rgba(232, 195, 122, 0.045), transparent);
  color: var(--faint);
  font-size: calc(9.5px * var(--type-scale));
  line-height: 1.7;
  text-align: left;
  border-radius: 0 8px 8px 0;
}

.provider-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 4px;
}

.provider-button {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  padding: 6px 4px;
  border-radius: 9px;
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.022);
  transition: all 0.15s ease;
  min-width: 0;
}

.provider-button:hover { border-color: var(--line-strong); background: rgba(255, 255, 255, 0.045); }

.provider-button.active {
  border-color: color-mix(in srgb, var(--provider-accent) 60%, transparent);
  background: color-mix(in srgb, var(--provider-accent) 10%, transparent);
  box-shadow: 0 0 0 1px color-mix(in srgb, var(--provider-accent) 20%, transparent) inset;
}

.provider-button.missing { opacity: 0.62; }

.logo-tile {
  display: grid;
  place-items: center;
  width: 27px;
  height: 27px;
  border-radius: 7px;
  background: #f2ede1;
  flex: none;
  overflow: hidden;
}

.logo-tile img { width: 19px; height: 19px; object-fit: contain; }
.provider-name { font-size: calc(9px * var(--type-scale)); font-weight: 650; color: var(--text-soft); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%; }

.key-dot {
  position: absolute;
  top: 4px;
  right: 4px;
  width: 5px;
  height: 5px;
  border-radius: 50%;
}

.key-dot.ok { background: var(--emerald); box-shadow: 0 0 6px rgba(84, 214, 168, 0.5); }
.key-dot.missing { background: var(--red); }

.model-card {
  display: flex;
  flex-direction: column;
  gap: 7px;
  padding: 8px;
  border-radius: 10px;
  border: 1px solid var(--line);
  background: rgba(4, 12, 9, 0.35);
}

.model-head { display: flex; align-items: center; gap: 8px; }
.model-icon { width: 30px; height: 30px; }
.model-icon img { width: 21px; height: 21px; }
.model-title { display: flex; flex-direction: column; gap: 1px; min-width: 0; }
.model-title strong { font-size: calc(11.5px * var(--type-scale)); font-weight: 750; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.model-title span { font-size: calc(8.5px * var(--type-scale)); color: var(--faint); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.model-specs { margin-left: auto; display: flex; gap: 3px; flex-wrap: wrap; justify-content: flex-end; }
.model-specs .chip { font-size: calc(7px * var(--type-scale)); padding: 1px 4px; }

.key-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }

.key-state {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: calc(9px * var(--type-scale));
  color: var(--faint);
}

.key-state.ok { color: var(--emerald); }
.key-state.missing { color: var(--red); }

.docs-link { margin-left: auto; display: inline-flex; align-items: center; gap: 3px; font-size: calc(9px * var(--type-scale)); color: var(--faint); }
.docs-link:hover { color: var(--gold); }

.param-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 7px 12px;
}

.param { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.param.span-2 { grid-column: span 2; }

.param-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: calc(9.5px * var(--type-scale));
  color: var(--muted);
}

.param-head strong { font-size: calc(9.5px * var(--type-scale)); color: var(--gold); }
.param-en { font-family: var(--font-mono); font-size: calc(7.5px * var(--type-scale)); color: var(--faint); margin-left: 3px; }
.param-hint { font-size: calc(8px * var(--type-scale)); line-height: 1.45; color: var(--faint); }

.mode-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 6px 0;
  border-top: 1px solid var(--line);
}

.mode-label { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.mode-label > span:first-child { font-size: calc(9.5px * var(--type-scale)); font-weight: 650; color: var(--muted); }
.effort-switch { flex: none; }
.effort-switch button { font-size: calc(8.5px * var(--type-scale)); padding: 3px 7px; }

.quirk-row {
  display: flex;
  align-items: flex-start;
  gap: 5px;
  font-size: calc(8.5px * var(--type-scale));
  line-height: 1.5;
  color: var(--faint);
  padding-top: 2px;
}

.test-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; padding-top: 2px; }
.test-result { display: inline-flex; align-items: center; gap: 4px; font-size: calc(8.5px * var(--type-scale)); min-width: 0; }
.test-result.ok { color: var(--emerald); }
.test-result.error { color: var(--red); }
.test-result.testing { color: var(--muted); }
.test-text { max-width: 160px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.bot-grid { display: flex; flex-direction: column; gap: 5px; }

.bot-option {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 7px 8px;
  border-radius: 8px;
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.02);
  text-align: left;
}

.bot-option:hover { border-color: var(--line-strong); }
.bot-option.active { border-color: rgba(121, 169, 255, 0.4); background: var(--blue-dim); }
.bot-icon { color: var(--blue); display: flex; margin-top: 1px; }
.bot-text { display: flex; flex-direction: column; gap: 1px; min-width: 0; }
.bot-text strong { font-size: calc(10.5px * var(--type-scale)); font-weight: 700; }
.bot-text small { font-size: calc(8.5px * var(--type-scale)); line-height: 1.45; color: var(--faint); }

.bot-note {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  font-size: calc(8.5px * var(--type-scale));
  line-height: 1.5;
  color: var(--faint);
  padding: 7px 8px;
  border-radius: 7px;
  background: rgba(255, 255, 255, 0.02);
  border: 1px dashed var(--line);
}

.inline-warning {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: calc(9px * var(--type-scale));
  color: var(--red);
}

.seat-placeholder { font-size: calc(10px * var(--type-scale)); color: var(--faint); padding: 14px; text-align: center; }

.rules-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}

.blind-row { display: flex; align-items: center; gap: 5px; }
.blind-sep { color: var(--faint); font-size: calc(11px * var(--type-scale)); }
.field-hint-inline { font-size: calc(8px * var(--type-scale)); color: var(--faint); font-weight: 400; }

.rules-row {
  display: flex;
  align-items: center;
  gap: 18px;
  flex-wrap: wrap;
  padding: 9px 10px;
  border-radius: 9px;
  border: 1px solid var(--line);
  background: rgba(255, 255, 255, 0.018);
}

.rule-toggle { display: flex; align-items: center; gap: 12px; flex: 1; min-width: 220px; }
.rule-text { display: flex; flex-direction: column; gap: 1px; }
.rule-text strong { font-size: calc(10.5px * var(--type-scale)); font-weight: 700; }
.rule-text span { font-size: calc(8.5px * var(--type-scale)); color: var(--faint); line-height: 1.45; }
.rebuy-field { width: 130px; flex: none; }

.blind-presets { display: flex; align-items: center; gap: 5px; font-size: calc(9px * var(--type-scale)); }
.blind-presets .chip { cursor: pointer; }
.blind-presets .chip:hover { color: var(--gold); border-color: rgba(232, 195, 122, 0.3); }

.footer-note { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.validation { display: inline-flex; align-items: center; gap: 5px; font-size: calc(10px * var(--type-scale)); }
.validation.ok { color: var(--emerald); }
.validation.error { color: var(--red); }
.footer-hint { font-size: calc(8.5px * var(--type-scale)); color: var(--faint); }
.footer-actions { display: flex; align-items: center; gap: 8px; flex: none; }
.start-btn { min-width: 128px; }

@media (max-width: 980px) {
  .seat-grid { grid-template-columns: minmax(0, 1fr); }
  .rules-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}

@media (max-width: 640px) {
  .provider-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .rules-grid { grid-template-columns: minmax(0, 1fr); }
  .param-grid { grid-template-columns: minmax(0, 1fr); }
  .param.span-2 { grid-column: span 1; }
  .preset-hint { display: none; }
  .footer-note { display: none; }
  .footer-actions { width: 100%; justify-content: flex-end; }
}
</style>
