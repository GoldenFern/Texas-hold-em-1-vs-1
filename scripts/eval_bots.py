"""Offline bot-vs-bot evaluation for Heads-Up Lab.

The script drives the engine directly and never starts Flask, Socket.IO, the
frontend, or any LLM provider. It plays a round-robin matrix of RLCard policy
types, records one row per hand, and writes a per-pairing statistical summary.

Examples
--------
Pilot with untrained networks when checkpoints are missing::

    python scripts/eval_bots.py --hands 2000 --allow-untrained

Full run with trained checkpoints::

    python scripts/eval_bots.py --hands 100000 --workers 4

The default model locations are ``models/rlcard/nlh_dqn.pth``,
``models/rlcard/nlh_nfsp.pth`` and ``models/rlcard/nlh_dmc.pth``.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import statistics
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agents.base import AgentView
from src.agents.rlcard_bot import RLCardBot
from src.engine.actions import Action, ActionType
from src.engine.heads_up import HeadsUpGame, Street


BOT_TYPES = ("random", "dqn", "nfsp", "dmc")
DEFAULT_MODEL_FILES = {
    "dqn": "nlh_dqn.pth",
    "nfsp": "nlh_nfsp.pth",
    "dmc": "nlh_dmc.pth",
}
MAX_ACTIONS_PER_HAND = 500


@dataclass(frozen=True)
class Pairing:
    """One unordered bot matchup."""

    index: int
    a: str
    b: str

    @property
    def label(self) -> str:
        return f"{self.a}_vs_{self.b}"


class InMemoryRLCardBot(RLCardBot):
    """RLCardBot with an injected policy, used for untrained pilot runs."""

    def __init__(self, name: str, agent_type: str, agent: Any, seed: int) -> None:
        self.name = name
        self.agent_type = agent_type
        self.model_path = None
        self.seed = seed
        self._agent = agent
        self._warning = "untrained network (pilot only)"
        self._available = True


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run offline RLCard bot-vs-bot matches.")
    parser.add_argument("--hands", type=int, default=2000, help="Hands per pairing (must be even with --duplicate).")
    parser.add_argument("--pairings", default="all", help="'all' or comma-separated labels such as random_vs_dqn,dqn_vs_dqn.")
    parser.add_argument("--chips", type=int, default=1000, help="Starting chips for both seats, reset every hand.")
    parser.add_argument("--sb", type=int, default=5, help="Small blind.")
    parser.add_argument("--bb", type=int, default=10, help="Big blind.")
    parser.add_argument("--seed", type=int, default=2026, help="Master seed for deck and bot randomness.")
    parser.add_argument("--output", type=Path, default=None, help="Output directory. Defaults to tmp/bot_eval/<timestamp>.")
    parser.add_argument("--model-dir", type=Path, default=PROJECT_ROOT / "models" / "rlcard")
    parser.add_argument("--dqn-model", type=Path, default=None)
    parser.add_argument("--nfsp-model", type=Path, default=None)
    parser.add_argument("--dmc-model", type=Path, default=None)
    parser.add_argument("--allow-untrained", action="store_true", help="Use random-initialized networks when a checkpoint is missing.")
    parser.add_argument("--no-duplicate", dest="duplicate", action="store_false", help="Do not replay each deal with seats swapped.")
    parser.set_defaults(duplicate=True)
    parser.add_argument("--workers", type=int, default=1, help="Parallel pairing workers. 1 runs in-process.")
    parser.add_argument("--quiet", action="store_true", help="Suppress per-pairing progress lines.")
    args = parser.parse_args(argv)
    if args.hands <= 0:
        parser.error("--hands must be positive")
    if args.duplicate and args.hands % 2:
        parser.error("--hands must be even when duplicate deals are enabled")
    if args.bb < args.sb or args.sb <= 0 or args.bb <= 0:
        parser.error("blinds must satisfy 0 < sb <= bb")
    if args.chips <= 0:
        parser.error("--chips must be positive")
    if args.workers <= 0:
        parser.error("--workers must be positive")
    return args


def build_pairings(selection: str) -> List[Pairing]:
    """Return the selected matchups in a stable order."""
    if selection.strip().lower() == "all":
        pairs = [(a, b) for i, a in enumerate(BOT_TYPES) for b in BOT_TYPES[i:]]
    else:
        pairs = []
        for raw in selection.split(","):
            item = raw.strip().lower()
            if not item:
                continue
            if "_vs_" in item:
                left, right = item.split("_vs_", 1)
            elif "-" in item:
                left, right = item.split("-", 1)
            else:
                raise ValueError(f"无法解析组合 {raw!r}，请使用 random_vs_dqn 这种格式")
            if left not in BOT_TYPES or right not in BOT_TYPES:
                raise ValueError(f"未知 Bot 类型: {raw!r}")
            pairs.append((left, right))
        pairs = list(dict.fromkeys(pairs))
    return [Pairing(index=index, a=a, b=b) for index, (a, b) in enumerate(pairs)]


def resolve_model_path(agent_type: str, args: argparse.Namespace) -> Optional[Path]:
    if agent_type == "random":
        return None
    explicit = getattr(args, f"{agent_type}_model", None)
    path = Path(explicit) if explicit else Path(args.model_dir) / DEFAULT_MODEL_FILES[agent_type]
    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()
    return path


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def make_untrained_agent(agent_type: str, seed: int) -> Any:
    import torch

    torch.manual_seed(seed)
    if agent_type == "dqn":
        from rlcard.agents import DQNAgent

        return DQNAgent(num_actions=5, state_shape=[54], mlp_layers=[128, 128], device="cpu")
    if agent_type == "nfsp":
        from rlcard.agents import NFSPAgent

        return NFSPAgent(
            num_actions=5,
            state_shape=[54],
            hidden_layers_sizes=[128, 128],
            q_mlp_layers=[128, 128],
            device="cpu",
            evaluate_with="average_policy",
        )
    if agent_type == "dmc":
        from rlcard.agents.dmc_agent.model import DMCAgent

        return DMCAgent(state_shape=[54], action_shape=[5], device="cpu")
    raise ValueError(f"不支持的 Bot 类型: {agent_type}")


def create_bot(
    agent_type: str,
    name: str,
    seed: int,
    args: argparse.Namespace,
) -> Tuple[RLCardBot, bool, Optional[Path]]:
    """Create a bot and return (bot, is_untrained, model_path)."""
    if agent_type == "random":
        bot = RLCardBot(name, "random", seed=seed)
        info = bot.describe()
        if not info.get("available"):
            raise RuntimeError(f"加载 random 失败: {info.get('warning')}")
        return bot, False, None

    path = resolve_model_path(agent_type, args)
    assert path is not None
    if path.is_file():
        bot = RLCardBot(
            name,
            agent_type,
            model_path=str(path),
            seed=seed,
            allow_external_model=True,
        )
        info = bot.describe()
        if not info.get("available"):
            raise RuntimeError(f"加载 {agent_type} 失败: {info.get('warning')}")
        return bot, False, path

    if not args.allow_untrained:
        raise FileNotFoundError(
            f"{agent_type} 缺少模型文件: {path}\n"
            f"请放到 models/rlcard/ 或使用 --{agent_type}-model 指定；"
            "如果只想验证管线，可以加 --allow-untrained。"
        )

    agent = make_untrained_agent(agent_type, seed)
    return InMemoryRLCardBot(name, agent_type, agent, seed), True, None


def build_view(game: HeadsUpGame, seat: int) -> AgentView:
    """Fair-information view, matching GameSession._build_view."""
    player = game.players[seat]
    opponent = game.players[1 - seat]
    constraints = game.action_constraints(seat)
    return AgentView(
        seat=seat,
        name=player.name,
        hand_id=game.hand_id,
        street=game.street.value,
        hole_cards=[card.code for card in player.hole_cards],
        community=[card.code for card in game.community],
        pot=game.pot,
        my_chips=player.chips,
        opponent_chips=opponent.chips,
        my_street_bet=player.street_bet,
        opponent_street_bet=opponent.street_bet,
        my_total_bet=player.total_bet,
        opponent_total_bet=opponent.total_bet,
        current_bet=game.current_bet,
        to_call=max(0, constraints.get("to_call", 0)),
        call_amount=int(constraints.get("call_amount", 0)),
        min_raise_to=int(constraints.get("min_raise_to", game.big_blind)),
        max_raise_to=int(constraints.get("max_raise_to", player.chips)),
        legal_actions=[action.value for action in game.legal_actions(seat)],
        action_history=list(game.actions[-12:]),
        opponent_last_action=opponent.last_action,
        button=game.button_index,
        small_blind=game.small_blind,
        big_blind=game.big_blind,
        hand_number=game.hand_id,
    )


def safe_action(game: HeadsUpGame, seat: int) -> Action:
    legal = {action.value for action in game.legal_actions(seat)}
    if "check" in legal:
        return Action(seat, ActionType.CHECK, source="system", auto=True)
    if "call" in legal:
        return Action(seat, ActionType.CALL, source="system", auto=True)
    return Action(seat, ActionType.FOLD, source="system", auto=True)


def play_hand(
    game: HeadsUpGame,
    bots: Sequence[RLCardBot],
) -> Tuple[dict, int, int]:
    """Play one hand. Returns (engine result, action count, fallback count)."""
    game.start_hand()
    actions = 0
    fallbacks = 0
    while game.street != Street.FINISHED:
        seat = game.actor
        if seat is None:
            raise RuntimeError("actor is None before the hand finished")
        actions += 1
        if actions > MAX_ACTIONS_PER_HAND:
            raise RuntimeError(f"单手动作数超过 {MAX_ACTIONS_PER_HAND}，可能存在死循环")
        view = build_view(game, seat)
        action = bots[seat].decide(view, lambda event: None)
        result = game.apply(action)
        if not result.ok:
            fallbacks += 1
            result = game.apply(safe_action(game, seat))
            if not result.ok:
                raise RuntimeError(f"兜底动作也被拒绝: {result.error}")
    if game.result is None:
        raise RuntimeError("手牌结束但没有 result")
    return game.result, actions, fallbacks


def derive_seed(master: int, pairing_index: int, index: int) -> int:
    return (int(master) + 1_000_003 * (pairing_index + 1) + 97 * index) & 0x7FFFFFFF


def compact_actions(game: HeadsUpGame) -> str:
    payload = [
        [item["street"], item["seat"], item["type"], item["amount"], item["pot_after"]]
        for item in game.actions
    ]
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def make_row(
    pairing: Pairing,
    deal_index: int,
    hand_index: int,
    assignment: int,
    seed: int,
    game: HeadsUpGame,
    bot_a_seat: int,
    untrained_a: bool,
    untrained_b: bool,
    action_count: int,
    fallback_count: int,
    duration_ms: float,
) -> dict:
    result = game.result
    assert result is not None
    players = {item["seat"]: item for item in result["players"]}
    net0 = int(players[0]["net"])
    net1 = int(players[1]["net"])
    net_a = net0 if bot_a_seat == 0 else net1
    net_b = net1 if bot_a_seat == 0 else net0
    if net_a + net_b != 0:
        raise RuntimeError(f"筹码不守恒: net_a={net_a}, net_b={net_b}")

    bot_b_seat = 1 - bot_a_seat
    hole_a = " ".join(card.code for card in game.players[bot_a_seat].hole_cards)
    hole_b = " ".join(card.code for card in game.players[bot_b_seat].hole_cards)
    seat0_bot = pairing.a if bot_a_seat == 0 else pairing.b
    seat1_bot = pairing.b if bot_a_seat == 0 else pairing.a
    actions = game.actions
    return {
        "pairing": pairing.label,
        "pairing_index": pairing.index,
        "deal_index": deal_index,
        "hand_index": hand_index,
        "seat_assignment": assignment,
        "seed": seed,
        "button": game.button_index,
        "bot_a": pairing.a,
        "bot_b": pairing.b,
        "seat0_bot": seat0_bot,
        "seat1_bot": seat1_bot,
        "bot_a_seat": bot_a_seat,
        "untrained_a": int(untrained_a),
        "untrained_b": int(untrained_b),
        "hole_a": hole_a,
        "hole_b": hole_b,
        "board": " ".join(result.get("board") or []),
        "reason": result.get("reason", ""),
        "winner_seats": " ".join(str(seat) for seat in result.get("winner_seats") or []),
        "pot": int(result.get("pot", 0)),
        "net_a": net_a,
        "net_b": net_b,
        "net_seat0": net0,
        "net_seat1": net1,
        "chips_a": int(game.players[bot_a_seat].chips),
        "chips_b": int(game.players[bot_b_seat].chips),
        "actions_count": action_count,
        "fallback_count": fallback_count,
        "showdown": int(result.get("reason") == "showdown"),
        "all_in": int(
            any(
                item["type"] == "all_in" or int(item.get("chips_after", 1)) == 0
                for item in actions
            )
        ),
        "duration_ms": round(duration_ms, 3),
        "actions": compact_actions(game),
    }


def run_pairing(pairing: Pairing, args: argparse.Namespace) -> Tuple[List[dict], dict, dict]:
    """Play every hand for one pairing and return rows, summary, metadata."""
    seed_a = args.seed + pairing.index * 97 + 11
    # Self-play should use the same untrained weights on both seats. Trained
    # checkpoints are identical anyway because both seats load the same file.
    seed_b = seed_a if pairing.a == pairing.b else args.seed + pairing.index * 97 + 53
    bot_a, untrained_a, model_a = create_bot(pairing.a, f"{pairing.a}-A", seed_a, args)
    bot_b, untrained_b, model_b = create_bot(pairing.b, f"{pairing.b}-B", seed_b, args)
    bots_by_key = {"a": bot_a, "b": bot_b}
    started = time.perf_counter()

    rows: List[dict] = []
    if args.duplicate:
        deals = args.hands // 2
        for deal_index in range(deals):
            seed = derive_seed(args.seed, pairing.index, deal_index)
            for assignment in (0, 1):
                if assignment == 0:
                    seat0, seat1 = bots_by_key["a"], bots_by_key["b"]
                    bot_a_seat = 0
                else:
                    seat0, seat1 = bots_by_key["b"], bots_by_key["a"]
                    bot_a_seat = 1
                game = HeadsUpGame(
                    starting_chips=args.chips,
                    small_blind=args.sb,
                    big_blind=args.bb,
                    seed=seed,
                )
                game.button_index = -1  # start_hand increments to seat 0
                game.configure_players(
                    {"name": seat0.name, "kind": "bot"},
                    {"name": seat1.name, "kind": "bot"},
                )
                np_random_seed(seed)
                hand_started = time.perf_counter()
                result, action_count, fallback_count = play_hand(game, [seat0, seat1])
                duration_ms = (time.perf_counter() - hand_started) * 1000.0
                rows.append(
                    make_row(
                        pairing,
                        deal_index=deal_index,
                        hand_index=len(rows),
                        assignment=assignment,
                        seed=seed,
                        game=game,
                        bot_a_seat=bot_a_seat,
                        untrained_a=untrained_a,
                        untrained_b=untrained_b,
                        action_count=action_count,
                        fallback_count=fallback_count,
                        duration_ms=duration_ms,
                    )
                )
    else:
        for hand_index in range(args.hands):
            seed = derive_seed(args.seed, pairing.index, hand_index)
            if hand_index % 2 == 0:
                seat0, seat1 = bots_by_key["a"], bots_by_key["b"]
                bot_a_seat = 0
            else:
                seat0, seat1 = bots_by_key["b"], bots_by_key["a"]
                bot_a_seat = 1
            game = HeadsUpGame(
                starting_chips=args.chips,
                small_blind=args.sb,
                big_blind=args.bb,
                seed=seed,
            )
            game.button_index = -1
            game.configure_players(
                {"name": seat0.name, "kind": "bot"},
                {"name": seat1.name, "kind": "bot"},
            )
            np_random_seed(seed)
            hand_started = time.perf_counter()
            result, action_count, fallback_count = play_hand(game, [seat0, seat1])
            duration_ms = (time.perf_counter() - hand_started) * 1000.0
            rows.append(
                make_row(
                    pairing,
                    deal_index=hand_index,
                    hand_index=hand_index,
                    assignment=hand_index % 2,
                    seed=seed,
                    game=game,
                    bot_a_seat=bot_a_seat,
                    untrained_a=untrained_a,
                    untrained_b=untrained_b,
                    action_count=action_count,
                    fallback_count=fallback_count,
                    duration_ms=duration_ms,
                )
            )

    elapsed = time.perf_counter() - started
    summary = summarize_pairing(pairing, rows, elapsed, untrained_a, untrained_b, args.bb)
    metadata = {
        "pairing": pairing.label,
        "bot_a": pairing.a,
        "bot_b": pairing.b,
        "untrained_a": untrained_a,
        "untrained_b": untrained_b,
        "model_a": str(model_a) if model_a else None,
        "model_b": str(model_b) if model_b else None,
        "hands": len(rows),
        "elapsed_sec": round(elapsed, 3),
    }
    return rows, summary, metadata


def np_random_seed(seed: int) -> None:
    """Seed NumPy before a hand so RandomAgent / NFSP sampling is reproducible."""
    import numpy as np

    np.random.seed(seed % (2**32 - 1))


def summarize_pairing(
    pairing: Pairing,
    rows: Sequence[dict],
    elapsed: float,
    untrained_a: bool,
    untrained_b: bool,
    big_blind: int,
) -> dict:
    n = len(rows)
    nets = [int(row["net_a"]) for row in rows]
    mean_net = statistics.fmean(nets) if n else 0.0
    sd_net = statistics.pstdev(nets) if n > 1 else 0.0
    ci_net = 1.96 * sd_net / math.sqrt(n) if n else 0.0
    wins = sum(1 for value in nets if value > 0)
    pushes = sum(1 for value in nets if value == 0)
    win_rate = wins / n if n else 0.0
    win_ci_pp = 1.96 * math.sqrt(win_rate * (1.0 - win_rate) / n) * 100.0 if n else 0.0

    by_deal: Dict[int, List[int]] = defaultdict(list)
    for row in rows:
        by_deal[int(row["deal_index"])].append(int(row["net_a"]))
    pair_means = [statistics.fmean(values) for values in by_deal.values()]
    pair_mean = statistics.fmean(pair_means) if pair_means else 0.0
    pair_sd = statistics.pstdev(pair_means) if len(pair_means) > 1 else 0.0
    pair_ci = 1.96 * pair_sd / math.sqrt(len(pair_means)) if pair_means else 0.0
    deal_wins = [1.0 if mean > 0 else 0.0 for mean in pair_means]
    deal_win_rate = statistics.fmean(deal_wins) if deal_wins else 0.0
    deal_win_sd = statistics.pstdev(deal_wins) if len(deal_wins) > 1 else 0.0
    deal_win_ci = 1.96 * deal_win_sd / math.sqrt(len(deal_wins)) * 100.0 if deal_wins else 0.0

    action_counts = {key: Counter() for key in ("a", "b")}
    for row in rows:
        bot_a_seat = int(row["bot_a_seat"])
        for street, seat, action_type, amount, pot_after in json.loads(row["actions"]):
            key = "a" if seat == bot_a_seat else "b"
            action_counts[key][action_type] += 1

    def pct(counter: Counter, name: str, total_actions: int) -> float:
        return 100.0 * counter.get(name, 0) / total_actions if total_actions else 0.0

    a_total = sum(action_counts["a"].values())
    b_total = sum(action_counts["b"].values())
    total_pot = sum(int(row["pot"]) for row in rows)
    total_actions = sum(int(row["actions_count"]) for row in rows)
    seat0_wins = sum(1 for row in rows if int(row["net_seat0"]) > 0)
    return {
        "pairing": pairing.label,
        "bot_a": pairing.a,
        "bot_b": pairing.b,
        "untrained_a": int(untrained_a),
        "untrained_b": int(untrained_b),
        "hands": n,
        "deals": len(by_deal),
        "a_win_pct": round(100.0 * win_rate, 3),
        "a_deal_win_pct": round(100.0 * deal_win_rate, 3),
        "a_win_ci_pp": round(deal_win_ci, 3),
        "a_win_ci_binomial_pp": round(win_ci_pp, 3),
        "a_push_pct": round(100.0 * pushes / n, 3) if n else 0.0,
        "a_mean_net": round(mean_net, 3),
        "a_mean_bb100": round(mean_net / big_blind * 100.0, 3),
        "a_sd_net": round(sd_net, 3),
        "a_ci95_net": round(ci_net, 3),
        "a_ci95_bb100": round(ci_net / big_blind * 100.0, 3),
        "paired_mean_net": round(pair_mean, 3),
        "paired_ci95_net": round(pair_ci, 3),
        "paired_ci95_bb100": round(pair_ci / big_blind * 100.0, 3),
        "seat0_win_pct": round(100.0 * seat0_wins / n, 3) if n else 0.0,
        "showdown_pct": round(100.0 * sum(int(row["showdown"]) for row in rows) / n, 3) if n else 0.0,
        "allin_hand_pct": round(100.0 * sum(int(row["all_in"]) for row in rows) / n, 3) if n else 0.0,
        "fold_end_pct": round(100.0 * sum(1 for row in rows if row["reason"] == "fold") / n, 3) if n else 0.0,
        "avg_pot": round(total_pot / n, 3) if n else 0.0,
        "avg_actions": round(total_actions / n, 3) if n else 0.0,
        "ms_per_hand": round(1000.0 * elapsed / n, 3) if n else 0.0,
        "hands_per_sec": round(n / elapsed, 1) if elapsed else 0.0,
        "a_fold_pct": round(pct(action_counts["a"], "fold", a_total), 3),
        "a_check_pct": round(pct(action_counts["a"], "check", a_total), 3),
        "a_call_pct": round(pct(action_counts["a"], "call", a_total), 3),
        "a_bet_pct": round(pct(action_counts["a"], "bet", a_total), 3),
        "a_raise_pct": round(pct(action_counts["a"], "raise", a_total), 3),
        "a_allin_action_pct": round(pct(action_counts["a"], "all_in", a_total), 3),
        "b_fold_pct": round(pct(action_counts["b"], "fold", b_total), 3),
        "b_check_pct": round(pct(action_counts["b"], "check", b_total), 3),
        "b_call_pct": round(pct(action_counts["b"], "call", b_total), 3),
        "b_bet_pct": round(pct(action_counts["b"], "bet", b_total), 3),
        "b_raise_pct": round(pct(action_counts["b"], "raise", b_total), 3),
        "b_allin_action_pct": round(pct(action_counts["b"], "all_in", b_total), 3),
        "fallbacks": sum(int(row["fallback_count"]) for row in rows),
        "zero_sum_ok": int(all(int(row["net_a"]) + int(row["net_b"]) == 0 for row in rows)),
    }


def write_csv(path: Path, rows: Sequence[dict], fieldnames: Optional[Sequence[str]] = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    names = list(fieldnames or rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=names)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    pairings = build_pairings(args.pairings)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    output = args.output or (PROJECT_ROOT / "tmp" / "bot_eval" / timestamp)
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)

    print(f"output: {output}")
    print(f"pairings: {len(pairings)}, hands/pairing: {args.hands}, duplicate: {args.duplicate}")
    print(f"table: {args.chips} chips, blinds {args.sb}/{args.bb}, seed {args.seed}")

    all_rows: List[dict] = []
    summaries: List[dict] = []
    metadata: List[dict] = []
    run_started = time.perf_counter()
    if args.workers > 1:
        from concurrent.futures import ProcessPoolExecutor, as_completed

        with ProcessPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(run_pairing, pairing, args): pairing
                for pairing in pairings
            }
            for future in as_completed(futures):
                pairing = futures[future]
                rows, summary, meta = future.result()
                all_rows.extend(rows)
                summaries.append(summary)
                metadata.append(meta)
                if not args.quiet:
                    print(
                        f"[{len(summaries)}/{len(pairings)}] {pairing.label}: "
                        f"{summary['hands']} hands, "
                        f"{summary['a_mean_bb100']:+.1f} bb/100, "
                        f"win {summary['a_win_pct']:.1f}%, "
                        f"{summary['ms_per_hand']:.3f} ms/hand"
                        + ("  [untrained]" if summary["untrained_a"] or summary["untrained_b"] else ""),
                        flush=True,
                    )
    else:
        for pairing in pairings:
            if not args.quiet:
                print(f"[{pairing.index + 1}/{len(pairings)}] {pairing.label} ...", flush=True)
            rows, summary, meta = run_pairing(pairing, args)
            all_rows.extend(rows)
            summaries.append(summary)
            metadata.append(meta)
            if not args.quiet:
                print(
                    f"    {summary['hands']} hands, "
                    f"{summary['a_mean_bb100']:+.1f} bb/100, "
                    f"win {summary['a_win_pct']:.1f}%, "
                    f"{summary['ms_per_hand']:.3f} ms/hand"
                    + ("  [untrained]" if summary["untrained_a"] or summary["untrained_b"] else ""),
                    flush=True,
                )

    elapsed = time.perf_counter() - run_started
    write_csv(output / "hands.csv", all_rows)
    write_csv(output / "summary.csv", summaries)
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "duration_sec": round(elapsed, 3),
        "total_hands": len(all_rows),
        "hands_per_pairing": args.hands,
        "duplicate_deals": bool(args.duplicate),
        "chips": args.chips,
        "small_blind": args.sb,
        "big_blind": args.bb,
        "seed": args.seed,
        "pairings": [pairing.label for pairing in pairings],
        "untrained_types": sorted(
            {row["bot_a"] for row in summaries if row["untrained_a"]}
            | {row["bot_b"] for row in summaries if row["untrained_b"]}
        ),
        "models": {
            kind: (
                {
                    "path": str(path),
                    "sha256": file_sha256(path),
                    "size_kb": round(path.stat().st_size / 1024.0, 1),
                }
                if (path := resolve_model_path(kind, args)) and path.is_file()
                else None
            )
            for kind in ("dqn", "nfsp", "dmc")
        },
        "pairing_metadata": metadata,
        "python": sys.version.split()[0],
        "rlcard": _module_version("rlcard"),
        "torch": _module_version("torch"),
        "note": (
            "untrained_types 非空时，这些类型使用随机初始化网络，结果只能用于验证管线，不能当作强弱结论。"
            if any(row["untrained_a"] or row["untrained_b"] for row in summaries)
            else "所有类型均使用真实策略（random 或已加载 checkpoint）。"
        ),
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"done: {len(all_rows)} hands in {elapsed:.2f}s")
    print(f"summary: {output / 'summary.csv'}")
    print(f"hands:   {output / 'hands.csv'}")
    if manifest["untrained_types"]:
        print(f"warning: untrained types used: {', '.join(manifest['untrained_types'])}")
    return 0


def _module_version(name: str) -> Optional[str]:
    try:
        module = __import__(name)
    except Exception:
        return None
    return getattr(module, "__version__", None)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
