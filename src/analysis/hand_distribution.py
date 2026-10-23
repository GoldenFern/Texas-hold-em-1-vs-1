"""Distribution of the hero's final five-card hand category.

Given the hero's two hole cards and the visible board, enumerate the missing
board cards exactly when the space is small, otherwise use Monte Carlo.
Treys scores are mapped to categories through its rank-class table, which keeps
the hot loop free of per-sample combination extraction.
"""

from __future__ import annotations

import copy
import itertools
import random
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from src.engine.cards import FULL_DECK, Card
from src.engine.evaluator import (
    CATEGORY_BY_KEY,
    HAND_CATEGORIES,
    assess_hand,
    category_from_score,
    hand_category_name,
    treys_int,
)
from src.i18n import tr
from treys import Evaluator

_EVALUATOR = Evaluator()
DEFAULT_ITERATIONS = 30000
MIN_ITERATIONS = 2000
MAX_ITERATIONS = 200000
EXACT_COMBINATION_LIMIT = 60000
_CACHE_LIMIT = 256


@dataclass
class DistributionResult:
    hero_seat: int
    method: str
    iterations: int
    elapsed_ms: float
    total: int
    distribution: List[dict]
    current_best: Optional[dict]
    known_cards: List[str]
    needed_board_cards: int

    def to_dict(self) -> dict:
        return {
            "hero_seat": self.hero_seat,
            "method": self.method,
            "iterations": self.iterations,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "total": self.total,
            "distribution": copy.deepcopy(self.distribution),
            "current_best": copy.deepcopy(self.current_best),
            "known_cards": list(self.known_cards),
            "needed_board_cards": self.needed_board_cards,
        }

    def to_llm_dict(self, locale: str = "zh-CN") -> dict:
        """Compact tool result for an LLM."""
        current_best = None
        if self.current_best:
            current_best = dict(self.current_best)
            current_best["category_name"] = hand_category_name(
                str(current_best.get("category") or ""),
                locale,
                str(current_best.get("category_name") or ""),
            )
        return {
            "method": self.method,
            "samples": self.total,
            "needed_board_cards": self.needed_board_cards,
            "current_best": current_best,
            "probabilities": {
                hand_category_name(item["key"], locale, item["name"]): round(
                    item["probability"] * 100, 3
                )
                for item in self.distribution
                if item["probability"] > 0
            },
            "note": tr(locale, "analysis.hand.note"),
        }


class HandDistributionAnalyzer:
    """Compute and cache final hand-category distributions."""

    def __init__(self) -> None:
        self._cache: "OrderedDict[tuple, DistributionResult]" = OrderedDict()
        self._lock = threading.RLock()

    def analyze(
        self,
        hero_hole: Sequence[Card],
        community: Sequence[Card],
        hero_seat: int = 0,
        iterations: int = DEFAULT_ITERATIONS,
        locale: str = "zh-CN",
    ) -> DistributionResult:
        if len(hero_hole) != 2:
            raise ValueError(tr(locale, "analysis.hand.two_hole"))
        if len(community) > 5:
            raise ValueError(tr(locale, "analysis.hand.community_max"))
        iterations = max(MIN_ITERATIONS, min(int(iterations), MAX_ITERATIONS))

        known = list(hero_hole) + list(community)
        codes = [card.code for card in known]
        if len(set(codes)) != len(codes):
            raise ValueError(tr(locale, "analysis.hand.duplicate"))
        key = (
            int(hero_seat),
            tuple(sorted(card.code for card in hero_hole)),
            tuple(card.code for card in community),
            iterations,
        )
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return cached

        start = time.perf_counter()
        needed = 5 - len(community)
        known_set = {card.code for card in known}
        unknown = [card for card in FULL_DECK if card.code not in known_set]
        unknown_ints = [treys_int(card) for card in unknown]
        hole_ints = [treys_int(card) for card in hero_hole]
        board_ints = [treys_int(card) for card in community]

        counts = {item["key"]: 0 for item in HAND_CATEGORIES if item["key"] != "incomplete"}
        total = 0

        if needed <= 0:
            method = "exact"
            score = _EVALUATOR.evaluate(hole_ints, board_ints)
            counts[category_from_score(score)] += 1
            total = 1
        else:
            combinations = _combinations_count(len(unknown), needed)
            if combinations <= EXACT_COMBINATION_LIMIT:
                method = "exact"
                for extra in itertools.combinations(unknown_ints, needed):
                    score = _EVALUATOR.evaluate(hole_ints, board_ints + list(extra))
                    counts[category_from_score(score)] += 1
                    total += 1
            else:
                method = "monte_carlo"
                rng = random.Random()
                for _ in range(iterations):
                    extra = rng.sample(unknown_ints, needed)
                    score = _EVALUATOR.evaluate(hole_ints, board_ints + extra)
                    counts[category_from_score(score)] += 1
                    total += 1

        elapsed_ms = (time.perf_counter() - start) * 1000.0
        distribution = []
        for item in HAND_CATEGORIES:
            if item["key"] == "incomplete":
                continue
            count = counts.get(item["key"], 0)
            distribution.append(
                {
                    "key": item["key"],
                    "name": item["name"],
                    "short": item["short"],
                    "color": item["color"],
                    "count": count,
                    "probability": (count / total) if total else 0.0,
                }
            )

        assessment = assess_hand(list(hero_hole) + list(community))
        current_best = assessment.to_dict() if assessment else None
        result = DistributionResult(
            hero_seat=hero_seat,
            method=method,
            iterations=iterations if method == "monte_carlo" else total,
            elapsed_ms=elapsed_ms,
            total=total,
            distribution=distribution,
            current_best=current_best,
            known_cards=codes,
            needed_board_cards=needed,
        )
        with self._lock:
            self._cache[key] = result
            self._cache.move_to_end(key)
            while len(self._cache) > _CACHE_LIMIT:
                self._cache.popitem(last=False)
        return result


def _combinations_count(n: int, k: int) -> int:
    if k < 0 or k > n:
        return 0
    if k == 0:
        return 1
    k = min(k, n - k)
    result = 1
    for index in range(1, k + 1):
        result = result * (n - k + index) // index
    return result


__all__ = [
    "DEFAULT_ITERATIONS",
    "DistributionResult",
    "HandDistributionAnalyzer",
    "MAX_ITERATIONS",
    "MIN_ITERATIONS",
]
