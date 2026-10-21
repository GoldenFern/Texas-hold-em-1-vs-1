"""Seventy-three float observation for the project heads-up bot.

Layout
------
0..51    card multi-hot (hero hole cards + board)
52       pot / big blind
53       hero remaining chips / big blind
54       villain remaining chips / big blind
55       hero street bet / big blind
56       villain street bet / big blind
57       to_call / big blind
58       min_raise_to / big blind
59       max_raise_to / big blind
60..63   street one-hot: preflop, flop, turn, river
64       is_button
65..69   last action type: fold, check, call, aggressive, none
70       last action amount / pot after action
71..72   last actor: hero, villain

The vector is always from the acting player's point of view, so one network can
play both seats.  The legal 10-slot mask is kept outside the vector.
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from src.agents.base import AgentView
from src.engine.cards import Card

OBS_DIM = 73

CARD_DIM = 52
POT_INDEX = 52
MY_CHIPS_INDEX = 53
OPPONENT_CHIPS_INDEX = 54
MY_STREET_BET_INDEX = 55
OPPONENT_STREET_BET_INDEX = 56
TO_CALL_INDEX = 57
MIN_RAISE_INDEX = 58
MAX_RAISE_INDEX = 59
STREET_OFFSET = 60
IS_BUTTON_INDEX = 64
LAST_TYPE_OFFSET = 65
LAST_SIZE_INDEX = 70
LAST_ACTOR_OFFSET = 71

STREET_INDEX = {
    "preflop": 0,
    "flop": 1,
    "turn": 2,
    "river": 3,
}

_SUIT_ORDER = {"s": 0, "h": 1, "d": 2, "c": 3}
_LAST_TYPE_INDEX = {
    "fold": 0,
    "check": 1,
    "call": 2,
    "bet": 3,
    "raise": 3,
    "all_in": 3,
    "all-in": 3,
}
LAST_TYPE_NONE = 4


def card_index(code: str) -> int:
    """Return the RLCard-compatible 0..51 card index."""
    card = Card.from_str(code)
    suit = _SUIT_ORDER[card.suit.value]
    rank = (int(card.rank) - 1) % 13
    return suit * 13 + rank


def encode_observation(view: AgentView, big_blind: int) -> np.ndarray:
    """Encode an ``AgentView`` into the 73-float vector."""
    obs = np.zeros(OBS_DIM, dtype=np.float32)
    for code in list(view.community) + list(view.hole_cards):
        obs[card_index(code)] = 1.0

    bb = max(1, int(big_blind))
    obs[POT_INDEX] = float(view.pot) / bb
    obs[MY_CHIPS_INDEX] = float(view.my_chips) / bb
    obs[OPPONENT_CHIPS_INDEX] = float(view.opponent_chips) / bb
    obs[MY_STREET_BET_INDEX] = float(view.my_street_bet) / bb
    obs[OPPONENT_STREET_BET_INDEX] = float(view.opponent_street_bet) / bb
    obs[TO_CALL_INDEX] = float(max(0, view.to_call)) / bb
    obs[MIN_RAISE_INDEX] = float(view.min_raise_to) / bb
    obs[MAX_RAISE_INDEX] = float(view.max_raise_to) / bb

    street = STREET_INDEX.get(str(view.street))
    if street is not None:
        obs[STREET_OFFSET + street] = 1.0

    obs[IS_BUTTON_INDEX] = 1.0 if view.is_button else 0.0

    last = view.action_history[-1] if view.action_history else None
    _encode_last_action(obs, last, int(view.seat))
    return obs


def _encode_last_action(obs: np.ndarray, last: Optional[dict], hero_seat: int) -> None:
    if not last:
        obs[LAST_TYPE_OFFSET + LAST_TYPE_NONE] = 1.0
        return

    action_type = str(last.get("type") or "").lower()
    type_index = _LAST_TYPE_INDEX.get(action_type, LAST_TYPE_NONE)
    obs[LAST_TYPE_OFFSET + type_index] = 1.0

    amount = float(last.get("amount") or 0)
    pot_after = float(last.get("pot_after") or 0)
    obs[LAST_SIZE_INDEX] = min(2.0, amount / max(pot_after, 1.0))

    actor = last.get("seat")
    if actor is not None:
        if int(actor) == hero_seat:
            obs[LAST_ACTOR_OFFSET] = 1.0
        elif int(actor) in (0, 1):
            obs[LAST_ACTOR_OFFSET + 1] = 1.0


__all__ = [
    "CARD_DIM",
    "IS_BUTTON_INDEX",
    "LAST_ACTOR_OFFSET",
    "LAST_SIZE_INDEX",
    "LAST_TYPE_NONE",
    "LAST_TYPE_OFFSET",
    "MAX_RAISE_INDEX",
    "MIN_RAISE_INDEX",
    "MY_CHIPS_INDEX",
    "MY_STREET_BET_INDEX",
    "OBS_DIM",
    "OPPONENT_CHIPS_INDEX",
    "OPPONENT_STREET_BET_INDEX",
    "POT_INDEX",
    "STREET_INDEX",
    "STREET_OFFSET",
    "TO_CALL_INDEX",
    "card_index",
    "encode_observation",
]
