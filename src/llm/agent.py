"""The tool-using LLM poker agent."""

from __future__ import annotations

import json
import random
import threading
import time
import uuid
from typing import Callable, List, Optional

from src.agents.base import AgentView, BaseAgent
from src.analysis.hand_distribution import HandDistributionAnalyzer
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
    ) -> None:
        self.seat = seat
        self.name = name
        self.spec = spec
        self.params = spec.normalize_params(params)
        self.client = client
        self.analyzer = analyzer
        self.validate_action = validate_action
        self.max_steps = int(runtime.get("max_steps_per_turn", 12))
        self.max_failures = int(runtime.get("max_consecutive_failures", 3))
        self.require_estimate_first = bool(runtime.get("require_estimate_first", False))
        runtime_budget = int(runtime.get("max_context_tokens_estimate", 220000))
        reserved_output = int(self.params.get("max_tokens", 32768))
        context_window = int(getattr(spec, "context_window", 0) or 0)
        if context_window > 0:
            runtime_budget = min(runtime_budget, context_window - reserved_output - 2048)
        self.max_context_tokens = max(8000, runtime_budget)
        self.max_estimate_calls = int(runtime.get("max_estimate_calls_per_turn", 2))
        self.max_estimate_iterations = int(runtime.get("max_estimate_iterations", 50000))
        self.messages: List[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        self.turn_count = 0
        self._estimated_keys: set[tuple[int, str]] = set()
        self._nudged_keys: set[tuple[int, str]] = set()
        self._estimate_calls_this_turn = 0
        self._estimate_iterations_this_turn = 0
        self._tools_tokens = estimate_tokens(json.dumps(TOOLS, ensure_ascii=False))
        self.total_usage = {"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0}
        self.last_error = ""
        self._trimmed = False
        self._cancel_event = threading.Event()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def reset(self) -> None:
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        self.turn_count = 0
        self.total_usage = {"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0}
        self.last_error = ""
        self._trimmed = False
        self._estimated_keys.clear()
        self._nudged_keys.clear()
        self._estimate_calls_this_turn = 0
        self._estimate_iterations_this_turn = 0
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
        self._estimate_calls_this_turn = 0
        self._estimate_iterations_this_turn = 0
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
                    TOOLS,
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
                    if (
                        self.require_estimate_first
                        and street_key not in self._estimated_keys
                        and street_key not in self._nudged_keys
                    ):
                        # Soft workflow requirement: one probability check per
                        # street before the first action. Only nudged once, and
                        # it does not count toward the three-failure fallback.
                        self._nudged_keys.add(street_key)
                        self._append_tool_error(
                            call,
                            "在本街的第一次决策前，请先调用 estimate_hand_probabilities 一次，再提交 apply_action。",
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

                failures += 1
                self._append_tool_error(
                    call,
                    f"未知工具 {call.name!r}。只能使用 estimate_hand_probabilities、"
                    f"get_random_number 和 apply_action。",
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


__all__ = ["LLMAgent", "TOOLS"]
