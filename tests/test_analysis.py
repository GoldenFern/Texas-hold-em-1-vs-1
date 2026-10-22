"""Hand-distribution analysis tests."""

from __future__ import annotations

import math

import pytest

from src.analysis.hand_distribution import HandDistributionAnalyzer
from src.engine.cards import Card


def cards(*codes: str) -> list[Card]:
    return [Card.from_str(code) for code in codes]


def test_preflop_monte_carlo_probabilities_sum_to_one() -> None:
    analyzer = HandDistributionAnalyzer()
    result = analyzer.analyze(cards("Ah", "Kh"), [], iterations=4000)
    assert result.method == "monte_carlo"
    assert result.total == 4000
    assert math.isclose(sum(item["probability"] for item in result.distribution), 1.0, rel_tol=1e-9)
    assert result.current_best is None
    assert result.needed_board_cards == 5


def test_flop_uses_exact_enumeration() -> None:
    analyzer = HandDistributionAnalyzer()
    result = analyzer.analyze(cards("Ah", "Kh"), cards("Qh", "Jh", "2c"), iterations=30000)
    assert result.method == "exact"
    assert result.total == 1081  # C(47, 2)
    assert math.isclose(sum(item["probability"] for item in result.distribution), 1.0, rel_tol=1e-9)
    assert result.current_best is not None
    assert result.current_best["category"] == "high_card"


def test_river_is_deterministic() -> None:
    analyzer = HandDistributionAnalyzer()
    result = analyzer.analyze(
        cards("Ah", "Kh"),
        cards("Qh", "Jh", "2c", "7d", "3s"),
        iterations=30000,
    )
    assert result.method == "exact"
    assert result.total == 1
    positive = [item for item in result.distribution if item["probability"] > 0]
    assert len(positive) == 1
    assert positive[0]["key"] == "high_card"


def test_duplicate_cards_are_rejected() -> None:
    analyzer = HandDistributionAnalyzer()
    with pytest.raises(ValueError):
        analyzer.analyze(cards("Ah", "Ah"), [])


def test_cache_returns_same_result() -> None:
    analyzer = HandDistributionAnalyzer()
    first = analyzer.analyze(cards("Ah", "Kh"), cards("Qh", "Jh", "2c"))
    second = analyzer.analyze(cards("Ah", "Kh"), cards("Qh", "Jh", "2c"))
    assert first is second


def test_cache_key_includes_hero_seat() -> None:
    analyzer = HandDistributionAnalyzer()
    hero = cards("Ah", "Kh")
    board = cards("Qh", "Jh", "2c")
    seat0 = analyzer.analyze(hero, board, hero_seat=0)
    seat1 = analyzer.analyze(hero, board, hero_seat=1)
    assert seat0 is not seat1
    assert seat0.hero_seat == 0
    assert seat1.hero_seat == 1


def test_cached_result_lists_are_copies() -> None:
    analyzer = HandDistributionAnalyzer()
    result = analyzer.analyze(cards("Ah", "Kh"), cards("Qh", "Jh", "2c"))
    payload = result.to_dict()
    payload["distribution"].clear()
    payload["known_cards"].clear()
    again = analyzer.analyze(cards("Ah", "Kh"), cards("Qh", "Jh", "2c"))
    assert again.distribution
    assert again.known_cards


def test_cache_evicts_least_recently_used(monkeypatch) -> None:
    import src.analysis.hand_distribution as module

    monkeypatch.setattr(module, "_CACHE_LIMIT", 2)
    analyzer = HandDistributionAnalyzer()
    first = analyzer.analyze(cards("Ah", "Kh"), cards("Qh", "Jh", "2c"))
    second = analyzer.analyze(cards("9s", "8s"), cards("7c", "6c", "4d"))
    analyzer.analyze(cards("Ah", "Kh"), cards("Qh", "Jh", "2c"))
    analyzer.analyze(cards("5s", "5h"), cards("3c", "3d", "2s"))
    assert analyzer.analyze(cards("Ah", "Kh"), cards("Qh", "Jh", "2c")) is first
    assert analyzer.analyze(cards("9s", "8s"), cards("7c", "6c", "4d")) is not second
