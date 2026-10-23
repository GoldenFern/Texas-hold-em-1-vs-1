"""Action data structures shared by the engine, agents and server."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict

from src.i18n import tr


class ActionType(str, Enum):
    FOLD = "fold"
    CHECK = "check"
    CALL = "call"
    BET = "bet"
    RAISE = "raise"
    ALL_IN = "all_in"

    @property
    def label(self) -> str:
        return {
            ActionType.FOLD: "弃牌",
            ActionType.CHECK: "过牌",
            ActionType.CALL: "跟注",
            ActionType.BET: "下注",
            ActionType.RAISE: "加注",
            ActionType.ALL_IN: "全下",
        }[self]


ACTION_ORDER = [
    ActionType.FOLD,
    ActionType.CHECK,
    ActionType.CALL,
    ActionType.BET,
    ActionType.RAISE,
    ActionType.ALL_IN,
]


@dataclass
class Action:
    """A player action.

    For ``BET`` / ``RAISE``, ``amount`` is the **total street bet after the
    action** (raise-to semantics). For other actions it is ignored.
    """

    seat: int
    type: ActionType
    amount: int = 0
    source: str = "human"
    auto: bool = False
    meta: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(
        cls,
        seat: int,
        payload: dict,
        source: str = "human",
        locale: str = "zh-CN",
    ) -> "Action":
        raw_type = str(payload.get("type") or payload.get("action") or "").strip().lower()
        aliases = {
            "x": "check",
            "pass": "check",
            "bet": "bet",
            "raise": "raise",
            "call": "call",
            "fold": "fold",
            "allin": "all_in",
            "all-in": "all_in",
            "all_in": "all_in",
        }
        normalized = aliases.get(raw_type, raw_type)
        if normalized not in {item.value for item in ActionType}:
            raise ValueError(tr(locale, "engine.payload.unknown_action", action=repr(raw_type)))
        amount = payload.get("amount", 0)
        if amount is None:
            amount = 0
        if isinstance(amount, bool):
            raise ValueError(tr(locale, "engine.payload.amount_number"))
        if isinstance(amount, str):
            text = amount.strip().replace(",", "")
            if not text:
                amount = 0
            else:
                try:
                    amount = float(text)
                except ValueError as exc:
                    raise ValueError(tr(locale, "engine.payload.amount_number")) from exc
        if isinstance(amount, float):
            if not amount.is_integer():
                raise ValueError(tr(locale, "engine.payload.amount_integer"))
            amount = int(amount)
        if not isinstance(amount, int):
            raise ValueError(tr(locale, "engine.payload.amount_integer"))
        return cls(seat=seat, type=ActionType(normalized), amount=amount, source=source)

    def to_dict(self) -> dict:
        return {
            "seat": self.seat,
            "type": self.type.value,
            "label": self.type.label,
            "amount": self.amount,
            "source": self.source,
            "auto": self.auto,
        }


__all__ = ["ACTION_ORDER", "Action", "ActionType"]
