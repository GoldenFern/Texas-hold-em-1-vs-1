"""LLM agent tool-loop tests using a fake streaming client."""

from __future__ import annotations

from dataclasses import replace
from typing import List

from src.agents.base import AgentView
from src.analysis.hand_distribution import HandDistributionAnalyzer
from src.engine.actions import Action, ActionType
from src.llm.agent import LLMAgent, select_tools
from src.llm.client import ChatResult, ToolCall
from src.llm.prompt import ALL_TOOL_NAMES, build_system_prompt
from src.llm.providers import ProviderRegistry


class FakeClient:
    def __init__(self, responses: List[dict]) -> None:
        self.responses = list(responses)
        self.calls = 0

    def stream(self, spec, messages, tools, params, on_event, cancel=None):
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
    runtime: dict | None = None,
    validator=None,
    tools: list[str] | None = None,
    system_prompt: str | None = None,
):
    registry = ProviderRegistry()
    spec = registry.resolve("deepseek", "deepseek-flash")
    events = []

    def validate(action: Action):
        if action.type in (ActionType.BET, ActionType.RAISE) and action.amount < 20:
            return "最小下注为 20"
        return None

    settings = {
        "max_steps_per_turn": 8,
        "max_consecutive_failures": failures,
        "require_estimate_first": require_estimate_first,
        "max_context_tokens_estimate": 200000,
    }
    if runtime:
        settings.update(runtime)

    agent = LLMAgent(
        seat=0,
        name="Hero",
        spec=spec,
        params={},
        client=FakeClient(responses),
        analyzer=HandDistributionAnalyzer(),
        validate_action=validator or validate,
        runtime=settings,
        tools=select_tools(tools) if tools is not None else None,
        system_prompt=system_prompt,
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


def test_apply_action_only_agent_rejects_hidden_tools() -> None:
    agent, events = build_agent(
        [
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
        tools=["apply_action"],
    )
    action = agent.decide(make_view(), events.append)
    assert action.type == ActionType.CHECK
    assert [tool["function"]["name"] for tool in agent.tools] == ["apply_action"]
    results = [event for event in events if event["type"] == "tool_result"]
    assert results[0]["ok"] is False
    assert "未知工具" in results[0]["summary"]
    assert "apply_action" in results[0]["summary"]


def test_require_estimate_each_turn_skips_river() -> None:
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
        runtime={
            "require_estimate_each_turn": True,
            "skip_estimate_on_complete_board": True,
        },
    )
    action = agent.decide(make_view(), events.append)
    assert action.type == ActionType.CHECK
    results = [event for event in events if event["type"] == "tool_result"]
    assert results[0]["ok"] is False
    assert "本回合在调用 apply_action 之前" in results[0]["summary"]

    river_agent, river_events = build_agent(
        [{"tool_calls": [{"name": "apply_action", "arguments": '{"action": "check"}'}]}],
        runtime={
            "require_estimate_each_turn": True,
            "skip_estimate_on_complete_board": True,
        },
    )
    river_view = replace(
        make_view(),
        street="river",
        community=["Qh", "Jh", "2c", "7d", "9s"],
    )
    river_action = river_agent.decide(river_view, river_events.append)
    assert river_action.type == ActionType.CHECK
    assert river_agent.client.calls == 1
    river_results = [event for event in river_events if event["type"] == "tool_result"]
    assert river_results[0]["ok"] is True


def test_system_prompt_matches_available_tools() -> None:
    control = build_system_prompt(("apply_action",))
    assert "estimate_hand_probabilities" not in control
    assert "get_random_number" not in control
    assert "本回合你只需要调用 apply_action" in control

    treatment = build_system_prompt(
        ALL_TOOL_NAMES,
        require_estimate_each_turn=True,
    )
    assert "只要公共牌少于五张" in treatment
    assert "河牌圈五张公共牌全部发出后不再要求" in treatment
    assert "get_random_number" in treatment


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


def test_reasoning_content_counts_toward_context_budget() -> None:
    agent, _ = build_agent([])
    agent.messages.append(
        {
            "role": "assistant",
            "content": "",
            "reasoning_content": "x" * 40000,
        }
    )
    assert agent._context_tokens() >= 10000


def test_estimate_calls_are_capped_per_turn() -> None:
    agent, events = build_agent(
        [
            {
                "tool_calls": [
                    {
                        "name": "estimate_hand_probabilities",
                        "arguments": '{"iterations": 2000}',
                    }
                ]
            },
            {
                "tool_calls": [
                    {
                        "name": "estimate_hand_probabilities",
                        "arguments": '{"iterations": 2000}',
                    }
                ]
            },
            {"tool_calls": [{"name": "apply_action", "arguments": '{"action": "check"}'}]},
        ],
        runtime={"max_estimate_calls_per_turn": 1},
    )
    action = agent.decide(make_view(), events.append)
    assert action.type == ActionType.CHECK
    results = [event for event in events if event["type"] == "tool_result"]
    assert results[0]["ok"] is True
    assert results[1]["ok"] is False
    assert "上限" in results[1]["summary"]
    assert results[2]["ok"] is True


def test_random_number_never_returns_one(monkeypatch) -> None:
    monkeypatch.setattr("src.llm.agent.random.random", lambda: 0.999999999)
    agent, events = build_agent(
        [
            {"tool_calls": [{"name": "get_random_number", "arguments": "{}"}]},
            {"tool_calls": [{"name": "apply_action", "arguments": '{"action": "check"}'}]},
        ]
    )
    agent.decide(make_view(), events.append)
    results = [
        event
        for event in events
        if event["type"] == "tool_result" and event.get("name") == "get_random_number"
    ]
    assert 0.999 <= results[0]["result"]["value"] < 1.0


def test_malformed_random_arguments_return_tool_error() -> None:
    agent, events = build_agent(
        [
            {"tool_calls": [{"name": "get_random_number", "arguments": "not-json"}]},
            {"tool_calls": [{"name": "apply_action", "arguments": '{"action": "check"}'}]},
        ]
    )
    action = agent.decide(make_view(), events.append)
    assert action.type == ActionType.CHECK
    results = [event for event in events if event["type"] == "tool_result"]
    assert results[0]["ok"] is False
    assert "合法 JSON" in results[0]["summary"]


def test_validation_exception_becomes_tool_error() -> None:
    calls = {"count": 0}

    def validator(action: Action):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("boom")
        return None

    agent, events = build_agent(
        [
            {"tool_calls": [{"name": "apply_action", "arguments": '{"action": "check"}'}]},
            {"tool_calls": [{"name": "apply_action", "arguments": '{"action": "check"}'}]},
        ],
        validator=validator,
    )
    action = agent.decide(make_view(), events.append)
    assert action.type == ActionType.CHECK
    results = [event for event in events if event["type"] == "tool_result"]
    assert results[0]["ok"] is False
    assert "动作校验失败" in results[0]["summary"]
    assert results[1]["ok"] is True


def test_success_clears_last_error() -> None:
    agent, events = build_agent(
        [{"tool_calls": [{"name": "apply_action", "arguments": '{"action": "check"}'}]}]
    )
    agent.last_error = "old failure"
    agent.decide(make_view(), events.append)
    assert agent.last_error == ""


def test_cancel_returns_fold_without_calling_model() -> None:
    agent, events = build_agent([])
    agent.cancel()
    action = agent.decide(make_view(), events.append)
    assert action.type == ActionType.FOLD
    assert agent.client.calls == 0


def test_model_raise_is_normalized_to_bet_at_big_blind_option() -> None:
    view = replace(
        make_view(),
        current_bet=10,
        my_street_bet=10,
        to_call=0,
        call_amount=0,
        min_raise_to=20,
        legal_actions=["fold", "check", "bet", "all_in"],
    )
    agent, events = build_agent(
        [
            {
                "tool_calls": [
                    {
                        "name": "apply_action",
                        "arguments": '{"action": "raise", "amount": 20}',
                    }
                ]
            }
        ]
    )
    action = agent.decide(view, events.append)
    assert action.type == ActionType.BET
    assert action.amount == 20
