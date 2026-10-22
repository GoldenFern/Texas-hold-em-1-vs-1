"""Table-talk text normalization and runtime limits."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


_ZERO_WIDTH = re.compile(r"[\u200b-\u200d\u2060\ufeff]")
_BIDI = re.compile(r"[\u202a-\u202e\u2066-\u2069]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class TableTalkLimits:
    """Per-match table-talk limits derived from ``config/game.json``."""

    max_chars: int = 160
    max_per_turn: int = 2
    max_per_hand: int = 6
    history_limit: int = 6
    snapshot_limit: int = 40

    @classmethod
    def from_runtime(cls, runtime: dict | None) -> "TableTalkLimits":
        """Read limits from the LLM runtime config, falling back to defaults."""
        runtime = runtime or {}

        def value(key: str, default: int, upper: int) -> int:
            try:
                parsed = int(runtime.get(key, default))
            except (TypeError, ValueError):
                return default
            return max(1, min(upper, parsed))

        return cls(
            max_chars=value("table_talk_max_chars", 160, 1000),
            max_per_turn=value("table_talk_max_per_turn", 2, 20),
            max_per_hand=value("table_talk_max_per_hand", 6, 100),
            history_limit=value("table_talk_history_limit", 6, 30),
            snapshot_limit=value("table_talk_snapshot_limit", 40, 300),
        )


def normalize_table_talk(text: str, max_chars: int = 160) -> str:
    """Normalize one table-talk message into a single safe line.

    Args:
        text: Raw text produced by the model.
        max_chars: Maximum number of characters allowed after normalization.

    Returns:
        A single-line string without control or zero-width characters.

    Raises:
        ValueError: If the text is empty or longer than ``max_chars``.
    """
    if not isinstance(text, str):
        raise ValueError("喊话内容必须是文本")
    normalized = unicodedata.normalize("NFC", text)
    normalized = _ZERO_WIDTH.sub("", normalized)
    normalized = _BIDI.sub("", normalized)
    normalized = _CONTROL.sub(" ", normalized)
    normalized = _WHITESPACE.sub(" ", normalized).strip()
    if not normalized:
        raise ValueError("喊话内容不能为空")
    if len(normalized) > max_chars:
        raise ValueError(f"喊话不能超过 {max_chars} 字")
    return normalized


__all__ = ["TableTalkLimits", "normalize_table_talk"]
