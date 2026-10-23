"""English prompt, tool and analysis-text tests."""

from __future__ import annotations

from src.agents.base import AgentView
from src.analysis.equity import EquityAnalyzer
from src.analysis.hand_distribution import HandDistributionAnalyzer
from src.analysis.pot_odds import analyze_call
from src.engine.actions import Action
from src.llm.agent import LLMAgent
from src.llm.client import ChatResult, ToolCall
from src.llm.prompt import (
    ALL_TOOL_NAMES,
    build_hand_result_message,
    build_nudge_message,
    build_system_prompt,
    build_turn_message,
)
from src.llm.providers import ProviderRegistry
from src.llm.tools import select_tools


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
        action_history=[
            {
                "type": "call",
                "label": "跟注",
                "amount": 50,
                "street": "preflop",
                "seat": 1,
            }
        ],
        opponent_last_action=None,
        button=0,
        small_blind=5,
        big_blind=10,
        hand_number=1,
        match_note="第 1 手",
    )


class FakeClient:
    def stream(self, spec, messages, tools, params, on_event, cancel=None):
        return ChatResult(content="ok", usage={})


class UnknownToolClient(FakeClient):
    def stream(self, spec, messages, tools, params, on_event, cancel=None):
        call = ToolCall(id="call_0", name="bogus_tool", arguments="{}")
        on_event(
            {
                "type": "tool_call_start",
                "index": 0,
                "call_id": call.id,
                "name": call.name,
            }
        )
        return ChatResult(content="", tool_calls=[call], usage={})


def test_english_system_prompt_and_tools() -> None:
    prompt = build_system_prompt(ALL_TOOL_NAMES, locale="en-US")
    assert "Think and respond in English" in prompt
    assert "[Rules]" in prompt
    assert "raise-to" in prompt
    assert "【" not in prompt

    tools = select_tools(["apply_action", "estimate_equity"], locale="en-US")
    descriptions = {
        tool["function"]["name"]: tool["function"]["description"] for tool in tools
    }
    assert "Submit your final action" in descriptions["apply_action"]
    assert "showdown equity" in descriptions["estimate_equity"]

    talk_prompt = build_system_prompt(
        ["apply_action", "send_table_message"], locale="en-US"
    )
    assert "[Table talk (hard rule)]" in talk_prompt
    assert "English sentences" in talk_prompt


def test_english_turn_and_result_messages() -> None:
    view = make_view()
    turn = build_turn_message(view, locale="en-US")
    assert turn.startswith("[Table update")
    assert "Your hole cards: Ah Kh" in turn
    assert "To call: 0" in turn
    assert '"label": "Call"' in turn
    assert "【牌局更新" not in turn

    result = {
        "winner_seats": [0],
        "pot": 100,
        "players": [
            {"seat": 0, "net": 100, "chips_after": 1100},
        ],
        "board": ["Qh", "Jh", "2c", "7d", "9s"],
        "showdown": [
            {"seat": 0, "category": "three_of_a_kind", "best_five": ["Ah", "Ad", "As"]},
        ],
        "reason": "showdown",
        "match_over": False,
    }
    message = build_hand_result_message(result, 0, "Hero", locale="en-US")
    assert "You won the hand" in message
    assert "Three of a kind" in message
    assert "下一手" not in message
    assert "Call apply_action" in build_nudge_message(locale="en-US")


def test_english_analysis_tool_text() -> None:
    analyzer = HandDistributionAnalyzer()
    from src.engine.cards import Card

    distribution = analyzer.analyze(
        [Card.from_str("Ah"), Card.from_str("Kh")],
        [Card.from_str("Qh"), Card.from_str("Jh"), Card.from_str("2c")],
        iterations=2000,
        locale="en-US",
    ).to_llm_dict("en-US")
    assert "hand-category distribution" in distribution["note"]

    equity = EquityAnalyzer().analyze(
        [Card.from_str("Ah"), Card.from_str("Kh")],
        [Card.from_str("Qh"), Card.from_str("Jh"), Card.from_str("2c")],
        iterations=2000,
        seed=1,
        locale="en-US",
    ).to_llm_dict("en-US")
    assert "showdown pot share" in equity["note"]

    call_ev = analyze_call(100, 50, 900, 0.6, locale="en-US").to_llm_dict("en-US")
    assert "Static call EV" in call_ev["note"]


def test_english_agent_tool_errors_and_fallback() -> None:
    registry = ProviderRegistry()
    agent = LLMAgent(
        seat=0,
        name="Hero",
        spec=registry.resolve("deepseek", "deepseek-flash"),
        params={},
        client=UnknownToolClient(),
        analyzer=HandDistributionAnalyzer(),
        validate_action=lambda action: None,
        runtime={"max_steps_per_turn": 2, "max_consecutive_failures": 1},
        tools=select_tools(["apply_action"], locale="en-US"),
        locale="en-US",
    )
    events = []
    action = agent.decide(make_view(), events.append)
    turn_state = next(event for event in events if event["type"] == "turn_state")
    assert turn_state["text"].startswith("[Table update")
    assert "Your hole cards" in turn_state["text"]
    tool_results = [event for event in events if event["type"] == "tool_result"]
    assert tool_results
    assert "Unknown tool" in tool_results[0]["summary"]
    turn_end = [event for event in events if event["type"] == "turn_end"][-1]
    assert turn_end["reason"] == "three consecutive invalid actions"
    assert action.auto is True


def test_llm_agent_uses_english_prompt() -> None:
    registry = ProviderRegistry()
    agent = LLMAgent(
        seat=0,
        name="Hero",
        spec=registry.resolve("deepseek", "deepseek-flash"),
        params={},
        client=FakeClient(),
        analyzer=HandDistributionAnalyzer(),
        validate_action=lambda action: None,
        runtime={"max_steps_per_turn": 2},
        tools=select_tools(["apply_action"], locale="en-US"),
        locale="en-US",
    )
    assert "Think and respond in English" in agent.system_prompt
    assert "Submit your final action" in agent.tools[0]["function"]["description"]
    assert agent.locale == "en-US"
    assert Action.from_payload(0, {"type": "check"}, locale="en-US").type.value == "check"
