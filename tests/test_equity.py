"""Heads-up equity analysis tests."""

from __future__ import annotations

import pytest

from src.analysis.equity import EquityAnalyzer
from src.engine.cards import Card


def cards(value: str) -> list[Card]:
    return Card.parse_many(value.split())


def test_river_board_royal_flush_is_a_tie() -> None:
    analyzer = EquityAnalyzer()
    result = analyzer.analyze(
        cards("2c 3d"),
        cards("Ah Kh Qh Jh Th"),
    )
    assert result.method == "exact"
    assert result.samples == 990
    assert result.wins == 0
    assert result.ties == 990
    assert result.losses == 0
    assert result.equity == pytest.approx(0.5)
    assert result.std_error == 0.0


def test_river_hero_royal_flush_wins() -> None:
    analyzer = EquityAnalyzer()
    result = analyzer.analyze(
        cards("Th 9h"),
        cards("Ah Kh Qh Jh 2c"),
    )
    assert result.method == "exact"
    assert result.samples == 990
    assert result.wins == 990
    assert result.ties == 0
    assert result.losses == 0
    assert result.equity == pytest.approx(1.0)


def test_turn_exact_matches_reference() -> None:
    analyzer = EquityAnalyzer()
    result = analyzer.analyze(
        cards("Ah Kh"),
        cards("Qh Jh 2c 7d"),
    )
    assert result.method == "exact"
    assert result.samples == 45540
    assert result.equity == pytest.approx(0.6368357487922706, abs=1e-6)
    assert result.win_rate + result.tie_rate + result.lose_rate == pytest.approx(1.0)


def test_flop_uses_monte_carlo() -> None:
    analyzer = EquityAnalyzer()
    result = analyzer.analyze(
        cards("Ah Kh"),
        cards("Qh Jh 2c"),
        iterations=20000,
        seed=123,
    )
    assert result.method == "monte_carlo"
    assert result.samples == 20000
    assert result.needed_board_cards == 2
    assert result.equity == pytest.approx(0.7633, abs=0.02)
    assert result.std_error > 0.0
    assert 0.0 <= result.equity <= 1.0


def test_preflop_aces_are_strong() -> None:
    analyzer = EquityAnalyzer()
    result = analyzer.analyze(
        cards("Ah Ad"),
        [],
        iterations=20000,
        seed=7,
    )
    assert result.method == "monte_carlo"
    assert result.samples == 20000
    assert result.needed_board_cards == 5
    assert result.equity == pytest.approx(0.852, abs=0.02)


def test_duplicate_cards_are_rejected() -> None:
    analyzer = EquityAnalyzer()
    with pytest.raises(ValueError):
        analyzer.analyze(cards("Ah Kh"), cards("Ah Jh 2c"))


def test_cache_hit_is_marked() -> None:
    analyzer = EquityAnalyzer()
    first = analyzer.analyze(cards("Th 9h"), cards("Ah Kh Qh Jh 2c"))
    second = analyzer.analyze(cards("Th 9h"), cards("Ah Kh Qh Jh 2c"))
    assert first.cached is False
    assert second.cached is True
    assert second.equity == first.equity
    assert second.elapsed_ms == 0.0


def test_llm_payload_uses_decimal_equity() -> None:
    analyzer = EquityAnalyzer()
    result = analyzer.analyze(cards("Th 9h"), cards("Ah Kh Qh Jh 2c"))
    payload = result.to_llm_dict()
    assert payload["equity"] == pytest.approx(1.0)
    assert payload["opponent"] == "random"
    assert "平局" in payload["note"]
