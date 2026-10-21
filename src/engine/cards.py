"""Card, rank, suit and deck primitives for heads-up Hold'em."""

from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum
from typing import Iterable, List


class Suit(str, Enum):
    """Four suits. Value order is clubs, diamonds, hearts, spades."""

    CLUBS = "c"
    DIAMONDS = "d"
    HEARTS = "h"
    SPADES = "s"

    @property
    def symbol(self) -> str:
        return {
            Suit.CLUBS: "♣",
            Suit.DIAMONDS: "♦",
            Suit.HEARTS: "♥",
            Suit.SPADES: "♠",
        }[self]

    @property
    def is_red(self) -> bool:
        return self in (Suit.HEARTS, Suit.DIAMONDS)


class Rank(int, Enum):
    """Card ranks, ace high (14)."""

    TWO = 2
    THREE = 3
    FOUR = 4
    FIVE = 5
    SIX = 6
    SEVEN = 7
    EIGHT = 8
    NINE = 9
    TEN = 10
    JACK = 11
    QUEEN = 12
    KING = 13
    ACE = 14

    @property
    def label(self) -> str:
        return {
            Rank.TWO: "2",
            Rank.THREE: "3",
            Rank.FOUR: "4",
            Rank.FIVE: "5",
            Rank.SIX: "6",
            Rank.SEVEN: "7",
            Rank.EIGHT: "8",
            Rank.NINE: "9",
            Rank.TEN: "T",
            Rank.JACK: "J",
            Rank.QUEEN: "Q",
            Rank.KING: "K",
            Rank.ACE: "A",
        }[self]


_RANK_FROM_CHAR = {
    "2": Rank.TWO,
    "3": Rank.THREE,
    "4": Rank.FOUR,
    "5": Rank.FIVE,
    "6": Rank.SIX,
    "7": Rank.SEVEN,
    "8": Rank.EIGHT,
    "9": Rank.NINE,
    "T": Rank.TEN,
    "10": Rank.TEN,
    "J": Rank.JACK,
    "Q": Rank.QUEEN,
    "K": Rank.KING,
    "A": Rank.ACE,
}

_SUIT_FROM_CHAR = {
    "c": Suit.CLUBS,
    "d": Suit.DIAMONDS,
    "h": Suit.HEARTS,
    "s": Suit.SPADES,
    "♣": Suit.CLUBS,
    "♦": Suit.DIAMONDS,
    "♥": Suit.HEARTS,
    "♠": Suit.SPADES,
}


@dataclass(frozen=True, slots=True)
class Card:
    """A single playing card."""

    rank: Rank
    suit: Suit

    @classmethod
    def from_str(cls, value: str) -> "Card":
        """Parse a compact card code such as ``Ah``, ``Td`` or ``A♥``."""
        text = value.strip()
        if len(text) < 2:
            raise ValueError(f"无效牌面: {value!r}")
        rank_char = text[:-1].upper()
        suit_char = text[-1].lower()
        if rank_char not in _RANK_FROM_CHAR:
            raise ValueError(f"无效点数: {value!r}")
        if suit_char not in _SUIT_FROM_CHAR:
            raise ValueError(f"无效花色: {value!r}")
        return cls(_RANK_FROM_CHAR[rank_char], _SUIT_FROM_CHAR[suit_char])

    @classmethod
    def parse_many(cls, values: Iterable[str]) -> List["Card"]:
        return [cls.from_str(item) for item in values]

    @property
    def code(self) -> str:
        """Treys-compatible code, e.g. ``Ah``."""
        return f"{self.rank.label}{self.suit.value}"

    @property
    def rank_label(self) -> str:
        return self.rank.label

    @property
    def suit_symbol(self) -> str:
        return self.suit.symbol

    @property
    def is_red(self) -> bool:
        return self.suit.is_red

    def __str__(self) -> str:
        return f"{self.rank.label}{self.suit.symbol}"

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "rank": self.rank.label,
            "suit": self.suit.value,
            "symbol": self.suit.symbol,
            "red": self.suit.is_red,
            "label": str(self),
        }


FULL_DECK: tuple[Card, ...] = tuple(
    Card(rank, suit) for suit in Suit for rank in Rank
)


class Deck:
    """A 52-card deck with a deterministic option and burn pile tracking."""

    def __init__(self, seed: int | None = None) -> None:
        self._rng = random.Random(seed)
        self._cards: List[Card] = []
        self.burned: List[Card] = []
        self.reset()

    def reset(self) -> None:
        self._cards = list(FULL_DECK)
        self.burned = []

    def shuffle(self) -> None:
        self._rng.shuffle(self._cards)

    def deal(self, count: int = 1) -> List[Card]:
        if count < 0:
            raise ValueError("发牌数不能为负")
        if count > len(self._cards):
            raise ValueError("牌堆剩余牌不足")
        dealt: List[Card] = []
        for _ in range(count):
            dealt.append(self._cards.pop())
        return dealt

    def deal_one(self) -> Card:
        return self.deal(1)[0]

    def burn(self) -> Card:
        card = self.deal_one()
        self.burned.append(card)
        return card

    @property
    def remaining(self) -> int:
        return len(self._cards)

    def peek_from_top(self) -> List[Card]:
        """Return remaining cards in the order they will be dealt next."""
        return list(reversed(self._cards))

    def __len__(self) -> int:
        return len(self._cards)
