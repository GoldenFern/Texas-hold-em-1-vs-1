"""Inference adapter for models trained on the project's 73-dim observation.

The checkpoint format is deliberately simple and safe to load:

    {
        "format": "project-bot-v1",
        "algorithm": "dqn" | "nfsp",
        "obs_dim": 73,
        "num_actions": 10,
        "mlp_layers": [128, 128],
        "state_dict": {...},
        "metadata": {...},
    }

Only tensors and primitive values are stored, so inference can use
``torch.load(..., weights_only=True)``.  The legacy ``RLCardBot`` path still
handles 54-dim / 5-action RLCard checkpoints; this adapter is for project bots.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np

from src.agents.base import AgentView, BaseAgent
from src.engine.actions import Action
from src.i18n import normalize_locale, tr
from src.training.actions import (
    ACTION_LABELS,
    NUM_ACTIONS,
    action_from_view,
    legal_action_mask,
)
from src.training.obs import OBS_DIM, OBS_VERSION, encode_observation

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHECKPOINT_FORMAT = "project-bot-v1"


def save_project_checkpoint(
    path: Path,
    algorithm: str,
    state_dict: Dict[str, Any],
    obs_dim: int = OBS_DIM,
    num_actions: int = NUM_ACTIONS,
    mlp_layers: Optional[list[int]] = None,
    metadata: Optional[dict] = None,
    obs_version: int = OBS_VERSION,
) -> Path:
    """Save a safe, inference-only checkpoint."""
    import torch

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "format": CHECKPOINT_FORMAT,
        "algorithm": str(algorithm),
        "obs_dim": int(obs_dim),
        "obs_version": int(obs_version),
        "num_actions": int(num_actions),
        "mlp_layers": list(mlp_layers or [128, 128]),
        "state_dict": state_dict,
        "metadata": dict(metadata or {}),
    }
    torch.save(payload, path)
    return path


class ProjectBot(BaseAgent):
    """A DQN or NFSP policy trained on the project observation/action space."""

    kind = "project_bot"

    def __init__(
        self,
        name: str,
        model_path: str | Path,
        algorithm: Optional[str] = None,
        device: str = "cpu",
        sample: Optional[bool] = None,
        locale: str = "zh-CN",
    ) -> None:
        self.name = name
        self.locale = normalize_locale(locale, "zh-CN") or "zh-CN"
        self.model_path = str(model_path)
        self.device_name = str(device)
        self.device: Any = None
        self._sample_explicit = sample is not None
        self.sample = bool(sample) if sample is not None else False
        self.algorithm = (algorithm or "").strip().lower()
        self.obs_dim = OBS_DIM
        self.obs_version = OBS_VERSION
        self.num_actions = NUM_ACTIONS
        self.mlp_layers = [128, 128]
        self.net: Optional[Any] = None
        self._warning = ""
        self._load()

    def _load(self) -> None:
        import torch

        path = Path(self.model_path)
        if not path.is_absolute():
            path = (PROJECT_ROOT / path).resolve()
        if not path.is_file():
            raise FileNotFoundError(
                tr(self.locale, "bot.project.model_missing", path=path)
            )
        self.model_path = str(path)
        self.device = torch.device(self.device_name)
        try:
            payload = torch.load(path, map_location="cpu", weights_only=True)
        except Exception as exc:
            raise ValueError(
                tr(
                    self.locale,
                    "bot.project.unsafe_checkpoint",
                    path=path,
                    format=CHECKPOINT_FORMAT,
                    error=exc,
                )
            ) from exc
        if not isinstance(payload, dict) or payload.get("format") != CHECKPOINT_FORMAT:
            raise ValueError(
                tr(
                    self.locale,
                    "bot.project.wrong_format",
                    path=path,
                    format=CHECKPOINT_FORMAT,
                )
            )

        requested_algorithm = self.algorithm
        algorithm = str(payload.get("algorithm") or self.algorithm).lower()
        if algorithm not in ("dqn", "nfsp"):
            raise ValueError(
                tr(self.locale, "bot.project.unsupported_algorithm", algorithm=repr(algorithm))
            )
        if requested_algorithm and requested_algorithm != algorithm:
            raise ValueError(
                tr(
                    self.locale,
                    "bot.project.algorithm_mismatch",
                    actual=algorithm,
                    requested=requested_algorithm,
                )
            )
        self.algorithm = algorithm
        if not self._sample_explicit:
            self.sample = algorithm == "nfsp"
        self.obs_dim = int(payload.get("obs_dim", OBS_DIM))
        self.obs_version = int(payload.get("obs_version", 1))
        if self.obs_version < 1 or self.obs_version > OBS_VERSION:
            raise ValueError(
                tr(self.locale, "bot.project.unsupported_obs", version=self.obs_version)
            )
        self.num_actions = int(payload.get("num_actions", NUM_ACTIONS))
        self.mlp_layers = list(payload.get("mlp_layers") or [128, 128])

        if self.obs_dim != OBS_DIM:
            raise ValueError(
                tr(
                    self.locale,
                    "bot.project.obs_dim",
                    actual=self.obs_dim,
                    expected=OBS_DIM,
                )
            )
        if self.num_actions != NUM_ACTIONS:
            raise ValueError(
                tr(
                    self.locale,
                    "bot.project.num_actions",
                    actual=self.num_actions,
                    expected=NUM_ACTIONS,
                )
            )

        if algorithm == "dqn":
            from rlcard.agents.dqn_agent import EstimatorNetwork

            net = EstimatorNetwork(
                num_actions=self.num_actions,
                state_shape=[self.obs_dim],
                mlp_layers=self.mlp_layers,
            )
        else:
            from rlcard.agents.nfsp_agent import AveragePolicyNetwork

            policy_layers = list(self.mlp_layers)
            if not policy_layers or policy_layers[-1] != self.num_actions:
                policy_layers = policy_layers + [self.num_actions]
            self.mlp_layers = policy_layers
            net = AveragePolicyNetwork(
                num_actions=self.num_actions,
                state_shape=[self.obs_dim],
                mlp_layers=self.mlp_layers,
            )

        state_dict = payload.get("state_dict")
        if not isinstance(state_dict, dict):
            raise ValueError(tr(self.locale, "bot.project.missing_state_dict"))
        net.load_state_dict(state_dict)
        net.to(self.device)
        net.eval()
        self.net = net

    # ------------------------------------------------------------------
    # Decision
    # ------------------------------------------------------------------

    def decide(self, view: AgentView, emit) -> Action:
        import torch

        if self.net is None:
            raise RuntimeError(tr(self.locale, "bot.project.not_loaded"))
        obs = encode_observation(view, view.big_blind, self.obs_version)
        mask = legal_action_mask(view)
        if not mask.any():
            raise RuntimeError(tr(self.locale, "bot.project.no_legal_actions"))

        tensor = torch.from_numpy(obs).float().unsqueeze(0).to(self.device)
        with torch.no_grad():
            output = self.net(tensor)[0].detach().cpu().numpy()

        if self.algorithm == "dqn":
            values = output.astype(np.float64, copy=True)
            values[~mask] = -np.inf
            slot = int(np.argmax(values))
            probabilities: Dict[str, float] = {}
        else:
            log_probs = output.astype(np.float64, copy=True)
            log_probs[~mask] = -np.inf
            shifted = log_probs - np.max(log_probs)
            probs = np.exp(shifted)
            probs[~mask] = 0.0
            total = float(probs.sum())
            if total <= 0:
                probs = mask.astype(np.float64)
                total = float(probs.sum())
            probs /= total
            if self.sample:
                slot = int(np.random.choice(self.num_actions, p=probs))
            else:
                slot = int(np.argmax(probs))
            probabilities = {
                ACTION_LABELS[index]: float(probs[index])
                for index in range(self.num_actions)
                if mask[index]
            }

        action = action_from_view(view, slot)
        emit(
            {
                "type": "bot_decision",
                "seat": int(view.seat),
                "hand_id": int(view.hand_id),
                "street": str(view.street),
                "agent_type": f"project_{self.algorithm}",
                "algorithm": self.algorithm,
                "action_id": slot,
                "slot": slot,
                "label": ACTION_LABELS[slot],
                "legal_ids": [int(i) for i in np.flatnonzero(mask)],
                "probabilities": probabilities,
                "mapped": action.to_dict(),
            }
        )
        return action

    def describe(self) -> dict:
        return {
            "kind": self.kind,
            "name": self.name,
            "agent_type": f"project_{self.algorithm}",
            "algorithm": self.algorithm,
            "sample": self.sample,
            "model_path": self.model_path,
            "obs_dim": self.obs_dim,
            "obs_version": self.obs_version,
            "num_actions": self.num_actions,
            "mlp_layers": list(self.mlp_layers),
            "available": self.net is not None,
            "warning": self._warning,
        }


__all__ = ["CHECKPOINT_FORMAT", "ProjectBot", "save_project_checkpoint"]
