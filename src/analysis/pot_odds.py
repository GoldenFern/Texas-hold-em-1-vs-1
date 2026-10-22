"""Static call EV and pot-odds helpers."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class CallEvResult:
    """Result of a static call-EV calculation."""

    pot: int
    to_call: int
    chips: int
    call_amount: int
    refund: int
    effective_pot: int
    equity: float
    ev_call: float
    ev_fold: float
    breakeven_equity: float
    pot_to_call_ratio: Optional[float]
    edge: float
    static_positive: bool

    def to_dict(self) -> dict:
        return {
            "pot": self.pot,
            "to_call": self.to_call,
            "chips": self.chips,
            "call_amount": self.call_amount,
            "refund": self.refund,
            "effective_pot": self.effective_pot,
            "equity": self.equity,
            "ev_call": self.ev_call,
            "ev_fold": self.ev_fold,
            "breakeven_equity": self.breakeven_equity,
            "pot_to_call_ratio": self.pot_to_call_ratio,
            "edge": self.edge,
            "static_positive": self.static_positive,
        }

    def to_llm_dict(self) -> dict:
        """Compact result for a model tool message."""
        return {
            "equity": round(self.equity, 4),
            "ev_call": round(self.ev_call, 2),
            "ev_fold": 0.0,
            "breakeven_equity": round(self.breakeven_equity, 4),
            "edge": round(self.edge, 4),
            "pot": self.pot,
            "to_call": self.to_call,
            "call_amount": self.call_amount,
            "effective_pot": self.effective_pot,
            "refund": self.refund,
            "pot_to_call_ratio": (
                round(self.pot_to_call_ratio, 3)
                if self.pot_to_call_ratio is not None
                else None
            ),
            "static_positive": self.static_positive,
            "note": (
                "静态跟注 EV，假设跟注后不再下注；翻牌和转牌还要考虑"
                "隐含赔率、反向隐含赔率和权益实现率。"
                "pot_to_call_ratio 是底池/跟注的倍数（3.0 表示 3:1），"
                "不是百分比；临界胜率看 breakeven_equity。"
            ),
        }


def analyze_call(pot: int, to_call: int, chips: int, equity: float) -> CallEvResult:
    """Calculate the static EV of calling.

    Args:
        pot: Current pot, already including the opponent's bet.
        to_call: Chips needed to match the current bet.
        chips: Chips behind before calling.
        equity: Showdown equity as a 0 to 1 decimal.

    Returns:
        The call EV and the pot-odds threshold.

    Raises:
        ValueError: If the inputs are not a legal calling spot.
    """
    if pot < 0:
        raise ValueError("底池不能为负")
    if to_call <= 0:
        raise ValueError("to_call 必须大于 0")
    if chips < 0:
        raise ValueError("筹码不能为负")
    if not math.isfinite(equity) or not 0.0 <= equity <= 1.0:
        raise ValueError("equity 必须是 0 到 1 之间的小数")

    call_amount = min(int(to_call), int(chips))
    if call_amount <= 0:
        raise ValueError("没有可跟注的筹码")
    refund = int(to_call) - call_amount
    effective_pot = int(pot) + 2 * call_amount - int(to_call)
    if effective_pot <= 0:
        raise ValueError("有效底池必须大于 0")

    ev_call = equity * effective_pot - call_amount
    breakeven = call_amount / effective_pot
    pot_to_call_ratio = pot / call_amount
    return CallEvResult(
        pot=int(pot),
        to_call=int(to_call),
        chips=int(chips),
        call_amount=call_amount,
        refund=refund,
        effective_pot=effective_pot,
        equity=float(equity),
        ev_call=float(ev_call),
        ev_fold=0.0,
        breakeven_equity=float(breakeven),
        pot_to_call_ratio=float(pot_to_call_ratio),
        edge=float(equity - breakeven),
        static_positive=bool(ev_call > 0),
    )


__all__ = ["CallEvResult", "analyze_call"]
