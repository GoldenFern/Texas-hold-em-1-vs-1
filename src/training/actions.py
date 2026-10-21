"""Ten-slot action abstraction for the project heads-up engine.

The slots are deliberately independent of RLCard's five-action abstraction:

0 fold
1 check
2 call
3 min-raise
4 1/3 pot
5 1/2 pot
6 3/4 pot
7 pot
8 1.5x pot
9 all-in

A slot is legal only when the exact raise-to amount is legal in the project
engine.  We never clamp a below-minimum size up to the minimum, because that
would execute a different action than the one the policy selected.
"""

from __future__ import annotations

import math
from typing import Iterable, Set

import numpy as np

from src.agents.base import AgentView
from src.engine.actions import Action, ActionType

ACTION_FOLD = 0
ACTION_CHECK = 1
ACTION_CALL = 2
ACTION_MIN_RAISE = 3
ACTION_THIRD_POT = 4
ACTION_HALF_POT = 5
ACTION_THREE_QUARTER_POT = 6
ACTION_POT = 7
ACTION_ONE_HALF_POT = 8
ACTION_ALL_IN = 9

NUM_ACTIONS = 10
ACTION_LABELS = (
    "fold",
    "check",
    "call",
    "min_raise",
    "third_pot",
    "half_pot",
    "three_quarter_pot",
    "pot",
    "one_half_pot",
    "all_in",
)
POT_FRACTIONS = {
    ACTION_THIRD_POT: 1.0 / 3.0,
    ACTION_HALF_POT: 0.5,
    ACTION_THREE_QUARTER_POT: 0.75,
    ACTION_POT: 1.0,
    ACTION_ONE_HALF_POT: 1.5,
}


def raise_to_for_fraction(view: AgentView, fraction: float) -> int:
    """Return the standard pot-fraction raise-to amount.

    Standard formula: call first, then raise by ``fraction`` of the pot after
    the call.  For an opening bet the call amount is zero, so this reduces to
    ``fraction * pot`` (rounded half up).
    """
    to_call = max(0, int(view.to_call))
    called_target = int(view.my_street_bet) + to_call
    pot_after_call = max(0, int(view.pot)) + to_call
    return called_target + int(math.floor(fraction * pot_after_call + 0.5))


def legal_action_mask(view: AgentView) -> np.ndarray:
    """Return a boolean mask of the ten action slots.

    The mask only depends on the public and private information in ``view``.
    It uses the engine's ``legal_actions``, ``min_raise_to`` and
    ``max_raise_to`` fields, so the training environment and the live server
    share the same rule source.
    """
    mask = np.zeros(NUM_ACTIONS, dtype=bool)
    legal = set(view.legal_actions)

    if "fold" in legal:
        mask[ACTION_FOLD] = True
    if "check" in legal:
        mask[ACTION_CHECK] = True
    if "call" in legal:
        mask[ACTION_CALL] = True

    can_raise = "bet" in legal or "raise" in legal
    if not can_raise:
        return mask

    current_bet = int(view.current_bet)
    min_raise_to = int(view.min_raise_to)
    max_raise_to = int(view.max_raise_to)
    my_street_bet = int(view.my_street_bet)
    seen_targets: Set[int] = set()

    if (
        min_raise_to > current_bet
        and min_raise_to > my_street_bet
        and min_raise_to <= max_raise_to
    ):
        mask[ACTION_MIN_RAISE] = True
        seen_targets.add(min_raise_to)

    for slot, fraction in POT_FRACTIONS.items():
        target = raise_to_for_fraction(view, fraction)
        if target <= current_bet:
            continue
        if target < min_raise_to:
            continue
        if target > max_raise_to:
            continue
        if target <= my_street_bet:
            continue
        if target in seen_targets:
            continue
        mask[slot] = True
        seen_targets.add(target)

    # All-in is the only slot allowed to make a sub-minimum raise.  When the
    # stack cannot cover the current bet, call handles the all-in call.
    if "all_in" in legal and max_raise_to > current_bet and max_raise_to not in seen_targets:
        mask[ACTION_ALL_IN] = True

    return mask


def action_from_view(view: AgentView, slot: int) -> Action:
    """Translate a slot into a concrete engine action.

    Raises ``ValueError`` when the slot is not legal.  Callers should mask
    first; raising here keeps silent illegal-action fallbacks out of training.
    """
    if not 0 <= int(slot) < NUM_ACTIONS:
        raise ValueError(f"动作 slot 越界: {slot!r}")
    slot = int(slot)
    mask = legal_action_mask(view)
    if not mask[slot]:
        legal = [ACTION_LABELS[index] for index, allowed in enumerate(mask) if allowed]
        raise ValueError(f"动作 {ACTION_LABELS[slot]} 当前不合法，合法动作: {legal}")

    seat = int(view.seat)
    if slot == ACTION_FOLD:
        return Action(seat, ActionType.FOLD, source="project")
    if slot == ACTION_CHECK:
        return Action(seat, ActionType.CHECK, source="project")
    if slot == ACTION_CALL:
        return Action(seat, ActionType.CALL, source="project")
    if slot == ACTION_ALL_IN:
        return Action(seat, ActionType.ALL_IN, source="project")

    if slot == ACTION_MIN_RAISE:
        target = int(view.min_raise_to)
    else:
        target = raise_to_for_fraction(view, POT_FRACTIONS[slot])

    action_type = ActionType.BET if int(view.current_bet) == 0 else ActionType.RAISE
    return Action(seat, action_type, amount=int(target), source="project")


def action_label(slot: int) -> str:
    if 0 <= int(slot) < NUM_ACTIONS:
        return ACTION_LABELS[int(slot)]
    return f"slot_{slot}"


def legal_slots(view: AgentView) -> Iterable[int]:
    return (index for index, allowed in enumerate(legal_action_mask(view)) if allowed)


__all__ = [
    "ACTION_ALL_IN",
    "ACTION_CALL",
    "ACTION_CHECK",
    "ACTION_FOLD",
    "ACTION_HALF_POT",
    "ACTION_LABELS",
    "ACTION_MIN_RAISE",
    "ACTION_ONE_HALF_POT",
    "ACTION_POT",
    "ACTION_THIRD_POT",
    "ACTION_THREE_QUARTER_POT",
    "NUM_ACTIONS",
    "POT_FRACTIONS",
    "action_from_view",
    "action_label",
    "legal_action_mask",
    "legal_slots",
    "raise_to_for_fraction",
]
