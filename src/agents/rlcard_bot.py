"""RLCard-backed heads-up bot.

The observation and action mask mirror RLCard's ``no-limit-holdem``
environment. That matters when a trained DQN / NFSP checkpoint is loaded:
the network expects the same 54-float vector and the same five action ids.

RLCard's own encoding (for two players) is:

* ``obs[0:52]``: one-hot cards (public + own hole cards)
* ``obs[52]``: own total contribution this hand
* ``obs[53]``: max total contribution this hand

The action ids are ``fold=0``, ``check_call=1``, ``raise_half_pot=2``,
``raise_pot=3``, ``all_in=4``.
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path
from typing import List, Optional

import numpy as np

from src.engine.actions import Action, ActionType
from src.engine.cards import Card

from .base import AgentView, BaseAgent

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODELS_DIR = (PROJECT_ROOT / "models" / "rlcard").resolve()

RLCARD_FOLD = 0
RLCARD_CHECK_CALL = 1
RLCARD_HALF_POT = 2
RLCARD_POT = 3
RLCARD_ALL_IN = 4


class RLCardBot(BaseAgent):
    """A bot whose policy comes from RLCard."""

    kind = "bot"

    def __init__(
        self,
        name: str,
        agent_type: str = "random",
        model_path: Optional[str] = None,
        seed: int = 42,
    ) -> None:
        self.name = name
        self.agent_type = (agent_type or "random").strip().lower()
        if self.agent_type not in ("random", "dqn", "nfsp", "dmc"):
            raise ValueError(f"不支持的 RLCard agent_type: {agent_type}")
        self.model_path = model_path or None
        self.seed = seed
        self._agent = None
        self._warning = ""
        self._available = True
        self._load()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load(self) -> None:
        try:
            import rlcard  # noqa: F401
        except ModuleNotFoundError:
            self._available = False
            self._warning = "未安装 rlcard，已退化为 check/call 策略"
            return

        np.random.seed(self.seed)
        try:
            if self.agent_type == "random":
                from rlcard.agents import RandomAgent

                self._agent = RandomAgent(num_actions=5)
            else:
                if not self.model_path:
                    raise ValueError(f"agent_type={self.agent_type} 需要 model_path")
                self._agent = self._load_model(self.agent_type, self.model_path)
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI
            self._available = False
            self._warning = f"加载 RLCard 策略失败：{exc}；已退化为 check/call"
            self._agent = None

    def _load_model(self, agent_type: str, model_path: str):
        resolved = Path(model_path)
        if not resolved.is_absolute():
            resolved = (PROJECT_ROOT / resolved).resolve()
        else:
            resolved = resolved.resolve()
        if resolved != MODELS_DIR and MODELS_DIR not in resolved.parents:
            raise ValueError(f"模型文件必须位于 {MODELS_DIR} 下")
        if not resolved.is_file():
            raise FileNotFoundError(f"模型文件不存在: {model_path}")
        import torch

        checkpoint = torch.load(model_path, map_location="cpu", weights_only=False)
        if agent_type == "dqn":
            from rlcard.agents import DQNAgent

            if isinstance(checkpoint, DQNAgent):
                return checkpoint
            with contextlib.redirect_stdout(io.StringIO()):
                return DQNAgent.from_checkpoint(checkpoint=checkpoint)
        if agent_type == "nfsp":
            from rlcard.agents import NFSPAgent

            if isinstance(checkpoint, NFSPAgent):
                return checkpoint
            return NFSPAgent.from_checkpoint(checkpoint=checkpoint)
        if agent_type == "dmc":
            from rlcard.agents.dmc_agent.model import DMCAgent

            if isinstance(checkpoint, DMCAgent):
                return checkpoint
            agent = DMCAgent(state_shape=[54], action_shape=[5], device="cpu")
            if isinstance(checkpoint, dict) and "model_state_dict" in checkpoint:
                state_dicts = checkpoint["model_state_dict"]
                if isinstance(state_dicts, list):
                    agent.load_state_dict(state_dicts[0])
                else:
                    agent.load_state_dict(state_dicts)
            else:
                agent.load_state_dict(checkpoint)
            agent.eval()
            return agent
        raise ValueError(f"未知 agent_type: {agent_type}")

    # ------------------------------------------------------------------
    # Decision
    # ------------------------------------------------------------------

    def decide(self, view: AgentView, emit) -> Action:
        legal_types = set(view.legal_actions)
        if not legal_types:
            return Action(view.seat, ActionType.FOLD, source="bot")

        if not self._available or self._agent is None:
            return self._fallback(view, legal_types)

        legal_ids = self._legal_action_ids(view)
        if not legal_ids:
            return self._fallback(view, legal_types)
        if len(legal_ids) == 1:
            return self._map_action(view, legal_ids[0])

        obs = self._observation(view)
        state = {
            "obs": obs,
            "legal_actions": {int(action_id): None for action_id in legal_ids},
            "raw_legal_actions": list(legal_ids),
        }
        try:
            action_id, info = self._agent.eval_step(state)
            action_id = int(action_id)
        except Exception as exc:  # noqa: BLE001
            self._warning = f"RLCard 决策异常：{exc}"
            return self._fallback(view, legal_types)

        mapped = self._map_action(view, action_id)
        if mapped.type.value not in legal_types:
            mapped = self._fallback(view, legal_types)
        emit(
            {
                "type": "bot_decision",
                "seat": view.seat,
                "hand_id": view.hand_id,
                "street": view.street,
                "agent_type": self.agent_type,
                "action_id": action_id,
                "legal_ids": legal_ids,
                "probabilities": info.get("probs", {}) if isinstance(info, dict) else {},
                "mapped": {
                    "type": mapped.type.value,
                    "label": mapped.type.label,
                    "amount": mapped.amount,
                    "auto": mapped.auto,
                },
            }
        )
        return mapped

    def _fallback(self, view: AgentView, legal_types: set) -> Action:
        if ActionType.CHECK.value in legal_types:
            return Action(view.seat, ActionType.CHECK, source="bot", auto=True)
        if ActionType.CALL.value in legal_types:
            return Action(view.seat, ActionType.CALL, source="bot", auto=True)
        return Action(view.seat, ActionType.FOLD, source="bot", auto=True)

    # ------------------------------------------------------------------
    # RLCard encoding
    # ------------------------------------------------------------------

    @staticmethod
    def _card_index(code: str) -> int:
        card = Card.from_str(code)
        suit_order = {"s": 0, "h": 1, "d": 2, "c": 3}
        rank_index = (int(card.rank) - 1) % 13
        return suit_order[card.suit.value] * 13 + rank_index

    def _observation(self, view: AgentView) -> np.ndarray:
        obs = np.zeros(54, dtype=np.float32)
        for code in list(view.community) + list(view.hole_cards):
            obs[self._card_index(code)] = 1.0
        obs[52] = float(view.my_total_bet)
        obs[53] = float(max(view.my_total_bet, view.opponent_total_bet))
        return obs

    def _legal_action_ids(self, view: AgentView) -> List[int]:
        legal_types = set(view.legal_actions)
        ids: List[int] = []
        if ActionType.FOLD.value in legal_types:
            ids.append(RLCARD_FOLD)
        if ActionType.CHECK.value in legal_types or ActionType.CALL.value in legal_types:
            ids.append(RLCARD_CHECK_CALL)

        to_call = max(0, view.current_bet - view.my_street_bet)
        can_raise = ActionType.BET.value in legal_types or ActionType.RAISE.value in legal_types
        if can_raise:
            pot = max(0, view.pot)
            half_add = pot // 2
            half_target = view.my_street_bet + half_add
            if half_add > 0 and half_target > view.current_bet and half_add <= view.my_chips:
                ids.append(RLCARD_HALF_POT)
            pot_target = view.my_street_bet + pot
            if pot > 0 and pot_target > view.current_bet and pot <= view.my_chips:
                ids.append(RLCARD_POT)
            if view.my_chips > to_call:
                ids.append(RLCARD_ALL_IN)
        return sorted(set(ids))

    def _map_action(self, view: AgentView, action_id: int) -> Action:
        legal_types = set(view.legal_actions)
        if action_id == RLCARD_FOLD:
            return Action(view.seat, ActionType.FOLD, source="bot")
        if action_id == RLCARD_CHECK_CALL:
            if ActionType.CHECK.value in legal_types:
                return Action(view.seat, ActionType.CHECK, source="bot")
            return Action(view.seat, ActionType.CALL, source="bot")

        to_call = max(0, view.current_bet - view.my_street_bet)
        pot = max(0, view.pot)
        if action_id == RLCARD_HALF_POT:
            added = pot // 2
        elif action_id == RLCARD_POT:
            added = pot
        elif action_id == RLCARD_ALL_IN:
            added = view.my_chips
        else:
            return self._fallback(view, legal_types)

        added = min(added, view.my_chips)
        if added <= 0:
            return self._fallback(view, legal_types)
        target = view.my_street_bet + added
        if target <= view.current_bet:
            return self._fallback(view, legal_types)

        if target < view.min_raise_to:
            target = min(view.min_raise_to, view.max_raise_to)
        target = min(target, view.max_raise_to)
        if target <= view.current_bet:
            return self._fallback(view, legal_types)

        action_type = ActionType.BET if view.current_bet == 0 else ActionType.RAISE
        if target >= view.max_raise_to and added >= view.my_chips:
            action_type = ActionType.ALL_IN
        return Action(view.seat, action_type, amount=target, source="bot")

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def describe(self) -> dict:
        return {
            "kind": self.kind,
            "name": self.name,
            "agent_type": self.agent_type,
            "model_path": self.model_path,
            "available": self._available,
            "warning": self._warning,
        }


__all__ = ["RLCardBot"]
