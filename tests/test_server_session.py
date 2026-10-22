"""Server session lifecycle tests without a real network server."""

from __future__ import annotations

import pytest
import time

from src.analysis.hand_distribution import HandDistributionAnalyzer
from src.llm.client import LLMClient
from src.llm.providers import ProviderRegistry
from src.server.session import GameSession


class FakeSocketIO:
    def __init__(self) -> None:
        self.events: list[tuple] = []

    def emit(self, *args, **kwargs) -> None:
        self.events.append((args, kwargs))


def make_session() -> GameSession:
    registry = ProviderRegistry()
    return GameSession(
        FakeSocketIO(),
        registry,
        LLMClient(registry, connect_timeout=1, read_timeout=1),
        HandDistributionAnalyzer(),
    )


def human_vs_random() -> dict:
    return {
        "seats": [
            {"kind": "human", "name": "You"},
            {"kind": "bot", "name": "RLCard", "bot": {"agent_type": "random"}},
        ],
        "rules": {
            "starting_chips": 1000,
            "small_blind": 5,
            "big_blind": 10,
            "auto_rebuy": False,
            "rebuy_amount": 1000,
            "max_hands": 0,
            "seed": 42,
            "god_view": False,
        },
    }


def test_failed_bot_build_keeps_running_match(tmp_path) -> None:
    session = make_session()
    try:
        session.start_match(human_vs_random())
        old_match_id = session.match_id
        old_game = session.game
        assert old_game is not None
        assert session._thread is not None and session._thread.is_alive()

        bad_model = tmp_path / "bad.pth"
        bad_model.write_bytes(b"not a checkpoint")
        payload = human_vs_random()
        payload["seats"][1] = {
            "kind": "bot",
            "name": "Project",
            "bot": {"agent_type": "project_dqn", "model_path": str(bad_model)},
        }
        with pytest.raises(ValueError):
            session.start_match(payload)

        assert session.match_id == old_match_id
        assert session.game is old_game
        assert session._thread is not None and session._thread.is_alive()
    finally:
        session.shutdown()


def test_human_token_is_set_when_state_is_broadcast() -> None:
    session = make_session()
    try:
        session.start_match(human_vs_random())
        for _ in range(50):
            if session._human_token is not None:
                break
            time.sleep(0.01)
        assert session._human_token is not None
    finally:
        session.shutdown()


def test_bot_delay_defaults_and_validates_range() -> None:
    session = make_session()
    try:
        seats = session._parse_seats(human_vs_random())
        assert seats[1].bot_delay_seconds == 3.0

        for value in (0, 2.5, 10):
            payload = human_vs_random()
            payload["seats"][1]["bot"]["delay_seconds"] = value
            seats = session._parse_seats(payload)
            assert seats[1].bot_delay_seconds == float(value)

        for value in (-0.5, 10.5, "fast", True):
            payload = human_vs_random()
            payload["seats"][1]["bot"]["delay_seconds"] = value
            with pytest.raises(ValueError):
                session._parse_seats(payload)
    finally:
        session.shutdown()


def test_rule_normalization_is_strict_and_explicit() -> None:
    session = make_session()
    rules = session._normalize_rules({"god_view": "false", "auto_rebuy": "true"})
    assert rules["god_view"] is False
    assert rules["auto_rebuy"] is True
    with pytest.raises(ValueError):
        session._normalize_rules({"god_view": "maybe"})
    with pytest.raises(ValueError):
        session._normalize_rules({"starting_chips": 50})
    with pytest.raises(ValueError):
        session._normalize_rules({"small_blind": 20, "big_blind": 10})
    with pytest.raises(ValueError):
        session._normalize_rules({"starting_chips": 1000.5})


def test_next_hand_requires_live_thread() -> None:
    session = make_session()
    try:
        session.start_match(human_vs_random())
        assert session._thread is not None
        session._thread = None
        with pytest.raises(ValueError):
            session.next_hand()
    finally:
        session.shutdown()
