"""LLM agent tool-loop tests using a fake streaming client."""

from __future__ import annotations

from typing import List

from src.agents.base import AgentView
from src.analysis.hand_distribution import HandDistributionAnalyzer
from src.engine.actions import Action, ActionType
from src.llm.agent import LLMAgent
from src.llm.client import ChatResult, ToolCall
from src.llm.providers import ProviderRegistry


class FakeClient:
    def __init__(self, responses: List[dict]) -> None:
        self.responses = list(responses)
        self.calls = 0

    def stream(self, spec, messages, tools, params, on_event):
        self.calls += 1
        response = self.responses.pop(0)
        if response.get("reasoning"):
            on_event({"type": "reasoning_delta", "delta": response["reasoning"]})
        if response.get("content"):
            on_event({"type": "content_delta", "delta": response["content"]})
        calls = []
        for index, item in enumerate(response.get("tool_calls", [])):
            call = ToolCall(
                id=item.get("id", f"call_{index}"),
                name=item["name"],
                arguments=item["arguments"],
            )
            calls.append(call)
            on_event(
                {
                    "type": "tool_call_start",
                    "index": index,
                    "call_id": call.id,
                    "name": call.name,
                }
            )
            on_event(
                {
                    "type": "tool_call_args_delta",
                    "index": index,
                    "call_id": call.id,
                    "delta": call.arguments,
                }
            )
        return ChatResult(
            content=response.get("content", ""),
            reasoning=response.get("reasoning", ""),
            tool_calls=calls,
            finish_reason="tool_calls" if calls else "stop",
            usage={"input_tokens": 100, "output_tokens": 30, "reasoning_tokens": 10},
            latency_ms=12.3,
        )


def make_view() -> AgentView:
    return AgentView(
        seat=0,
        name="Hero",
        hand_id=1,
        street="flop",
        hole_cards=["Ah", "Kh"],
        community=["Qh", "Jh", "2c"],
        pot=100,
        my_chips=900,
        opponent_chips=900,
        my_street_bet=0,
        opponent_street_bet=0,
        my_total_bet=50,
        opponent_total_bet=50,
        current_bet=0,
        to_call=0,
        call_amount=0,
        min_raise_to=10,
        max_raise_to=900,
        legal_actions=["fold", "check", "bet", "all_in"],
        action_history=[],
        opponent_last_action=None,
        button=0,
        small_blind=5,
        big_blind=10,
        hand_number=1,
    )


def build_agent(
    responses: List[dict],
    failures: int = 3,
    require_estimate_first: bool = False,
):
    registry = ProviderRegistry()
    spec = registry.resolve("deepseek", "deepseek-flash")
    events = []

    def validate(action: Action):
        if action.type in (ActionType.BET, ActionType.RAISE) and action.amount < 20:
            return "最小下注为 20"
        return None

    agent = LLMAgent(
        seat=0,
        name="Hero",
        spec=spec,
        params={},
        client=FakeClient(responses),
        analyzer=HandDistributionAnalyzer(),
        validate_action=validate,
        runtime={
            "max_steps_per_turn": 8,
            "max_consecutive_failures": failures,
            "require_estimate_first": require_estimate_first,
            "max_context_tokens_estimate": 200000,
        },
    )
    return agent, events


def test_estimate_then_apply() -> None:
    agent, events = build_agent(
        [
            {
                "content": "先看一下牌型分布。",
                "tool_calls": [
                    {
                        "name": "estimate_hand_probabilities",
                        "arguments": '{"iterations": 3000}',
                    }
                ],
            },
            {
                "content": "牌型分布不错，下注半池。",
                "tool_calls": [
                    {"name": "apply_action", "arguments": '{"action": "bet", "amount": 50}'}
                ],
            },
        ]
    )
    action = agent.decide(make_view(), events.append)
    assert action.type == ActionType.BET
    assert action.amount == 50
    assert agent.client.calls == 2
    types = [event["type"] for event in events]
    assert "turn_start" in types
    assert "tool_result" in types
    assert "turn_end" in types
    estimate_results = [
        event
        for event in events
        if event["type"] == "tool_result" and event.get("name") == "estimate_hand_probabilities"
    ]
    assert estimate_results and estimate_results[0]["ok"]
    assert estimate_results[0]["result"]["distribution"]


def test_three_invalid_actions_fall_back_to_call_or_fold() -> None:
    agent, events = build_agent(
        [
            {"tool_calls": [{"name": "apply_action", "arguments": '{"action": "bet", "amount": 1}'}]},
            {"tool_calls": [{"name": "apply_action", "arguments": '{"action": "bet", "amount": 2}'}]},
            {"tool_calls": [{"name": "apply_action", "arguments": '{"action": "bet", "amount": 3}'}]},
        ]
    )
    action = agent.decide(make_view(), events.append)
    assert action.auto is True
    assert action.type == ActionType.FOLD  # no call is legal in this view
    notes = [event for event in events if event["type"] == "note"]
    assert notes and "连续三次无效动作" in notes[-1]["text"]


def test_no_tool_calls_eventually_falls_back() -> None:
    agent, events = build_agent([{"content": "我再想想。"}] * 3)
    action = agent.decide(make_view(), events.append)
    assert action.auto is True
    assert agent.client.calls == 3
    turn_end = [event for event in events if event["type"] == "turn_end"]
    assert turn_end and turn_end[-1]["auto"] is True


def test_apply_without_estimate_is_allowed_by_default() -> None:
    agent, events = build_agent(
        [{"tool_calls": [{"name": "apply_action", "arguments": '{"action": "check"}'}]}]
    )
    action = agent.decide(make_view(), events.append)
    assert action.type == ActionType.CHECK
    assert agent.client.calls == 1
    results = [event for event in events if event["type"] == "tool_result"]
    assert len(results) == 1 and results[0]["ok"] is True


def test_apply_before_estimate_is_nudged_once() -> None:
    agent, events = build_agent(
        [
            {"tool_calls": [{"name": "apply_action", "arguments": '{"action": "check"}'}]},
            {
                "tool_calls": [
                    {
                        "name": "estimate_hand_probabilities",
                        "arguments": '{"iterations": 3000}',
                    }
                ]
            },
            {"tool_calls": [{"name": "apply_action", "arguments": '{"action": "check"}'}]},
        ],
        require_estimate_first=True,
    )
    action = agent.decide(make_view(), events.append)
    assert action.type == ActionType.CHECK
    assert agent.client.calls == 3
    results = [event for event in events if event["type"] == "tool_result"]
    assert results[0]["ok"] is False
    assert "请先调用" in results[0]["summary"]
    assert results[1]["ok"] is True
    assert results[1]["name"] == "estimate_hand_probabilities"


def test_unknown_tool_is_reported() -> None:
    agent, events = build_agent(
        [
            {
                "tool_calls": [
                    {"name": "some_other_tool", "arguments": "{}"}
                ]
            },
            {
                "tool_calls": [
                    {"name": "apply_action", "arguments": '{"action": "check"}'}
                ]
            },
        ]
    )
    action = agent.decide(make_view(), events.append)
    assert action.type == ActionType.CHECK
    results = [event for event in events if event["type"] == "tool_result"]
    assert results[0]["ok"] is False
    assert "未知工具" in results[0]["summary"]


def test_random_number_tool_returns_unit_interval() -> None:
    agent, events = build_agent(
        [
            {
                "content": "先抽个随机数。",
                "tool_calls": [{"name": "get_random_number", "arguments": "{}"}],
            },
            {
                "tool_calls": [
                    {"name": "apply_action", "arguments": '{"action": "check"}'}
                ]
            },
        ]
    )
    action = agent.decide(make_view(), events.append)
    assert action.type == ActionType.CHECK
    assert agent.client.calls == 2
    results = [
        event
        for event in events
        if event["type"] == "tool_result" and event.get("name") == "get_random_number"
    ]
    assert len(results) == 1
    assert results[0]["ok"] is True
    assert 0.0 <= results[0]["result"]["value"] < 1.0


def test_random_number_rerolls_on_each_call(monkeypatch) -> None:
    values = iter([0.1234, 0.8766])
    monkeypatch.setattr("src.llm.agent.random.random", lambda: next(values))
    agent, events = build_agent(
        [
            {
                "tool_calls": [
                    {"name": "get_random_number", "arguments": "{}"},
                    {"name": "get_random_number", "arguments": "{}"},
                ]
            },
            {
                "tool_calls": [
                    {"name": "apply_action", "arguments": '{"action": "check"}'}
                ]
            },
        ]
    )
    action = agent.decide(make_view(), events.append)
    assert action.type == ActionType.CHECK
    results = [
        event["result"]["value"]
        for event in events
        if event["type"] == "tool_result" and event.get("name") == "get_random_number"
    ]
    assert results == [0.1234, 0.8766]


def test_random_number_does_not_replace_estimate() -> None:
    agent, events = build_agent(
        [
            {
                "tool_calls": [{"name": "get_random_number", "arguments": "{}"}],
            },
            {
                "tool_calls": [
                    {"name": "apply_action", "arguments": '{"action": "check"}'}
                ]
            },
            {
                "tool_calls": [
                    {
                        "name": "estimate_hand_probabilities",
                        "arguments": '{"iterations": 3000}',
                    }
                ]
            },
            {
                "tool_calls": [
                    {"name": "apply_action", "arguments": '{"action": "check"}'}
                ]
            },
        ],
        require_estimate_first=True,
    )
    action = agent.decide(make_view(), events.append)
    assert action.type == ActionType.CHECK
    results = [
        event for event in events if event["type"] == "tool_result"
    ]
    assert results[0]["name"] == "get_random_number"
    assert results[0]["ok"] is True
    assert results[1]["ok"] is False
    assert "请先调用" in results[1]["summary"]
    assert results[2]["name"] == "estimate_hand_probabilities"
    assert results[2]["ok"] is True
