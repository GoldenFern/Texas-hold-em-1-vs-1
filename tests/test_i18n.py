"""Runtime localization tests for server, engine and bot messages."""

from __future__ import annotations

import pytest

from src.analysis.hand_distribution import HandDistributionAnalyzer
from src.engine.actions import Action
from src.engine.heads_up import HeadsUpGame
from src.i18n import normalize_locale, tr
from src.llm.client import LLMClient
from src.llm.providers import ProviderRegistry
from src.llm.table_talk import normalize_table_talk
from src.server.session import GameSession


def test_normalize_locale_accepts_headers_and_aliases() -> None:
    assert normalize_locale("zh-CN") == "zh-CN"
    assert normalize_locale("zh") == "zh-CN"
    assert normalize_locale("en-US,en;q=0.9") == "en-US"
    assert normalize_locale("fr-FR,zh;q=0.8") == "zh-CN"
    assert normalize_locale("fr-FR") == "en-US"
    assert normalize_locale(None, "zh-CN") == "zh-CN"


def test_translation_catalog_interpolates_and_falls_back() -> None:
    assert tr("en-US", "session.rule.range", key="starting_chips", min=100, max=1000) == (
        "starting_chips must be between 100 and 1000"
    )
    assert tr("zh-CN", "session.rule.blinds") == "大盲必须不小于小盲"
    assert tr("fr-FR", "session.rule.blinds") == "The big blind must be at least the small blind"
    assert tr("en-US", "missing.key") == "missing.key"


def test_engine_and_payload_errors_follow_locale() -> None:
    with pytest.raises(ValueError, match="positive integer"):
        HeadsUpGame(starting_chips="abc", locale="en-US")

    with pytest.raises(ValueError, match="Unknown action"):
        Action.from_payload(0, {"type": "nope"}, locale="en-US")

    with pytest.raises(ValueError, match="must be positive"):
        HeadsUpGame(starting_chips=0, locale="en-US")


def test_table_talk_and_provider_errors_follow_locale() -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        normalize_table_talk("  ", 20, locale="en-US")

    with pytest.raises(ValueError, match="Unknown provider"):
        ProviderRegistry().resolve("missing", "missing", locale="en-US")


def test_server_session_uses_its_ui_locale() -> None:
    registry = ProviderRegistry()
    session = GameSession(
        None,
        registry,
        LLMClient(registry, connect_timeout=1, read_timeout=1),
        HandDistributionAnalyzer(),
        ui_locale="en-US",
    )
    try:
        with pytest.raises(ValueError, match="between 100 and 1000"):
            session._normalize_rules({"starting_chips": 50})
        session.set_ui_locale("zh-CN")
        with pytest.raises(ValueError, match="必须在"):
            session._normalize_rules({"starting_chips": 50})
    finally:
        session.shutdown()
