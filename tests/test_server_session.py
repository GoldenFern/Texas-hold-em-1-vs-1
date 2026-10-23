"""Server session lifecycle tests without a real network server."""

from __future__ import annotations

import pytest
import time

from src.analysis.hand_distribution import HandDistributionAnalyzer
from src.engine.heads_up import HeadsUpGame
from src.llm.client import LLMClient
from src.llm.providers import ProviderRegistry
from src.server.session import GameSession, SeatConfig


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


def test_agent_locale_is_fixed_at_match_start() -> None:
    session = make_session()
    try:
        session.set_ui_locale("en-US")
        payload = human_vs_random()
        payload["agent_locale"] = "zh-CN"
        session.start_match(payload)
        assert session.agent_locale == "zh-CN"
        assert session.get_state()["match"]["agent_locale"] == "zh-CN"

        session.set_ui_locale("en-US")
        assert session.agent_locale == "zh-CN"
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


def test_table_talk_requires_two_llm_seats() -> None:
    llm = SeatConfig(kind="llm", name="A", provider_id="deepseek", model_id="deepseek-flash")
    other = SeatConfig(kind="llm", name="B", provider_id="deepseek", model_id="deepseek-flash")
    bot = SeatConfig(kind="bot", name="Bot")

    assert GameSession._resolve_table_talk([llm, other], True) is True
    assert GameSession._resolve_table_talk([llm, other], False) is False
    assert GameSession._resolve_table_talk([llm, bot], True) is False
    with pytest.raises(ValueError):
        GameSession._resolve_table_talk([llm, other], "yes")


def make_table_talk_session():
    session = make_session()
    game = HeadsUpGame(
        starting_chips=1000,
        small_blind=5,
        big_blind=10,
        seed=42,
    )
    game.configure_players(
        {"name": "Agent A", "kind": "llm"},
        {"name": "Agent B", "kind": "llm"},
    )
    game.start_hand()
    session.game = game
    session.match_id = "talk-match"
    session.table_talk_enabled = True
    session._generation = 0
    session._stop_event.clear()
    return session, game


def test_table_talk_records_event_and_delivers_once() -> None:
    session, game = make_table_talk_session()
    try:
        sender = game.actor
        assert sender is not None
        receiver = 1 - sender
        result = session._record_table_talk(0, sender, "  我这次\n不会再让你便宜看牌。  ", "turn-1")
        assert result["ok"] is True
        assert result["remaining_turn"] == 0
        entry = result["entry"]
        assert entry["text"] == "我这次 不会再让你便宜看牌。"
        assert entry["seat"] == sender
        assert entry["hand_id"] == game.hand_id
        assert entry["kind"] == "turn"

        talk_events = [
            args[1]
            for args, _kwargs in session.socketio.events
            if len(args) > 1
            and isinstance(args[1], dict)
            and args[1].get("type") == "table_talk"
        ]
        assert talk_events and talk_events[-1]["text"] == entry["text"]

        view = session._build_view(receiver)
        assert len(view.table_talk) == 1
        assert view.table_talk[0]["text"] == entry["text"]
        assert session._build_view(receiver).table_talk == []

        snapshot = session._snapshot_locked()
        assert snapshot["match"]["table_talk"] is True
        assert snapshot["table_talk"][-1]["text"] == entry["text"]
    finally:
        session.shutdown()


def test_table_talk_rejects_non_actor_and_enforces_turn_quota() -> None:
    session, game = make_table_talk_session()
    try:
        sender = game.actor
        assert sender is not None
        assert session._record_table_talk(0, sender, "施压。", "turn-1")["ok"] is True
        blocked = session._record_table_talk(0, sender, "再说一句。", "turn-1")
        assert blocked["ok"] is False
        assert "上限" in blocked["error"]
        assert session._record_table_talk(0, sender, "下一回合。", "turn-2")["ok"] is True
        wrong_turn = session._record_table_talk(0, 1 - sender, "抢话。", "turn-1")
        assert wrong_turn["ok"] is False
        assert "没有轮到" in wrong_turn["error"]
    finally:
        session.shutdown()


def test_final_table_talk_is_one_per_seat_and_marked() -> None:
    session, game = make_table_talk_session()
    try:
        sender = game.actor
        assert sender is not None
        game.match_over = True
        result = session._record_final_table_talk(0, sender, "这局打得不错，下次可没这么好运。")
        assert result["ok"] is True
        assert result["entry"]["kind"] == "final"
        assert result["entry"]["street"] == "finished"
        assert session._record_final_table_talk(0, sender, "再说一句。")["ok"] is False
        snapshot = session._snapshot_locked()
        assert snapshot["table_talk"][-1]["kind"] == "final"
    finally:
        session.shutdown()


def test_table_talk_normalizes_but_does_not_inspect_card_content() -> None:
    session, game = make_table_talk_session()
    try:
        sender = game.actor
        assert sender is not None
        result = session._record_table_talk(0, sender, "我拿着 A♥，你信吗？", "turn")
        assert result["ok"] is True
        assert result["entry"]["text"] == "我拿着 A♥，你信吗？"
    finally:
        session.shutdown()
