from __future__ import annotations

import math

import numpy as np
import pytest
import torch
from rlcard.agents import DQNAgent, RandomAgent

from src.agents.project_bot import ProjectBot, save_project_checkpoint
from src.training import NUM_ACTIONS, OBS_DIM, ProjectHeadsUpEnv
from src.training.actions import (
    ACTION_ALL_IN,
    ACTION_CALL,
    ACTION_CHECK,
    ACTION_FOLD,
    ACTION_MIN_RAISE,
    ACTION_HALF_POT,
    ACTION_THREE_QUARTER_POT,
    action_from_view,
    legal_action_mask,
    raise_to_for_fraction,
)
from src.training.obs import card_index, encode_observation


def test_card_index_matches_rlcard_order() -> None:
    assert card_index("As") == 0
    assert card_index("Ah") == 13
    assert card_index("Ad") == 26
    assert card_index("Ac") == 39
    assert card_index("Kd") == 38


def test_observation_shape_and_card_bits() -> None:
    env = ProjectHeadsUpEnv(seed=3)
    state = env.reset()
    obs = state["obs"]
    assert obs.shape == (OBS_DIM,)
    assert obs.dtype == np.float32
    view = env.build_view(env.game.actor)  # type: ignore[union-attr]
    known = list(view.community) + list(view.hole_cards)
    assert int(obs[:52].sum()) == len(known)
    for code in known:
        assert obs[card_index(code)] == 1.0
    assert obs[64] in (0.0, 1.0)


def test_preflop_mask_and_min_raise() -> None:
    env = ProjectHeadsUpEnv(seed=4)
    env.reset(button=0)
    view = env.build_view(0)
    mask = legal_action_mask(view)
    assert mask[ACTION_FOLD]
    assert mask[ACTION_CALL]
    assert mask[ACTION_MIN_RAISE]
    assert mask[ACTION_ALL_IN]
    assert not mask[ACTION_CHECK]

    action = action_from_view(view, ACTION_MIN_RAISE)
    assert action.amount == view.min_raise_to
    assert action.type.value == "raise"

    # At 5/10 preflop half-pot deduplicates with the min-raise.  Use 3/4 pot
    # to exercise the standard pot-fraction formula.
    assert not mask[ACTION_HALF_POT]
    fraction_action = action_from_view(view, ACTION_THREE_QUARTER_POT)
    assert fraction_action.amount == raise_to_for_fraction(view, 0.75)
    assert fraction_action.amount >= view.min_raise_to
    assert fraction_action.amount <= view.max_raise_to


def test_postflop_check_slot() -> None:
    env = ProjectHeadsUpEnv(seed=5)
    env.reset(button=0)
    env.play_hand([RandomAgent(NUM_ACTIONS), RandomAgent(NUM_ACTIONS)], training=False)
    # The hand may end preflop or later; construct a fresh flop state instead.
    env.reset(button=0)
    # SB call, BB check -> flop, BB acts first.
    env.step(ACTION_CALL)
    env.step(ACTION_CHECK)
    view = env.build_view(env.game.actor)  # type: ignore[union-attr]
    mask = legal_action_mask(view)
    assert mask[ACTION_CHECK]
    assert not mask[ACTION_CALL]
    assert action_from_view(view, ACTION_CHECK).type.value == "check"


def test_chip_conservation_and_button_rotation() -> None:
    env = ProjectHeadsUpEnv(starting_chips=1000, small_blind=5, big_blind=10, seed=6)
    agents = [RandomAgent(NUM_ACTIONS), RandomAgent(NUM_ACTIONS)]
    buttons = []
    for _ in range(200):
        env.reset()
        buttons.append(env.last_button)
        env.play_hand(agents, training=False)
        assert sum(player.chips for player in env.game.players) == 2000  # type: ignore[union-attr]
        net0, net1 = env.raw_payoffs()
        assert net0 + net1 == 0
        payoffs = env.payoffs()
        assert payoffs[0] == pytest.approx(net0 / 200.0)
        assert payoffs[1] == pytest.approx(net1 / 200.0)
    assert buttons[:6] == [0, 1, 0, 1, 0, 1]


def test_fold_refund_and_reward_scale() -> None:
    env = ProjectHeadsUpEnv(seed=7)
    env.reset(button=0)
    env.step(ACTION_FOLD)
    assert env.game.result is not None  # type: ignore[union-attr]
    assert env.game.result["reason"] == "fold"  # type: ignore[union-attr]
    net0, net1 = env.raw_payoffs()
    assert net0 == -5
    assert net1 == 5
    assert env.payoffs() == pytest.approx([-0.025, 0.025])


def test_project_bot_checkpoint_roundtrip(tmp_path) -> None:
    env = ProjectHeadsUpEnv(seed=8)
    env.reset(button=0)
    view = env.build_view(0)

    agent = DQNAgent(
        num_actions=NUM_ACTIONS,
        state_shape=[OBS_DIM],
        mlp_layers=[128, 128],
        device="cpu",
    )
    path = save_project_checkpoint(
        tmp_path / "dqn.pth",
        algorithm="dqn",
        state_dict=agent.q_estimator.qnet.state_dict(),
        mlp_layers=[128, 128],
        metadata={"test": True},
    )
    bot = ProjectBot("test", path)
    action = bot.decide(view, lambda event: None)
    assert action.seat == 0
    assert action.type.value in {"fold", "check", "call", "bet", "raise", "all_in"}
    payload = torch.load(path, map_location="cpu", weights_only=True)
    assert payload["format"] == "project-bot-v1"
    assert payload["obs_dim"] == OBS_DIM
    assert payload["num_actions"] == NUM_ACTIONS
