---
description: Heads-Up Lab project context for future coding agents
alwaysApply: true
---

# AGENTS.md — Heads-Up Lab

## 项目背景

从多人的 `Texas-hold-em` 项目拆分出来的**单挑特化版**。多人桌的 Agent 编排、边池与可视化复杂度太高，这里只做两人无限注德州扑克，并把重点放在：

1. LLM Agent 的可观测性：思考流、工具参数、工具返回、Token/延迟
2. 单挑规则的准确性：位置、最小加注、短码全下、弃牌退款
3. 可实验性：人类 / LLM / RLCard 任选其二，规则与模型可配置
4. 一个清晰、漂亮、和旧项目完全不同的前端

旧项目：`D:\YangYifei\Code\2.LargeLanguageModel\Texas-hold-em`（只作参考，不要照搬前端）。
Agent 可视化参考：`D:\YangYifei\Code\5.Chemistry\TAGUI`（思考折叠、工具卡片、实时流）。

## 启动 / 测试

```bash
python main.py                         # http://127.0.0.1:5050
cd frontend && npm run dev             # :5173，代理到 :5050
cd frontend && npm run build           # 构建到 static/dist
python -m pytest tests -q
cd frontend && npm run typecheck
python scripts/train_rlcard.py --episodes 50000 --chips 1000 --reward-scale 0.01
```

`.env` 从 `.env.example` 复制；**不要读取或打印真实 Key**，只检查环境变量是否存在。

## 架构分层

| 层 | 目录 | 职责 |
|---|---|---|
| 引擎 | `src/engine/` | Card/Deck、Treys 评估、双人状态机、动作校验 |
| 分析 | `src/analysis/` | 最终牌型分布，翻牌后精确枚举，翻前蒙特卡洛，LRU 缓存 |
| Agent | `src/agents/` | `AgentView`（公平信息）、RLCard 适配器 |
| LLM | `src/llm/` | Provider/模型元数据、SSE 客户端、Prompt、工具循环 |
| 服务 | `src/server/` | Flask 工厂、REST、SocketIO、`GameSession` 线程与事件 |
| 前端 | `frontend/` | Vue 3 + Vite + TS；`src/store.ts` 是 socket/状态 reducer |

## 引擎不变量（改之前必读）

- 只支持 2 人；`players` 座位固定 0/1，按钮位同时是小盲。
- 翻前按钮位先行动；翻后非按钮位（大盲）先行动。
- `Action.amount` 对 BET/RAISE 是**本街总额（raise-to）**，不是增量。
- `min_raise_to = current_bet + last_full_raise`；翻前初始 `last_full_raise = big_blind`。
- 短码 all-in 加注不重开下注：`raise_allowed` 只在完整加注时对对手重置。
- 两人局没有边池；`matched = min(total_bet)`，未匹配部分退回。
- `pot` 属性在牌局结束后返回有效底池（`_final_pot`），不是 `total_bet` 之和。
- 筹码守恒：活跃阶段 `sum(chips) + sum(total_bet)` 恒定；结束后 `sum(chips)` 恒定（自动重购会注入新筹码）。
- `start_hand` 前若有人筹码为 0：auto_rebuy 则补回，否则 match_over。

`tests/test_engine.py` 是规则回归安全网，改引擎必须跑。

## LLM Agent 契约

- 每次行动：追加一条 `【牌局更新】` 用户消息，保留完整历史。
- 工具：
  1. `estimate_hand_probabilities(iterations?)`：返回最终牌型分布（不是胜率）
  2. `apply_action(action, amount?)`：`amount` 是 raise-to 总额
- 每回合必须调用 `apply_action` 恰好一次；非法则把错误 + 约束 + 合法动作作为 tool 消息回传，让模型重试。
- 每条街第一次 `apply_action` 前要求先调用一次 `estimate_hand_probabilities`；没调用会软性 nudge 一次（不计入三次失败），避免模型完全跳过第一个工具。
- 连续 3 次非法动作：call 优先，否则 fold（`_fallback` 的 `allow_check` 只用于网络错误等非动作失败）。
- 单回合最多 12 次模型调用（`config/game.json`）。
- 上下文超过预算时从最早的 `【牌局更新】` 回合边界整段删除，不拆散 assistant/tool 对。
- 厂商要求（DeepSeek/Qwen/MiniMax/Kimi/GLM）需要把 `reasoning_content` 原样回传；不要在历史里把它拼进 `content`。
- 客户端在 400 时会自动去掉可选厂商字段（`thinking`、`reasoning_effort`、`tool_stream`、`stream_options`、`preserve_thinking`、`reasoning_content`）重试一次。
- `LLMAgent` 不拥有引擎；通过 `validate_action` 回调让 session 用 `game.preview()` 在副本上校验。

## Provider 事实（2026-10）

- DeepSeek：`thinking: {type}` + `reasoning_effort: low/high/max`；思考模式工具回合必须回传 reasoning_content，否则 400。
- 火山方舟：OpenAI 兼容，`thinking: {type: enabled/disabled}`；模型 `doubao-seed-2-1-turbo-260628`。
- MiniMax M3.1 Flash：始终思考，`thinking.type` 只接受 adaptive；忽略 temperature/top_p；有 `reasoning_effort`。
- Qwen3.8 Flash：`enable_thinking`（extra_body），`preserve_thinking` 默认 true；思考模式 temperature 低于 0.6 会被抬高。
- GLM-5.3 Flash：思考常开，`reasoning_effort` low/high/max，推荐 temperature 1.0、top_p 0.95；支持 `tool_stream`。
- Kimi K2.7 Code：强制思考，262K 上下文，`reasoning_content` 保留；不要发送不支持的 thinking 参数。
- LongCat 2.5 Preview：`thinking: {type}`，思考开启时 temperature 固定 1.0，128K 输出上限。
- 默认 `max_tokens=32768`；不要改回 3000 这种保守值。UI 控件范围来自 `config/providers.json`。

新增/修改 Provider：改 `config/providers.json` 的 `request` 与 `controls`，然后在 `src/llm/providers.py` 的 `build_payload` 里确认映射。前端会自动渲染参数控件。

## 状态同步（容易踩坑）

- 服务端每次广播 state 前递增 `state_seq`，快照带 `seq`。
- REST 变更接口（new/action/next-hand/god-view）和 Socket 广播可能乱序到达；前端 `applySnapshot` 会丢弃同 match 下 `seq` 更小的快照。
- `nextHand()` 在请求前关闭旧结果弹窗，不能在响应后强制关闭，否则新一手瞬间结束时会把新结果弹窗吞掉。
- Agent 事件按 `(match_id, seat, event_id)` 去重；`eventBuffer` 在 `startMatch` 期间缓冲，`refreshTranscripts` 后重放。
- 前端 transcript 只做增量 reducer；不要在每个 delta 上做全量 JSON 深拷贝/排序。

## 前端约定

- 设计语言：深翡翠 + 暖金 + 象牙白；牌桌是椭圆绒面，Agent 控制台是深色“观测站”。
- 不使用旧项目的布局/配色；不用 `v-html`。
- 主要组件：
  - `TableView` + `SeatPod` + `PlayingCard` + `DeckStrip`
  - `ActionBar`（人类行动、快捷下注、快捷键 F/C/R/A）
  - `AgentConsole` + `AgentTurn` + `ReasoningBlock` + `ToolCard`
  - `AnalysisPanel`（唯一可视化：最终牌型分布）
  - `SetupModal`（Provider/模型/参数/规则）
  - `ResultOverlay`、`ToastHost`
- 思考块在正文开始后自动折叠；用户手动展开后不再自动折叠。
- `turn_state` 事件保存发给模型的原样牌局输入，在 `AgentTurn` 的「牌局输入」折叠块中可查。
- 工具卡片默认展开；估算工具显示迷你分布条，动作工具显示动作徽章/错误约束。
- 响应式断点：1180px 控制台移到下方；720px 牌桌/表单单列。
- `frontend/src/store.ts` 是唯一 socket 入口；组件不要直接 `io()` 或 `fetch` 游戏接口（`api.ts` 除外）。
- 字号统一由 `:root` 的 `--type-scale` 缩放（当前 1.2）；新增 `font-size` 必须写成 `calc(Npx * var(--type-scale))`，不要写死 px，否则会绕过整体缩放。
- 类型检查必须通过 `npm run typecheck`；构建必须通过 `npm run build`。

## 测试 / 质量

- 引擎随机 60 手筹码守恒在 `tests/test_engine.py`。
- 牌型分布精确/蒙特卡洛在 `tests/test_analysis.py`。
- LLM 工具循环用 `FakeClient`，不要依赖真实 API Key。
- RLCard 适配器测试会构造 `AgentView` 并重复决策，断言动作合法。
- 真实 Provider 连通性用 UI 的「测试连接」，不要在测试里发真实请求。

## 安全 / 数据

- API Key 只在 `.env`，不写入代码、不返回前端、不写日志。
- `.env`、`.env.local`、模型文件、`static/dist` 已在 `.gitignore`。
- 默认 `127.0.0.1:5050`；没有鉴权，不要公网暴露。
- 删除文件优先 `trash`，不要用 `rm`。
- 模型文件是 torch pickle，只加载可信来源。

## 当前取舍

- 单桌、内存态；不做账号/持久化/多房间。
- 只做牌型分布一个可视化；胜率/EV/范围推断留到后续。
- RLCard 默认 RandomAgent；模型需要用户训练。短训练 DQN 可能退化为全下，不要把它当作“强 Bot”。
- 不做人类 vs 人类；UI 会阻止。
- 前端构建产物不入库，`start.bat` / README 会提示构建。
