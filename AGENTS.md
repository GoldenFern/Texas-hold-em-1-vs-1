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
cd frontend && npm test
python scripts/train_rlcard.py --episodes 50000 --chips 1000 --reward-scale 0.01
python scripts/train_nfsp.py --episodes 50000 --chips 1000 --reward-scale 0.01
python scripts/train_project_bot.py --algorithm dqn --hands 100000 --opponent mix
python scripts/eval_project_bots.py --hands 2000
python scripts/eval_bots.py --hands 2000 --allow-untrained   # 离线 Bot 评测；缺模型时用未训练网络占位
python scripts/eval_llm_vs_bot.py --hands 25 --workers 25   # LLM vs Project DQN，25 手并发
python scripts/eval_llm_vs_llm.py --hands 100 --workers 5  # LLM vs LLM 工具使用对照，100 手 5 并发
```

`.env` 从 `.env.example` 复制；**不要读取或打印真实 Key**，只检查环境变量是否存在。

离线评测不启动 Flask/SocketIO，直接驱动 `HeadsUpGame` + `RLCardBot`，结果写到 `tmp/bot_eval/`。默认 1000 筹码、5/10 盲注、每手重置筹码、同牌换座；`--hands` 是每个组合的手数。没有 DQN/NFSP/DMC checkpoint 时，不加 `--allow-untrained` 会直接报错，避免静默退化成 check/call。

## 架构分层

| 层 | 目录 | 职责 |
|---|---|---|
| 引擎 | `src/engine/` | Card/Deck、Treys 评估、双人状态机、动作校验 |
| 分析 | `src/analysis/` | 最终牌型分布、单挑 equity、静态跟注 EV；翻牌后精确枚举，翻前蒙特卡洛，LRU 缓存 |
| Agent | `src/agents/` | `AgentView`（公平信息）、RLCard 适配器、`ProjectBot`（73 维 / 10 动作） |
| 训练 | `src/training/` | `ProjectHeadsUpEnv`（本项目规则）、73 维观测、10 动作抽象、终端奖励 |
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
- 对手已经全下时不能再加注，只能跟注或弃牌；`ALL_IN` 只在能覆盖跟注时作为全下跟注。
- `start_hand` 只能在 `WAITING` 或 `FINISHED` 调用，进行中调用直接报错。
- `ActionType.BET` 与 `RAISE` 按 `to_call` 区分：`to_call == 0` 用 BET，否则用 RAISE。大盲选项虽然 `current_bet > 0`，仍然用 BET。
- 牌局结束后 `current_bet` 与双方 `street_bet` 归零，`total_bet` 保留给结果。
- `pot` 属性在牌局结束后返回有效底池（`_final_pot`），不是 `total_bet` 之和。
- 筹码守恒：活跃阶段 `sum(chips) + sum(total_bet)` 恒定；结束后 `sum(chips)` 恒定（自动重购会注入新筹码）。
- `start_hand` 前若有人筹码为 0：auto_rebuy 则补回，否则 match_over。

`tests/test_engine.py` 是规则回归安全网，改引擎必须跑。

> **与 RLCard 的规则差异**：RLCard 的 `no-limit-holdem` 在双人局翻后让按钮/小盲先行动，本项目按标准单挑规则让大盲先行动。RLCard checkpoint 直接放进本项目会有位置和行动顺序偏差；比较 RLCard 算法时不要把它在本项目引擎里的胜率当成绝对强弱。

## 项目规则 RL Bot

- `src/training/env.py` 的 `ProjectHeadsUpEnv` 包装 `HeadsUpGame`，不复制规则。
- 观测 73 维（`src/training/obs.py`），动作 10 槽（`src/training/actions.py`）。
- 动作掩码只使用引擎的 `legal_actions`、`min_raise_to`、`max_raise_to`；低于最小加注的尺寸屏蔽，不夹取。
- 训练：`scripts/train_project_bot.py`；离线评估：`scripts/eval_project_bots.py`。
- 模型格式 `project-bot-v1`，`ProjectBot` 用 `weights_only=True` 加载；旧的 `RLCardBot` 仍是 54 维 / 5 动作。
- 评估使用同牌换座，按 deal 配对；2000 手只适合粗筛。

## LLM Agent 契约

- 每次行动：追加一条 `【牌局更新】` 用户消息，保留完整历史。
- 工具：
  1. `estimate_hand_probabilities(iterations?)`：返回最终牌型分布（不是胜率）
  2. `estimate_equity()`：返回当前摊牌 equity（`胜率 + 平局率/2`），默认对手随机手牌；河牌/转牌精确枚举，翻牌/翻前 20000 次蒙特卡洛
  3. `get_random_number()`：返回 `[0, 1)` 均匀随机数；只在分析后仍难以取舍时用于打破僵局，每次调用重新抽取，不能替代估算
  4. `call_ev(equity?)`：用当前底池、跟注额和筹码计算静态跟注 EV；`equity` 省略时自动估算；短码全下用有效底池 `P + 2C - T` 扣除退款
  5. `apply_action(action, amount?)`：`amount` 是 raise-to 总额
- 每回合必须调用 `apply_action` 恰好一次；非法则把错误 + 约束 + 合法动作作为 tool 消息回传，让模型重试。
- 估算工具是可选辅助，默认不强制（`require_estimate_first: false`）。系统提示词和工具描述只说明用途与可选性，不引导也不劝阻调用，由模型自行判断。打开开关后只会在每条街第一次 `apply_action` 前软提醒一次，不计入三次失败；`get_random_number` 不能满足这个开关。
- 估算工具单回合默认最多 2 次、总采样 50000 次；equity 工具同样默认 2 次、50000 次（`config/game.json`），避免阻塞游戏线程。`call_ev` 内部估算也计入 equity 预算。
- `LLMAgent` 支持按座位传入 `tools` 与 `system_prompt`；`select_tools` 只暴露指定工具，未暴露的工具调用会按未知工具拒绝。`require_estimate_each_turn` 要求每次行动前估算一次，`skip_estimate_on_complete_board` 让河牌圈免于该要求。
- `scripts/eval_llm_vs_llm.py` 做 LLM 对 LLM 的工具使用对照：对照组只有 `apply_action`，工具组按 `ALL_TOOL_NAMES` 提供牌型分布/胜率/随机/跟注 EV/行动，并要求河牌前逐回合估算；输出在 `tmp/llm_vs_llm/`。
- 上下文预算会计入 `reasoning_content`、工具名、参数和 tool id；每轮模型调用前都会重新检查并裁剪。
- `LLMAgent.cancel()` 会设置取消标记，客户端在每次流式读取前检查，旧比赛不会继续烧模型调用。
- 厂商 4xx 文本在进入 `LLMError` 前会脱敏，`last_error`、状态快照和 `/api/llm/test` 都不能包含 Key。
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
- Socket.IO 客户端必须 polling 优先（`transports: ["polling", "websocket"]`）。直连 WebSocket 时 engine.io 在连接结束返回空迭代器且不调用 `start_response`，Werkzeug 3.1 会报 `write() before start_response`。

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
- `AgentTurn` 底部的阶段文案由 `turnPhase.ts` 根据最后一条 message 与 tool 状态推导；不要再用 `turn.status === "running"` 当作“等待工具调用”。
- 工具卡片默认展开；估算工具显示迷你分布条，随机数工具显示数值和 0 到 1 刻度，动作工具显示动作徽章/错误约束。
- 响应式断点：1520px 行动区换行，1180px 控制台移到下方；720px 牌桌/表单单列。
- 720px 以下 `.felt` 必须有确定高度，不能只给 `min-height` 再让 `.felt-inner` 使用 `height: 100%`。
- 全局快捷键必须忽略 `ctrlKey/metaKey/altKey/repeat`，否则 Ctrl+A/F/C 会误触发全下、弃牌、跟注。
- `frontend/src/store.ts` 是唯一 socket 入口；组件不要直接 `io()` 或 `fetch` 游戏接口（`api.ts` 除外）。
- 字号统一由 `:root` 的 `--type-scale` 缩放（当前 1.2）；新增 `font-size` 必须写成 `calc(Npx * var(--type-scale))`，不要写死 px，否则会绕过整体缩放。
- 类型检查必须通过 `npm run typecheck`；构建必须通过 `npm run build`。

## 测试 / 质量

- 引擎随机 60 手筹码守恒在 `tests/test_engine.py`。
- 牌型分布精确/蒙特卡洛在 `tests/test_analysis.py`；equity 与 `call_ev` 在 `tests/test_equity.py`、`tests/test_pot_odds.py`。
- LLM vs Bot 离线评测：`scripts/eval_llm_vs_bot.py`，每手独立、可并发、同牌换座，输出到 `tmp/llm_eval/`。默认对手是 `nlh_dqn_project.pth`（20k 手矩阵里最强的项目 Bot）。
- LLM 工具循环用 `FakeClient`，不要依赖真实 API Key。
- LLM 客户端流式解析、脱敏、降级、断线和取消在 `tests/test_llm_client.py`，不发起真实请求。
- 服务器建局原子性、规则校验、JSON 类型在 `tests/test_server_session.py` 与 `tests/test_server_api.py`。
- 前端单元测试在 `frontend/src/**/*.test.ts`，用 `cd frontend && npm test` 运行。
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
- 可视化保留牌型分布，加上胜率/跟注 EV 工具卡；对手范围推断留到后续。equity 默认对随机手牌，结果必须标明这个假设。
- RLCard 默认 RandomAgent；模型需要用户训练。短训练 DQN 可能退化为全下，不要把它当作“强 Bot”。
- 不做人类 vs 人类；UI 会阻止。
- 前端构建产物不入库，`start.bat` / README 会提示构建。
