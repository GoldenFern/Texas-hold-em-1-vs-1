"""RLCard bot adapter tests (no server, no network)."""

from __future__ import annotations

import pytest

pytest.importorskip("rlcard")

from src.agents.base import AgentView
from src.agents.rlcard_bot import RLCardBot
from src.engine.actions import ActionType


def make_view(
    *,
    current_bet: int,
    my_street_bet: int,
    legal: list[str],
    min_raise_to: int,
    max_raise_to: int,
    pot: int,
) -> AgentView:
    return AgentView(
        seat=0,
        name="Bot A",
        hand_id=1,
        street="flop",
        hole_cards=["Ah", "Kh"],
        community=["Qh", "Jh", "2c"],
        pot=pot,
        my_chips=1000 - my_street_bet,
        opponent_chips=980,
        my_street_bet=my_street_bet,
        opponent_street_bet=current_bet,
        my_total_bet=my_street_bet + 100,
        opponent_total_bet=current_bet + 100,
        current_bet=current_bet,
        to_call=max(0, current_bet - my_street_bet),
        call_amount=max(0, current_bet - my_street_bet),
        min_raise_to=min_raise_to,
        max_raise_to=max_raise_to,
        legal_actions=legal,
        action_history=[],
        opponent_last_action=None,
        button=0,
        small_blind=5,
        big_blind=10,
        hand_number=1,
    )


def test_random_bot_returns_legal_actions() -> None:
    bot = RLCardBot("Bot A", agent_type="random", seed=7)
    assert bot.describe()["available"] is True
    views = [
        make_view(
            current_bet=20,
            my_street_bet=10,
            legal=["fold", "call", "raise", "all_in"],
            min_raise_to=30,
            max_raise_to=1000,
            pot=100,
        ),
        make_view(
            current_bet=0,
            my_street_bet=0,
            legal=["fold", "check", "bet", "all_in"],
            min_raise_to=10,
            max_raise_to=1000,
            pot=60,
        ),
    ]
    for _ in range(100):
        for view in views:
            action = bot.decide(view, lambda event: None)
            assert action.type.value in view.legal_actions
            if action.type in (ActionType.BET, ActionType.RAISE):
                assert action.amount >= view.min_raise_to
                assert action.amount <= view.max_raise_to
            if action.type == ActionType.CALL:
                assert view.current_bet > view.my_street_bet


def test_raise_from_big_blind_option_becomes_bet() -> None:
    class FixedAgent:
        def eval_step(self, state):
            return 2, {}

    bot = RLCardBot("Bot A", agent_type="random", seed=7)
    bot._agent = FixedAgent()
    view = make_view(
        current_bet=10,
        my_street_bet=10,
        legal=["fold", "check", "bet", "all_in"],
        min_raise_to=20,
        max_raise_to=1000,
        pot=15,
    )
    action = bot.decide(view, lambda event: None)
    assert action.type.value in view.legal_actions
    assert action.type.value in {"bet", "all_in"}
    assert action.amount >= view.min_raise_to


def test_observation_shape_and_card_mapping() -> None:
    import numpy as np

    bot = RLCardBot("Bot A", agent_type="random", seed=7)
    view = make_view(
        current_bet=20,
        my_street_bet=10,
        legal=["fold", "call", "raise", "all_in"],
        min_raise_to=30,
        max_raise_to=1000,
        pot=100,
    )
    obs = bot._observation(view)
    assert obs.shape == (54,)
    assert obs.dtype == np.float32
    # Ah, Kh, Qh, Jh, 2c must be one-hot encoded.
    indices = [bot._card_index(code) for code in ("Ah", "Kh", "Qh", "Jh", "2c")]
    assert all(obs[index] == 1.0 for index in indices)
    assert obs[52] == float(view.my_total_bet)
    assert obs[53] == float(max(view.my_total_bet, view.opponent_total_bet))
