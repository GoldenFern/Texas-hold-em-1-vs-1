"""The tool-using LLM poker agent."""

from __future__ import annotations

import json
import math
import random
import threading
import time
import uuid
from typing import Callable, List, Optional, Sequence

from src.agents.base import AgentView, BaseAgent
from src.analysis.equity import (
    DEFAULT_ITERATIONS as DEFAULT_EQUITY_ITERATIONS,
    MIN_ITERATIONS as MIN_EQUITY_ITERATIONS,
    EquityAnalyzer,
    EquityResult,
)
from src.analysis.hand_distribution import HandDistributionAnalyzer
from src.analysis.pot_odds import analyze_call
from src.engine.actions import Action, ActionType
from src.engine.cards import Card

from .client import LLMClient, LLMError, ToolCall, estimate_tokens
from .prompt import (
    SYSTEM_PROMPT,
    build_hand_result_message,
    build_nudge_message,
    build_turn_message,
)
from .providers import ModelSpec

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "estimate_hand_probabilities",
            "description": (
                "根据你的两张底牌和当前公共牌，估算你最终最佳五张牌分别成为"
                "皇家同花顺/同花顺/四条/葫芦/同花/顺子/三条/两对/一对/高牌的概率。"
                "这是牌型分布，不是胜率。是否调用由你自行判断。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "iterations": {
                        "type": "integer",
                        "minimum": 2000,
                        "maximum": 50000,
                        "description": "蒙特卡洛采样次数；翻牌前建议 20000-50000，翻牌后引擎会自动精确枚举。单回合总采样有上限。",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "estimate_equity",
            "description": (
                "估算当前牌局的摊牌 equity。对手按剩余牌均匀随机；"
                "河牌和转牌精确枚举，翻牌和翻前蒙特卡洛。"
                "返回赢/平/输概率，equity = 胜率 + 平局率/2。不需要参数。"
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_random_number",
            "description": (
                "返回一个 [0, 1) 区间的均匀随机数。"
                "只有在你完成理性分析后，几种选择的期望仍然非常接近、确实拿不准时，"
                "才可以用它来打破僵局。这只是备选方案，不要用它代替对牌局、底池赔率、"
                "位置和对手范围的分析，也不要为了追求某个数字反复调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "call_ev",
            "description": (
                "计算当前跟注的静态期望收益。底池、跟注额和筹码自动取当前牌局；"
                "equity 可省略，省略时自动按当前牌局估算。"
                "返回跟注 EV、盈亏平衡胜率和有效底池。"
                "假设跟注后不再下注，翻牌和转牌还要考虑隐含赔率。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "equity": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 1,
                        "description": "可选。0 到 1 的小数；省略时自动估算。",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_action",
            "description": (
                "提交你的最终动作。每回合必须调用恰好一次。"
                "amount 是动作完成后你在本街的总额（raise-to），不是增量。"
                "fold/check/call/all_in 不需要 amount。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["fold", "check", "call", "bet", "raise", "all_in"],
                        "description": "你的动作。",
                    },
                    "amount": {
                        "type": "integer",
                        "description": "仅 bet/raise 需要：动作完成后你在本街的总投入（总额，不是增量）。",
                    },
                },
                "required": ["action"],
            },
        },
    },
]

_ACTION_ALIASES = {
    "x": "check",
    "pass": "check",
    "check": "check",
    "call": "call",
    "fold": "fold",
    "bet": "bet",
    "raise": "raise",
    "allin": "all_in",
    "all-in": "all_in",
    "all_in": "all_in",
    "jam": "all_in",
}


class EquityBudgetError(RuntimeError):
    """Raised when the per-turn equity budget is exhausted."""


def select_tools(names: Sequence[str]) -> List[dict]:
    """Return the tool schemas for the requested tool names.

    Args:
        names: Function names to expose to the model.

    Returns:
        A list of tool schemas in the canonical order.

    Raises:
        ValueError: If a requested tool name is unknown.
    """
    wanted = set(names)
    known = {tool["function"]["name"] for tool in TOOLS}
    unknown = wanted.difference(known)
    if unknown:
        raise ValueError(f"未知工具：{', '.join(sorted(unknown))}")
    return [tool for tool in TOOLS if tool["function"]["name"] in wanted]


class LLMAgent(BaseAgent):
    """An agent that keeps a full conversation and decides through tools."""

    kind = "llm"

    def __init__(
        self,
        seat: int,
        name: str,
        spec: ModelSpec,
        params: dict,
        client: LLMClient,
        analyzer: HandDistributionAnalyzer,
        validate_action: Callable[[Action], Optional[str]],
        runtime: dict,
        tools: Optional[Sequence[dict]] = None,
        system_prompt: Optional[str] = None,
        equity_analyzer: Optional[EquityAnalyzer] = None,
    ) -> None:
        self.seat = seat
        self.name = name
        self.spec = spec
        self.params = spec.normalize_params(params)
        self.client = client
        self.analyzer = analyzer
        self.equity_analyzer = equity_analyzer or EquityAnalyzer()
        self.validate_action = validate_action
        self.max_steps = int(runtime.get("max_steps_per_turn", 12))
        self.max_failures = int(runtime.get("max_consecutive_failures", 3))
        self.require_estimate_first = bool(runtime.get("require_estimate_first", False))
        self.require_estimate_each_turn = bool(
            runtime.get("require_estimate_each_turn", False)
        )
        self.skip_estimate_on_complete_board = bool(
            runtime.get("skip_estimate_on_complete_board", False)
        )
        runtime_budget = int(runtime.get("max_context_tokens_estimate", 220000))
        reserved_output = int(self.params.get("max_tokens", 32768))
        context_window = int(getattr(spec, "context_window", 0) or 0)
        if context_window > 0:
            runtime_budget = min(runtime_budget, context_window - reserved_output - 2048)
        self.max_context_tokens = max(8000, runtime_budget)
        self.max_estimate_calls = int(runtime.get("max_estimate_calls_per_turn", 2))
        self.max_estimate_iterations = int(runtime.get("max_estimate_iterations", 50000))
        self.max_equity_calls = int(runtime.get("max_equity_calls_per_turn", 2))
        self.max_equity_iterations = int(runtime.get("max_equity_iterations", 50000))
        self.tools = list(tools) if tools is not None else list(TOOLS)
        self.tool_names = {tool["function"]["name"] for tool in self.tools}
        if self.require_estimate_each_turn and "estimate_hand_probabilities" not in self.tool_names:
            raise ValueError("启用每回合估算要求时必须提供 estimate_hand_probabilities 工具")
        self.system_prompt = system_prompt or SYSTEM_PROMPT
        self.messages: List[dict] = [{"role": "system", "content": self.system_prompt}]
        self.turn_count = 0
        self._estimated_keys: set[tuple[int, str]] = set()
        self._nudged_keys: set[tuple[int, str]] = set()
        self._estimated_this_turn = False
        self._estimate_calls_this_turn = 0
        self._estimate_iterations_this_turn = 0
        self._equity_calls_this_turn = 0
        self._equity_iterations_this_turn = 0
        self._tools_tokens = estimate_tokens(json.dumps(self.tools, ensure_ascii=False))
        self.total_usage = {"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0}
        self.last_error = ""
        self._trimmed = False
        self._cancel_event = threading.Event()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def reset(self) -> None:
        self.messages = [{"role": "system", "content": self.system_prompt}]
        self.turn_count = 0
        self.total_usage = {"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0}
        self.last_error = ""
        self._trimmed = False
        self._estimated_keys.clear()
        self._nudged_keys.clear()
        self._estimated_this_turn = False
        self._estimate_calls_this_turn = 0
        self._estimate_iterations_this_turn = 0
        self._equity_calls_this_turn = 0
        self._equity_iterations_this_turn = 0
        self._cancel_event.clear()

    def cancel(self) -> None:
        """Ask an in-flight decision loop to stop making model calls."""
        self._cancel_event.set()

    def on_hand_finished(self, result: Optional[dict]) -> None:
        if not result:
            return
        text = build_hand_result_message(result, self.seat, self.name)
        self.messages.append({"role": "user", "content": text})

    def describe(self) -> dict:
        return {
            "kind": self.kind,
            "name": self.name,
            "provider_id": self.spec.provider_id,
            "provider_name": self.spec.provider_name,
            "model": self.spec.id,
            "model_name": self.spec.name,
            "params": dict(self.params),
            "turns": self.turn_count,
            "usage": dict(self.total_usage),
            "last_error": self.last_error,
        }

    # ------------------------------------------------------------------
    # Decision loop
    # ------------------------------------------------------------------

    def decide(self, view: AgentView, emit) -> Action:
        turn_id = uuid.uuid4().hex[:12]
        started = time.perf_counter()
        usage_total = {"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0}
        steps = 0
        failures = 0
        self.turn_count += 1
        self._estimated_this_turn = False
        self._estimate_calls_this_turn = 0
        self._estimate_iterations_this_turn = 0
        self._equity_calls_this_turn = 0
        self._equity_iterations_this_turn = 0
        if self._cancel_event.is_set():
            return Action(view.seat, ActionType.FOLD, source="llm", auto=True)

        emit(
            {
                "type": "turn_start",
                "turn_id": turn_id,
                "hand_id": view.hand_id,
                "hand_number": view.hand_number,
                "street": view.street,
                "pot": view.pot,
                "my_chips": view.my_chips,
                "opponent_chips": view.opponent_chips,
                "to_call": view.to_call,
                "legal_actions": view.legal_actions,
                "position": "button" if view.is_button else "big_blind",
            }
        )
        turn_message = build_turn_message(view)
        self.messages.append({"role": "user", "content": turn_message})
        emit({"type": "turn_state", "turn_id": turn_id, "text": turn_message})
        self._trim_context(emit)

        while steps < self.max_steps:
            if self._cancel_event.is_set():
                return Action(view.seat, ActionType.FOLD, source="llm", auto=True)
            steps += 1
            self._trim_context(emit)
            message_id = uuid.uuid4().hex[:12]
            emit(
                {
                    "type": "message_start",
                    "turn_id": turn_id,
                    "message_id": message_id,
                    "step": steps,
                }
            )
            emit(
                {
                    "type": "llm_request",
                    "turn_id": turn_id,
                    "message_id": message_id,
                    "step": steps,
                    "message_count": len(self.messages),
                    "estimated_context_tokens": self._context_tokens(),
                }
            )

            def on_stream(event: dict) -> None:
                event["turn_id"] = turn_id
                event["message_id"] = message_id
                emit(event)

            try:
                result = self.client.stream(
                    self.spec,
                    self.messages,
                    self.tools,
                    self.params,
                    on_stream,
                    cancel=self._cancel_event.is_set,
                )
                self.last_error = ""
            except LLMError as exc:
                if exc.kind == "cancelled":
                    return Action(view.seat, ActionType.FOLD, source="llm", auto=True)
                if exc.kind == "context_length":
                    before = len(self.messages)
                    self._trim_context(emit, force=True)
                    if len(self.messages) < before and steps < self.max_steps:
                        self.last_error = str(exc)
                        emit(
                            {
                                "type": "message_end",
                                "turn_id": turn_id,
                                "message_id": message_id,
                                "error": True,
                                "content": "",
                                "reasoning": "",
                                "tool_calls": [],
                                "estimated_usage": False,
                            }
                        )
                        continue
                self.last_error = str(exc)
                emit(
                    {
                        "type": "error",
                        "turn_id": turn_id,
                        "message_id": message_id,
                        "kind": exc.kind,
                        "text": exc.detail,
                    }
                )
                emit(
                    {
                        "type": "message_end",
                        "turn_id": turn_id,
                        "message_id": message_id,
                        "error": True,
                        "content": "",
                        "reasoning": "",
                        "tool_calls": [],
                    }
                )
                return self._fallback(
                    view,
                    emit,
                    turn_id,
                    started,
                    steps,
                    usage_total,
                    reason=f"LLM 调用失败（{exc.kind}）",
                    allow_check=True,
                )
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"{type(exc).__name__}: {exc}"
                emit(
                    {
                        "type": "error",
                        "turn_id": turn_id,
                        "message_id": message_id,
                        "kind": "unknown",
                        "text": self.last_error,
                    }
                )
                emit(
                    {
                        "type": "message_end",
                        "turn_id": turn_id,
                        "message_id": message_id,
                        "error": True,
                        "content": "",
                        "reasoning": "",
                        "tool_calls": [],
                    }
                )
                return self._fallback(
                    view,
                    emit,
                    turn_id,
                    started,
                    steps,
                    usage_total,
                    reason="LLM 调用异常",
                    allow_check=True,
                )

            for key in usage_total:
                usage_total[key] += int(result.usage.get(key, 0))
            self.total_usage["input_tokens"] += int(result.usage.get("input_tokens", 0))
            self.total_usage["output_tokens"] += int(result.usage.get("output_tokens", 0))
            self.total_usage["reasoning_tokens"] += int(result.usage.get("reasoning_tokens", 0))

            emit(
                {
                    "type": "message_end",
                    "turn_id": turn_id,
                    "message_id": message_id,
                    "content": result.content,
                    "reasoning": result.reasoning,
                    "finish_reason": result.finish_reason,
                    "usage": dict(result.usage),
                    "estimated_usage": bool(result.estimated_usage),
                    "latency_ms": round(result.latency_ms, 1),
                    "tool_calls": [
                        {
                            "index": index,
                            "call_id": call.id,
                            "name": call.name,
                            "arguments": call.arguments,
                        }
                        for index, call in enumerate(result.tool_calls)
                    ],
                }
            )

            self.messages.append(
                result.assistant_message(bool(self.spec.request.get("reasoning_echo", False)))
            )

            if not result.tool_calls:
                failures += 1
                emit(
                    {
                        "type": "note",
                        "level": "warn",
                        "turn_id": turn_id,
                        "text": f"模型没有调用工具（{failures}/{self.max_failures}），已提醒它提交 apply_action。",
                    }
                )
                if failures >= self.max_failures:
                    return self._fallback(
                        view,
                        emit,
                        turn_id,
                        started,
                        steps,
                        usage_total,
                        reason="连续多次没有提交动作",
                    )
                self.messages.append({"role": "user", "content": build_nudge_message()})
                continue

            chosen: Optional[Action] = None
            for index, call in enumerate(result.tool_calls):
                if chosen is not None:
                    self._append_tool_error(
                        call,
                        "本回合已经提交了动作，后续工具调用被忽略。",
                        emit,
                        turn_id,
                        index,
                        ignored=True,
                    )
                    continue
                tool_started = time.perf_counter()
                parsed = call.parsed_arguments()
                emit(
                    {
                        "type": "tool_call",
                        "turn_id": turn_id,
                        "message_id": message_id,
                        "index": index,
                        "call_id": call.id,
                        "name": call.name,
                        "arguments": call.arguments,
                        "parsed": parsed,
                        "parse_error": parsed is None,
                    }
                )

                if call.name not in self.tool_names:
                    failures += 1
                    available = "、".join(sorted(self.tool_names))
                    self._append_tool_error(
                        call,
                        f"未知工具 {call.name!r}。本回合可用的工具：{available}。",
                        emit,
                        turn_id,
                        index,
                    )
                    if failures >= self.max_failures:
                        return self._fallback(
                            view,
                            emit,
                            turn_id,
                            started,
                            steps,
                            usage_total,
                            reason="连续三次无效动作",
                        )
                    continue

                if call.name == "estimate_hand_probabilities":
                    if not self._handle_estimate(view, call, parsed, emit, turn_id, index, tool_started):
                        failures += 1
                        if failures >= self.max_failures:
                            return self._fallback(
                                view,
                                emit,
                                turn_id,
                                started,
                                steps,
                                usage_total,
                                reason="连续三次无效动作",
                            )
                    continue

                if call.name == "estimate_equity":
                    if not self._handle_equity(view, call, parsed, emit, turn_id, index, tool_started):
                        failures += 1
                        if failures >= self.max_failures:
                            return self._fallback(
                                view,
                                emit,
                                turn_id,
                                started,
                                steps,
                                usage_total,
                                reason="连续三次无效动作",
                            )
                    continue

                if call.name == "call_ev":
                    if not self._handle_call_ev(view, call, parsed, emit, turn_id, index, tool_started):
                        failures += 1
                        if failures >= self.max_failures:
                            return self._fallback(
                                view,
                                emit,
                                turn_id,
                                started,
                                steps,
                                usage_total,
                                reason="连续三次无效动作",
                            )
                    continue

                if call.name == "get_random_number":
                    if not self._handle_random(call, parsed, emit, turn_id, index, tool_started):
                        failures += 1
                        if failures >= self.max_failures:
                            return self._fallback(
                                view,
                                emit,
                                turn_id,
                                started,
                                steps,
                                usage_total,
                                reason="连续三次无效动作",
                            )
                    continue

                if call.name == "apply_action":
                    if parsed is None:
                        failures += 1
                        self._append_tool_error(
                            call,
                            f"工具参数不是合法 JSON：{call.arguments[:300]}",
                            emit,
                            turn_id,
                            index,
                        )
                        if failures >= self.max_failures:
                            return self._fallback(
                                view,
                                emit,
                                turn_id,
                                started,
                                steps,
                                usage_total,
                                reason="连续三次无效动作",
                            )
                        continue

                    street_key = (view.hand_id, view.street)
                    board_complete = len(view.community) >= 5
                    requires_estimate = False
                    if "estimate_hand_probabilities" in self.tool_names:
                        if self.require_estimate_each_turn:
                            requires_estimate = not self._estimated_this_turn and not (
                                self.skip_estimate_on_complete_board and board_complete
                            )
                        elif self.require_estimate_first:
                            requires_estimate = (
                                street_key not in self._estimated_keys
                                and street_key not in self._nudged_keys
                                and not (
                                    self.skip_estimate_on_complete_board
                                    and board_complete
                                )
                            )
                    if requires_estimate:
                        if self.require_estimate_each_turn:
                            message = (
                                "本回合在调用 apply_action 之前，请先调用 "
                                "estimate_hand_probabilities 至少一次；河牌圈五张公共牌"
                                "全部发出后除外。"
                            )
                        else:
                            # Soft workflow requirement: one probability check per
                            # street before the first action. Only nudged once, and
                            # it does not count toward the three-failure fallback.
                            self._nudged_keys.add(street_key)
                            message = (
                                "在本街的第一次决策前，请先调用 "
                                "estimate_hand_probabilities 一次，再提交 apply_action。"
                            )
                        self._append_tool_error(
                            call,
                            message,
                            emit,
                            turn_id,
                            index,
                            extra={"required_tool": "estimate_hand_probabilities"},
                        )
                        continue

                    action, error = self._build_action(view, parsed)
                    if error:
                        failures += 1
                        self._append_tool_error(
                            call,
                            error,
                            emit,
                            turn_id,
                            index,
                            extra=self._constraint_payload(view),
                        )
                        if failures >= self.max_failures:
                            return self._fallback(
                                view,
                                emit,
                                turn_id,
                                started,
                                steps,
                                usage_total,
                                reason="连续三次无效动作",
                            )
                        continue

                    try:
                        validation_error = self.validate_action(action)
                    except Exception as exc:  # noqa: BLE001
                        validation_error = f"动作校验失败：{type(exc).__name__}: {exc}"
                    if validation_error:
                        failures += 1
                        self._append_tool_error(
                            call,
                            validation_error,
                            emit,
                            turn_id,
                            index,
                            extra=self._constraint_payload(view),
                        )
                        if failures >= self.max_failures:
                            return self._fallback(
                                view,
                                emit,
                                turn_id,
                                started,
                                steps,
                                usage_total,
                                reason="连续三次无效动作",
                            )
                        continue

                    chosen = action
                    payload = {
                        "ok": True,
                        "accepted": True,
                        "action": action.type.value,
                        "label": action.type.label,
                        "amount": action.amount,
                        "message": "动作已接受，牌局将继续。",
                    }
                    self._append_tool_result(call, payload, emit, turn_id, index, tool_started)
                    continue

            if chosen is not None:
                emit(
                    {
                        "type": "turn_end",
                        "turn_id": turn_id,
                        "action": chosen.to_dict(),
                        "auto": False,
                        "steps": steps,
                        "usage": usage_total,
                        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                    }
                )
                return chosen

        return self._fallback(
            view,
            emit,
            turn_id,
            started,
            steps,
            usage_total,
            reason=f"单回合超过 {self.max_steps} 次模型调用",
        )

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    def _handle_estimate(
        self,
        view: AgentView,
        call: ToolCall,
        parsed: Optional[dict],
        emit,
        turn_id: str,
        index: int,
        started: float,
    ) -> bool:
        if parsed is None or not isinstance(parsed, dict):
            self._append_tool_error(
                call,
                f"工具参数不是合法 JSON：{call.arguments[:300]}",
                emit,
                turn_id,
                index,
            )
            return False
        if self._estimate_calls_this_turn >= self.max_estimate_calls:
            self._append_tool_error(
                call,
                f"本回合调用 estimate_hand_probabilities 的次数已达上限 "
                f"（{self.max_estimate_calls} 次），请直接提交 apply_action。",
                emit,
                turn_id,
                index,
            )
            return False
        remaining = self.max_estimate_iterations - self._estimate_iterations_this_turn
        if remaining <= 0:
            self._append_tool_error(
                call,
                "本回合的估算采样预算已用完，请直接提交 apply_action。",
                emit,
                turn_id,
                index,
            )
            return False
        if remaining < 2000:
            self._append_tool_error(
                call,
                "本回合剩余估算采样次数不足，请直接提交 apply_action。",
                emit,
                turn_id,
                index,
            )
            return False
        iterations = parsed.get("iterations", 30000)
        try:
            iterations = int(iterations)
        except (TypeError, ValueError):
            iterations = 30000
        iterations = max(2000, min(iterations, remaining))
        self._estimate_calls_this_turn += 1
        try:
            hole = [Card.from_str(code) for code in view.hole_cards]
            community = [Card.from_str(code) for code in view.community]
            result = self.analyzer.analyze(
                hole,
                community,
                hero_seat=view.seat,
                iterations=iterations,
            )
        except Exception as exc:  # noqa: BLE001
            self._append_tool_error(
                call,
                f"牌型概率计算失败：{exc}",
                emit,
                turn_id,
                index,
            )
            return True

        self._estimate_iterations_this_turn += iterations
        self._estimated_this_turn = True
        self._estimated_keys.add((view.hand_id, view.street))
        payload = {"ok": True, **result.to_llm_dict()}
        top = sorted(
            (item for item in result.distribution if item["probability"] > 0),
            key=lambda item: item["probability"],
            reverse=True,
        )[:4]
        summary = " · ".join(f"{item['name']} {item['probability'] * 100:.1f}%" for item in top)
        self._append_tool_result(
            call,
            payload,
            emit,
            turn_id,
            index,
            started,
            ui_result={"ok": True, **result.to_dict()},
            summary=summary,
        )
        return True

    def _handle_equity(
        self,
        view: AgentView,
        call: ToolCall,
        parsed: Optional[dict],
        emit,
        turn_id: str,
        index: int,
        started: float,
    ) -> bool:
        if parsed is None or not isinstance(parsed, dict):
            self._append_tool_error(
                call,
                f"工具参数不是合法 JSON：{call.arguments[:300]}",
                emit,
                turn_id,
                index,
            )
            return False
        try:
            result = self._compute_equity(view)
        except EquityBudgetError as exc:
            self._append_tool_error(call, str(exc), emit, turn_id, index)
            return False
        except Exception as exc:  # noqa: BLE001
            self._append_tool_error(
                call,
                f"胜率计算失败：{exc}",
                emit,
                turn_id,
                index,
            )
            return True

        payload = {"ok": True, "street": view.street, **result.to_llm_dict()}
        summary = (
            f"equity {result.equity * 100:.1f}% · "
            f"胜 {result.win_rate * 100:.1f}% / 平 {result.tie_rate * 100:.1f}%"
        )
        self._append_tool_result(
            call,
            payload,
            emit,
            turn_id,
            index,
            started,
            ui_result={"ok": True, "street": view.street, **result.to_dict()},
            summary=summary,
        )
        return True

    def _handle_call_ev(
        self,
        view: AgentView,
        call: ToolCall,
        parsed: Optional[dict],
        emit,
        turn_id: str,
        index: int,
        started: float,
    ) -> bool:
        if parsed is None or not isinstance(parsed, dict):
            self._append_tool_error(
                call,
                f"工具参数不是合法 JSON：{call.arguments[:300]}",
                emit,
                turn_id,
                index,
            )
            return False

        if view.to_call <= 0 or view.call_amount <= 0 or "call" not in view.legal_actions:
            if view.to_call <= 0:
                reason = "当前 to_call=0，跟注不合法；请使用 check 或 bet。"
            elif view.call_amount <= 0:
                reason = "你没有可跟注的筹码；请选择 fold 或 all_in。"
            else:
                reason = "当前行动不包含 call；请查看合法动作。"
            payload = {
                "ok": True,
                "applicable": False,
                "reason": reason,
                "street": view.street,
                "pot": view.pot,
                "to_call": view.to_call,
                "call_amount": view.call_amount,
                "legal_actions": list(view.legal_actions),
            }
            self._append_tool_result(
                call,
                payload,
                emit,
                turn_id,
                index,
                started,
                summary=reason,
            )
            return True

        equity_result: Optional[EquityResult] = None
        equity_source = "model"
        raw_equity = parsed.get("equity")
        if raw_equity is None:
            try:
                equity_result = self._compute_equity(view)
            except EquityBudgetError as exc:
                self._append_tool_error(call, str(exc), emit, turn_id, index)
                return False
            except Exception as exc:  # noqa: BLE001
                self._append_tool_error(
                    call,
                    f"胜率估算失败：{exc}",
                    emit,
                    turn_id,
                    index,
                )
                return True
            equity = equity_result.equity
            equity_source = "internal"
        else:
            try:
                equity = float(raw_equity)
            except (TypeError, ValueError):
                self._append_tool_error(
                    call,
                    "equity 必须是 0 到 1 之间的小数；例如 63.2% 请写 0.632。",
                    emit,
                    turn_id,
                    index,
                )
                return False
            if not math.isfinite(equity) or not 0.0 <= equity <= 1.0:
                self._append_tool_error(
                    call,
                    "equity 必须是 0 到 1 之间的小数；例如 63.2% 请写 0.632。",
                    emit,
                    turn_id,
                    index,
                )
                return False

        try:
            calc = analyze_call(view.pot, view.to_call, view.my_chips, equity)
        except Exception as exc:  # noqa: BLE001
            self._append_tool_error(
                call,
                f"跟注 EV 计算失败：{exc}",
                emit,
                turn_id,
                index,
            )
            return True

        payload = {
            "ok": True,
            "equity_source": equity_source,
            "street": view.street,
            **calc.to_llm_dict(),
        }
        ui_result = {
            "ok": True,
            "equity_source": equity_source,
            "street": view.street,
            **calc.to_dict(),
        }
        if equity_result is not None:
            payload.update(
                {
                    "equity_method": equity_result.method,
                    "equity_samples": equity_result.samples,
                    "equity_std_error": round(equity_result.std_error, 4),
                }
            )
            ui_result.update(
                {
                    "equity_method": equity_result.method,
                    "equity_samples": equity_result.samples,
                    "equity_std_error": equity_result.std_error,
                    "equity_cached": equity_result.cached,
                }
            )
        else:
            payload["equity_method"] = "model"
            ui_result["equity_method"] = "model"

        summary = (
            f"EV {calc.ev_call:+.1f} · 临界 {calc.breakeven_equity * 100:.1f}% · "
            f"你的 {equity * 100:.1f}%"
        )
        self._append_tool_result(
            call,
            payload,
            emit,
            turn_id,
            index,
            started,
            ui_result=ui_result,
            summary=summary,
        )
        return True

    def _compute_equity(self, view: AgentView) -> EquityResult:
        if self._equity_calls_this_turn >= self.max_equity_calls:
            raise EquityBudgetError(
                f"本回合调用 equity 的次数已达上限（{self.max_equity_calls} 次），"
                "请直接提交 apply_action。"
            )
        remaining = self.max_equity_iterations - self._equity_iterations_this_turn
        if remaining < MIN_EQUITY_ITERATIONS:
            raise EquityBudgetError(
                "本回合的 equity 采样预算已用完，请直接提交 apply_action。"
            )
        iterations = min(DEFAULT_EQUITY_ITERATIONS, remaining)
        hole = [Card.from_str(code) for code in view.hole_cards]
        community = [Card.from_str(code) for code in view.community]
        result = self.equity_analyzer.analyze(
            hole,
            community,
            hero_seat=view.seat,
            iterations=iterations,
        )
        self._equity_calls_this_turn += 1
        if not result.cached and result.method == "monte_carlo":
            self._equity_iterations_this_turn += result.samples
        return result

    def _handle_random(
        self,
        call: ToolCall,
        parsed: Optional[dict],
        emit,
        turn_id: str,
        index: int,
        started: float,
    ) -> bool:
        if parsed is None or not isinstance(parsed, dict):
            self._append_tool_error(
                call,
                f"工具参数不是合法 JSON：{call.arguments[:300]}",
                emit,
                turn_id,
                index,
            )
            return False
        value = random.random()
        payload = {"ok": True, "value": value}
        self._append_tool_result(
            call,
            payload,
            emit,
            turn_id,
            index,
            started,
            summary=f"{value:.4f}",
        )
        return True

    def _build_action(self, view: AgentView, parsed: dict) -> tuple[Optional[Action], str]:
        raw = str(parsed.get("action", "")).strip().lower()
        action_type = _ACTION_ALIASES.get(raw, raw)
        if action_type not in {item.value for item in ActionType}:
            return None, f"未知动作 {raw!r}。允许的动作：{', '.join(view.legal_actions)}"
        # The engine distinguishes an opening bet (BB option included) from a
        # raise. Accept either spelling from the model and normalize it.
        if action_type == "raise" and "raise" not in view.legal_actions and "bet" in view.legal_actions:
            action_type = "bet"
        elif action_type == "bet" and "bet" not in view.legal_actions and "raise" in view.legal_actions:
            action_type = "raise"
        if action_type not in view.legal_actions:
            return (
                None,
                f"动作 {action_type!r} 当前不合法。合法动作：{', '.join(view.legal_actions)}。"
                f"当前需要跟注 {view.to_call}，最小加注到 {view.min_raise_to}，最大到 {view.max_raise_to}。",
            )
        amount = parsed.get("amount", parsed.get("raise_to", parsed.get("to", 0)))
        try:
            return (
                Action.from_payload(
                    view.seat,
                    {"type": action_type, "amount": amount},
                    source="llm",
                ),
                "",
            )
        except ValueError as exc:
            return None, str(exc)

    def _append_tool_result(
        self,
        call: ToolCall,
        payload: dict,
        emit,
        turn_id: str,
        index: int,
        started: float,
        ui_result: Optional[dict] = None,
        summary: str = "",
    ) -> None:
        self.messages.append(
            {
                "role": "tool",
                "tool_call_id": call.id,
                "name": call.name,
                "content": json.dumps(payload, ensure_ascii=False),
            }
        )
        emit(
            {
                "type": "tool_result",
                "turn_id": turn_id,
                "index": index,
                "call_id": call.id,
                "name": call.name,
                "ok": True,
                "result": ui_result if ui_result is not None else payload,
                "summary": summary,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            }
        )

    def _append_tool_error(
        self,
        call: ToolCall,
        error: str,
        emit,
        turn_id: str,
        index: int,
        extra: Optional[dict] = None,
        ignored: bool = False,
    ) -> None:
        payload = {"ok": False, "error": error}
        if extra:
            payload.update(extra)
        self.messages.append(
            {
                "role": "tool",
                "tool_call_id": call.id,
                "name": call.name,
                "content": json.dumps(payload, ensure_ascii=False),
            }
        )
        emit(
            {
                "type": "tool_result",
                "turn_id": turn_id,
                "index": index,
                "call_id": call.id,
                "name": call.name,
                "ok": False,
                "ignored": ignored,
                "result": payload,
                "summary": error,
                "duration_ms": 0.0,
            }
        )

    @staticmethod
    def _constraint_payload(view: AgentView) -> dict:
        return {
            "legal_actions": view.legal_actions,
            "to_call": view.to_call,
            "call_amount": view.call_amount,
            "current_bet": view.current_bet,
            "your_street_bet": view.my_street_bet,
            "min_raise_to": view.min_raise_to,
            "max_raise_to": view.max_raise_to,
            "pot": view.pot,
            "your_chips": view.my_chips,
        }

    # ------------------------------------------------------------------
    # Fallback and context
    # ------------------------------------------------------------------

    def _fallback(
        self,
        view: AgentView,
        emit,
        turn_id: str,
        started: float,
        steps: int,
        usage: dict,
        reason: str,
        allow_check: bool = False,
    ) -> Action:
        legal = set(view.legal_actions)
        if "call" in legal:
            action = Action(view.seat, ActionType.CALL, source="llm", auto=True)
            note = "自动跟注"
        elif allow_check and "check" in legal:
            action = Action(view.seat, ActionType.CHECK, source="llm", auto=True)
            note = "自动过牌"
        else:
            action = Action(view.seat, ActionType.FOLD, source="llm", auto=True)
            note = "自动弃牌"
        emit(
            {
                "type": "note",
                "level": "error",
                "turn_id": turn_id,
                "text": f"{reason}，{note}。",
            }
        )
        emit(
            {
                "type": "turn_end",
                "turn_id": turn_id,
                "action": action.to_dict(),
                "auto": True,
                "steps": steps,
                "usage": usage,
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "reason": reason,
            }
        )
        return action

    def _context_tokens(self) -> int:
        total = self._tools_tokens
        for message in self.messages:
            total += 6
            content = message.get("content")
            if isinstance(content, str):
                total += estimate_tokens(content)
            reasoning = message.get("reasoning_content")
            if isinstance(reasoning, str):
                total += estimate_tokens(reasoning)
            tool_call_id = message.get("tool_call_id")
            if isinstance(tool_call_id, str):
                total += estimate_tokens(tool_call_id)
            for call in message.get("tool_calls") or []:
                total += estimate_tokens(str(call.get("id") or ""))
                function = call.get("function") or {}
                total += estimate_tokens(str(function.get("name") or ""))
                total += estimate_tokens(str(function.get("arguments") or ""))
        return total

    def _trim_context(self, emit, force: bool = False) -> None:
        removed = 0
        while self._context_tokens() > self.max_context_tokens or (force and removed == 0):
            turn_starts = [
                index
                for index, message in enumerate(self.messages)
                if message.get("role") == "user"
                and str(message.get("content", "")).startswith("【牌局更新")
            ]
            if len(turn_starts) <= 1:
                break
            # Drop the oldest complete turn, always cutting at a turn boundary.
            cut = turn_starts[1]
            removed += cut - 1
            self.messages = [self.messages[0]] + self.messages[cut:]
            force = False
        if removed and not self._trimmed:
            self._trimmed = True
            emit(
                {
                    "type": "note",
                    "level": "warn",
                    "text": f"上下文接近预算上限，已压缩最早的 {removed} 条消息（保留系统提示与最近回合）。",
                }
            )


__all__ = ["LLMAgent", "TOOLS", "select_tools"]
