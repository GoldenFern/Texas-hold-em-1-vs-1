"""Heads-up equity against a uniformly random opponent hand.

The unknown cards (the opponent's two hole cards and any undealt board
cards) are treated as a uniformly random joint selection from the remaining
deck. Equity is the expected share of the pot at showdown:

    equity = (wins + ties / 2) / deals

River and turn spots are small enough to enumerate exactly. Flop and
preflop spots use Monte Carlo. The cache follows the same LRU pattern as
``hand_distribution.py``.
"""

from __future__ import annotations

import itertools
import math
import random
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, replace
from typing import List, Optional, Sequence

from src.engine.cards import FULL_DECK, Card
from src.engine.evaluator import evaluate_score, treys_int

DEFAULT_ITERATIONS = 20000
MIN_ITERATIONS = 2000
MAX_ITERATIONS = 200000
EXACT_DEAL_LIMIT = 100000
_CACHE_LIMIT = 256


@dataclass(frozen=True)
class EquityResult:
    """Result of a heads-up equity calculation."""

    hero_seat: int
    method: str
    samples: int
    elapsed_ms: float
    wins: int
    ties: int
    losses: int
    equity: float
    win_rate: float
    tie_rate: float
    lose_rate: float
    std_error: float
    known_cards: List[str]
    needed_board_cards: int
    cached: bool = False

    @property
    def total(self) -> int:
        """Alias used by the existing analysis panel."""
        return self.samples

    def to_dict(self) -> dict:
        return {
            "hero_seat": self.hero_seat,
            "method": self.method,
            "samples": self.samples,
            "total": self.samples,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "wins": self.wins,
            "ties": self.ties,
            "losses": self.losses,
            "equity": self.equity,
            "win": self.win_rate,
            "tie": self.tie_rate,
            "lose": self.lose_rate,
            "std_error": self.std_error,
            "known_cards": list(self.known_cards),
            "needed_board_cards": self.needed_board_cards,
            "opponent": "random",
            "cached": self.cached,
        }

    def to_llm_dict(self) -> dict:
        """Compact result for a model tool message."""
        return {
            "equity": round(self.equity, 4),
            "win": round(self.win_rate, 4),
            "tie": round(self.tie_rate, 4),
            "lose": round(self.lose_rate, 4),
            "method": self.method,
            "samples": self.samples,
            "std_error": round(self.std_error, 4),
            "opponent": "random",
            "note": (
                "equity/win/tie/lose 都是 0 到 1 的小数，不是百分数；"
                "equity = 胜率 + 平局率/2；对手底牌和未翻开公共牌按剩余牌"
                "均匀随机；这是摊牌期望份额，不是当前牌型强度。"
                "method=exact 时 samples 是等概率组合数，不是蒙特卡洛采样次数。"
            ),
        }


class EquityAnalyzer:
    """Compute and cache heads-up equity against a random hand."""

    def __init__(self) -> None:
        self._cache: "OrderedDict[tuple, EquityResult]" = OrderedDict()
        self._lock = threading.RLock()

    def analyze(
        self,
        hero_hole: Sequence[Card],
        community: Sequence[Card],
        hero_seat: int = 0,
        iterations: int = DEFAULT_ITERATIONS,
        seed: Optional[int] = None,
    ) -> EquityResult:
        """Calculate equity for the current spot.

        Args:
            hero_hole: The hero's two hole cards.
            community: The board cards already dealt (0 to 5).
            hero_seat: Seat index stored in the result.
            iterations: Monte Carlo samples for large spots.
            seed: Optional RNG seed for deterministic tests.

        Returns:
            The equity result, possibly served from the LRU cache.

        Raises:
            ValueError: If the cards are invalid or the spot is impossible.
        """
        if len(hero_hole) != 2:
            raise ValueError("需要恰好两张底牌")
        if len(community) > 5:
            raise ValueError("公共牌不能超过五张")
        iterations = max(MIN_ITERATIONS, min(int(iterations), MAX_ITERATIONS))

        known = list(hero_hole) + list(community)
        codes = [card.code for card in known]
        if len(set(codes)) != len(codes):
            raise ValueError("已知牌中存在重复牌")

        known_set = set(codes)
        unknown = [card for card in FULL_DECK if card.code not in known_set]
        needed = 5 - len(community)
        total_deals = _deal_count(len(unknown), needed)
        if total_deals <= 0:
            raise ValueError("剩余牌不足以完成公共牌和对手底牌")

        method = "exact" if total_deals <= EXACT_DEAL_LIMIT else "monte_carlo"
        key = (
            int(hero_seat),
            tuple(sorted(card.code for card in hero_hole)),
            tuple(sorted(card.code for card in community)),
            0 if method == "exact" else iterations,
            None if method == "exact" else seed,
        )
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return replace(cached, cached=True, elapsed_ms=0.0)

        start = time.perf_counter()
        hole_ints = [treys_int(card) for card in hero_hole]
        board_ints = [treys_int(card) for card in community]
        unknown_ints = [treys_int(card) for card in unknown]

        if method == "exact":
            wins, ties, losses = _enumerate(
                hole_ints, board_ints, unknown_ints, needed
            )
            samples = total_deals
            equity = (wins + ties / 2) / samples if samples else 0.0
            std_error = 0.0
        else:
            wins, ties, losses = _sample(
                hole_ints,
                board_ints,
                unknown_ints,
                needed,
                iterations,
                seed,
            )
            samples = iterations
            equity = (wins + ties / 2) / samples if samples else 0.0
            mean_square = (wins + ties * 0.25) / samples if samples else 0.0
            variance = max(0.0, mean_square - equity * equity)
            std_error = math.sqrt(variance / samples) if samples else 0.0

        elapsed_ms = (time.perf_counter() - start) * 1000.0
        result = EquityResult(
            hero_seat=int(hero_seat),
            method=method,
            samples=samples,
            elapsed_ms=elapsed_ms,
            wins=wins,
            ties=ties,
            losses=losses,
            equity=equity,
            win_rate=wins / samples if samples else 0.0,
            tie_rate=ties / samples if samples else 0.0,
            lose_rate=losses / samples if samples else 0.0,
            std_error=std_error,
            known_cards=codes,
            needed_board_cards=needed,
        )
        with self._lock:
            self._cache[key] = result
            self._cache.move_to_end(key)
            while len(self._cache) > _CACHE_LIMIT:
                self._cache.popitem(last=False)
        return result

    def clear(self) -> None:
        """Drop all cached results."""
        with self._lock:
            self._cache.clear()


def _deal_count(unknown: int, needed: int) -> int:
    """Number of (opponent hand, board runout) deals for a spot."""
    if unknown < 2 + needed:
        return 0
    return math.comb(unknown, 2) * math.comb(unknown - 2, needed)


def _enumerate(
    hole_ints: Sequence[int],
    board_ints: Sequence[int],
    unknown_ints: Sequence[int],
    needed: int,
) -> tuple[int, int, int]:
    """Enumerate every opponent hand and board runout exactly."""
    wins = ties = losses = 0
    n = len(unknown_ints)
    evaluate = evaluate_score
    for runout_idx in itertools.combinations(range(n), needed):
        runout_set = set(runout_idx)
        board_full = list(board_ints) + [unknown_ints[i] for i in runout_idx]
        hero_score = evaluate(hole_ints, board_full)
        rest = [unknown_ints[i] for i in range(n) if i not in runout_set]
        for opp in itertools.combinations(rest, 2):
            opp_score = evaluate(opp, board_full)
            if hero_score < opp_score:
                wins += 1
            elif hero_score == opp_score:
                ties += 1
            else:
                losses += 1
    return wins, ties, losses


def _sample(
    hole_ints: Sequence[int],
    board_ints: Sequence[int],
    unknown_ints: Sequence[int],
    needed: int,
    iterations: int,
    seed: Optional[int],
) -> tuple[int, int, int]:
    """Sample opponent hands and board runouts without replacement."""
    rng = random.Random(seed)
    wins = ties = losses = 0
    evaluate = evaluate_score
    draw = 2 + needed
    for _ in range(iterations):
        cards = rng.sample(unknown_ints, draw)
        opp = cards[:2]
        board_full = list(board_ints) + cards[2:]
        hero_score = evaluate(hole_ints, board_full)
        opp_score = evaluate(opp, board_full)
        if hero_score < opp_score:
            wins += 1
        elif hero_score == opp_score:
            ties += 1
        else:
            losses += 1
    return wins, ties, losses


__all__ = [
    "DEFAULT_ITERATIONS",
    "EXACT_DEAL_LIMIT",
    "EquityAnalyzer",
    "EquityResult",
    "MAX_ITERATIONS",
    "MIN_ITERATIONS",
]
