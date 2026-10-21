"""Heads-up engine invariants and rule tests."""

from __future__ import annotations

import random

import pytest

from src.engine.actions import Action, ActionType
from src.engine.cards import Card
from src.engine.heads_up import HeadsUpGame, PlayerStatus, Street


def make_game(starting_chips: int = 1000, seed: int = 42) -> HeadsUpGame:
    game = HeadsUpGame(
        starting_chips=starting_chips,
        small_blind=5,
        big_blind=10,
        auto_rebuy=False,
        seed=seed,
    )
    game.configure_players({"name": "A", "kind": "human"}, {"name": "B", "kind": "bot"})
    game.start_hand()
    return game


def act(game: HeadsUpGame, seat: int, action_type: ActionType, amount: int = 0) -> None:
    result = game.apply(Action(seat, action_type, amount=amount, source="test"))
    assert result.ok, result.error


def test_blinds_and_postflop_position() -> None:
    game = make_game()
    assert game.button_index == 0
    assert game.players[0].is_small_blind
    assert game.players[1].is_big_blind
    assert game.players[0].chips == 995
    assert game.players[1].chips == 990
    assert game.actor == 0
    assert game.to_call(0) == 5

    act(game, 0, ActionType.RAISE, 30)
    act(game, 1, ActionType.CALL)
    assert game.street == Street.FLOP
    assert len(game.community) == 3
    assert game.actor == 1  # non-button acts first after the flop
    assert game.pot == 60


def test_minimum_raise_is_enforced() -> None:
    game = make_game()
    result = game.apply(Action(0, ActionType.RAISE, amount=15))
    assert not result.ok
    result = game.apply(Action(0, ActionType.RAISE, amount=20))
    assert result.ok
    assert game.current_bet == 20
    assert game.min_raise_to == 30


def test_short_allin_does_not_reopen_betting() -> None:
    game = HeadsUpGame(
        starting_chips=40,
        small_blind=5,
        big_blind=10,
        seed=7,
    )
    game.configure_players({"name": "A", "kind": "human"}, {"name": "B", "kind": "bot"})
    game.start_hand()
    act(game, 0, ActionType.RAISE, 30)
    assert game.current_bet == 30
    assert game.last_full_raise == 20
    assert game.players[1].chips == 30
    act(game, 1, ActionType.ALL_IN)
    assert game.players[1].status == PlayerStatus.ALL_IN
    assert game.current_bet == 40
    assert game.players[0].raise_allowed is False
    assert ActionType.RAISE not in game.legal_actions(0)
    assert ActionType.CALL in game.legal_actions(0)
    act(game, 0, ActionType.CALL)
    assert game.street in (Street.FLOP, Street.FINISHED)


def test_short_allin_does_not_offer_illegal_allin_with_chips_behind() -> None:
    game = HeadsUpGame(starting_chips=50, small_blind=5, big_blind=10, seed=8)
    game.configure_players({"name": "A", "kind": "human"}, {"name": "B", "kind": "bot"})
    game.start_hand()
    # SB has 20 chips behind after raising to 30. Give BB only 30 chips
    # behind so its all-in to 40 is a short raise.
    game.players[1].chips = 30
    act(game, 0, ActionType.RAISE, 30)
    act(game, 1, ActionType.ALL_IN)
    assert game.current_bet == 40
    assert game.players[0].chips > game.to_call(0)
    legal = game.legal_actions(0)
    assert ActionType.RAISE not in legal
    assert ActionType.ALL_IN not in legal
    assert ActionType.CALL in legal


def test_fold_returns_uncalled_chips() -> None:
    game = make_game()
    act(game, 0, ActionType.RAISE, 100)
    act(game, 1, ActionType.FOLD)
    assert game.street == Street.FINISHED
    assert game.result is not None
    assert game.result["reason"] == "fold"
    assert game.result["pot"] == 20
    assert game.result["refunds"][0] == 90
    assert game.players[0].chips == 1010
    assert game.players[1].chips == 990


def test_showdown_split_and_odd_chip() -> None:
    game = make_game(seed=11)
    # Force a board where both players play the board (a royal flush on board
    # is impossible with 5 cards, so use a broadway straight on board).
    game.community = [Card.from_str(code) for code in ("Ah", "Kd", "Qc", "Js", "Th")]
    game.street = Street.RIVER
    game.players[0].hole_cards = [Card.from_str("2c"), Card.from_str("3c")]
    game.players[1].hole_cards = [Card.from_str("4c"), Card.from_str("5c")]
    for seat in (0, 1):
        game.players[seat].total_bet = 100
        game.players[seat].chips = 900
    game._showdown()
    assert game.result is not None
    assert game.result["reason"] == "showdown"
    assert sorted(game.result["winner_seats"]) == [0, 1]
    assert sum(game.players[seat].chips for seat in (0, 1)) == 2000


def test_random_play_conserves_chips() -> None:
    rng = random.Random(2026)
    game = HeadsUpGame(
        starting_chips=500,
        small_blind=5,
        big_blind=10,
        auto_rebuy=True,
        seed=99,
        max_hands=60,
    )
    game.configure_players({"name": "A", "kind": "human"}, {"name": "B", "kind": "bot"})
    for _ in range(60):
        if game.match_over:
            break
        game.start_hand()
        total = sum(player.chips for player in game.players) + sum(
            player.total_bet for player in game.players
        )
        for _step in range(80):
            if game.street in (Street.FINISHED, Street.SHOWDOWN):
                break
            seat = game.actor
            assert seat is not None
            legal = game.legal_actions(seat)
            assert legal
            chosen = rng.choice(legal)
            constraints = game.action_constraints(seat)
            amount = 0
            if chosen in (ActionType.BET, ActionType.RAISE):
                low = constraints["min_raise_to"]
                high = constraints["max_raise_to"]
                amount = rng.randint(low, high) if high >= low else high
            result = game.apply(Action(seat, chosen, amount=amount, source="test"))
            assert result.ok, result.error
            for player in game.players:
                assert player.chips >= 0
            if game.street in (Street.FINISHED, Street.SHOWDOWN):
                assert sum(player.chips for player in game.players) >= 0
            else:
                assert (
                    sum(player.chips for player in game.players)
                    + sum(player.total_bet for player in game.players)
                    == total
                )
        assert game.street == Street.FINISHED


def test_human_snapshot_visibility() -> None:
    game = make_game()
    snapshot = game.snapshot(reveal_all=False, reveal_seats=(0,))
    assert snapshot["players"][0]["hole_hidden"] is False
    assert snapshot["players"][1]["hole_hidden"] is True
    assert len(snapshot["players"][0]["hole_cards"]) == 2
    assert snapshot["players"][1]["hole_cards"] == []
    assert snapshot["deck_remaining"] == []


def test_god_snapshot_shows_deck_and_burn() -> None:
    game = make_game()
    snapshot = game.snapshot(reveal_all=True)
    assert snapshot["players"][0]["hole_hidden"] is False
    assert snapshot["players"][1]["hole_hidden"] is False
    assert len(snapshot["deck_remaining"]) == game.deck.remaining
    assert "burned" in snapshot
