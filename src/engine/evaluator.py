"""Hand evaluation built on Treys (TwoPlusTwo lookup tables)."""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Dict, List, Sequence

from treys import Card as TreysCard
from treys import Evaluator

from .cards import Card

_EVALUATOR = Evaluator()

# Treys rank classes: 0 royal flush, 1 straight flush, ..., 9 high card.
_TREYS_CLASS_TO_KEY = {
    0: "royal_flush",
    1: "straight_flush",
    2: "four_of_a_kind",
    3: "full_house",
    4: "flush",
    5: "straight",
    6: "three_of_a_kind",
    7: "two_pair",
    8: "one_pair",
    9: "high_card",
}

HAND_CATEGORIES: List[Dict[str, str]] = [
    {"key": "royal_flush", "name": "皇家同花顺", "short": "皇家", "color": "#F5C451"},
    {"key": "straight_flush", "name": "同花顺", "short": "同花顺", "color": "#F09A5B"},
    {"key": "four_of_a_kind", "name": "四条", "short": "四条", "color": "#E86A6A"},
    {"key": "full_house", "name": "葫芦", "short": "葫芦", "color": "#C77DFF"},
    {"key": "flush", "name": "同花", "short": "同花", "color": "#6EA8FE"},
    {"key": "straight", "name": "顺子", "short": "顺子", "color": "#57D3B5"},
    {"key": "three_of_a_kind", "name": "三条", "short": "三条", "color": "#8BD17C"},
    {"key": "two_pair", "name": "两对", "short": "两对", "color": "#D6C36A"},
    {"key": "one_pair", "name": "一对", "short": "一对", "color": "#B9A58A"},
    {"key": "high_card", "name": "高牌", "short": "高牌", "color": "#8E9AA6"},
    {"key": "incomplete", "name": "未成牌", "short": "未成牌", "color": "#66707A"},
]

CATEGORY_BY_KEY: Dict[str, Dict[str, str]] = {item["key"]: item for item in HAND_CATEGORIES}
STRENGTH_ORDER: List[str] = [item["key"] for item in HAND_CATEGORIES]


def treys_int(card: Card) -> int:
    """Convert a card to its Treys integer."""
    return TreysCard.new(card.code)


def card_from_treys(value: int) -> str:
    """Human readable string from a Treys integer (for debugging)."""
    return TreysCard.int_to_str(value)


def category_from_score(score: int) -> str:
    """Map a Treys score to our stable category key."""
    return _TREYS_CLASS_TO_KEY[_EVALUATOR.get_rank_class(score)]


def evaluate_score(treys_hole: Sequence[int], treys_board: Sequence[int]) -> int:
    """Raw Treys score (lower is stronger). Hot path for Monte Carlo."""
    return _EVALUATOR.evaluate(list(treys_hole), list(treys_board))


def evaluate_score_cards(cards: Sequence[Card]) -> int:
    """Evaluate 5-7 cards and return the Treys score."""
    values = [treys_int(card) for card in cards]
    if len(values) < 5:
        raise ValueError("至少需要 5 张牌")
    return _EVALUATOR.evaluate(values[:2], values[2:])


@dataclass(frozen=True)
class HandAssessment:
    """A readable assessment of a made hand."""

    category: str
    category_name: str
    score: int
    best_five: List[str]

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "category_name": self.category_name,
            "score": self.score,
            "best_five": list(self.best_five),
        }


def assess_hand(cards: Sequence[Card]) -> HandAssessment | None:
    """Return the best five-card hand for 5-7 cards.

    Returns ``None`` when fewer than five cards are available (preflop or
    an incomplete flop).
    """
    if len(cards) < 5:
        return None
    if len(cards) > 7:
        raise ValueError("最多评估 7 张牌")

    best_score = 7463
    best_combo: tuple[Card, ...] = ()
    for combo in itertools.combinations(cards, 5):
        values = [treys_int(card) for card in combo]
        score = _EVALUATOR.evaluate(values[:2], values[2:])
        if score < best_score:
            best_score = score
            best_combo = combo
    category = category_from_score(best_score)
    ordered = sorted(best_combo, key=lambda card: (-int(card.rank), card.suit.value))
    return HandAssessment(
        category=category,
        category_name=CATEGORY_BY_KEY[category]["name"],
        score=best_score,
        best_five=[card.code for card in ordered],
    )


def hand_strength(cards: Sequence[Card]) -> tuple[str, str]:
    """Convenience helper returning ``(category_key, category_name)``."""
    assessment = assess_hand(cards)
    if assessment is None:
        return "incomplete", CATEGORY_BY_KEY["incomplete"]["name"]
    return assessment.category, assessment.category_name


__all__ = [
    "CATEGORY_BY_KEY",
    "HAND_CATEGORIES",
    "STRENGTH_ORDER",
    "HandAssessment",
    "assess_hand",
    "category_from_score",
    "evaluate_score",
    "evaluate_score_cards",
    "hand_strength",
    "treys_int",
]
