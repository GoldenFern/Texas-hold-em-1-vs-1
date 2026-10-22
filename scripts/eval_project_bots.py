"""Evaluate project-observation bots (Random / DQN / NFSP) offline.

This is the project-rules counterpart to ``eval_bots.py``.  It uses
``ProjectHeadsUpEnv`` and ``ProjectBot``, so the 73-dim observation, ten action
slots, button order, min-raise and short-all-in rules all match training.

Examples
--------
Pilot::

    python scripts/eval_project_bots.py --hands 2000

Full matrix::

    python scripts/eval_project_bots.py --hands 20000 --output tmp/project_eval/main
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rlcard.agents import RandomAgent

from src.agents.project_bot import ProjectBot
from src.training import NUM_ACTIONS, ProjectHeadsUpEnv

BOT_TYPES = ("random", "dqn", "nfsp")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Project-rules bot matrix evaluation")
    parser.add_argument("--hands", type=int, default=2000, help="Hands per pairing; must be even")
    parser.add_argument(
        "--pairings",
        default="all",
        help="'all' or labels like random_vs_dqn,dqn_vs_nfsp",
    )
    parser.add_argument("--chips", type=int, default=1000)
    parser.add_argument("--sb", type=int, default=5)
    parser.add_argument("--bb", type=int, default=10)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--dqn-model", type=Path, default=None)
    parser.add_argument("--nfsp-model", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.hands <= 0 or args.hands % 2:
        parser.error("--hands 必须是正偶数")
    if args.chips <= 0 or args.sb <= 0 or args.bb < args.sb:
        parser.error("筹码和盲注必须满足 chips > 0, 0 < sb <= bb")
    if args.dqn_model is None:
        args.dqn_model = PROJECT_ROOT / "models" / "rlcard" / "nlh_dqn_project.pth"
    if args.nfsp_model is None:
        args.nfsp_model = PROJECT_ROOT / "models" / "rlcard" / "nlh_nfsp_project.pth"
    if args.output is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        args.output = PROJECT_ROOT / "tmp" / "project_eval" / stamp
    return args


def build_pairings(selection: str) -> List[Tuple[str, str]]:
    if selection.strip().lower() == "all":
        return [(a, b) for index, a in enumerate(BOT_TYPES) for b in BOT_TYPES[index:]]
    pairs: List[Tuple[str, str]] = []
    for raw in selection.split(","):
        item = raw.strip().lower()
        if not item:
            continue
        if "_vs_" in item:
            left, right = item.split("_vs_", 1)
        elif "-" in item:
            left, right = item.split("-", 1)
        else:
            raise ValueError(f"无法解析组合 {raw!r}")
        if left not in BOT_TYPES or right not in BOT_TYPES:
            raise ValueError(f"未知 Bot 类型: {raw!r}")
        pairs.append((left, right))
    return list(dict.fromkeys(pairs))


def _resolved_model_path(path: Path) -> Path:
    path = Path(path)
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def make_bot(kind: str, args: argparse.Namespace):
    if kind == "random":
        return RandomAgent(num_actions=NUM_ACTIONS)
    if kind == "dqn":
        return ProjectBot("dqn", args.dqn_model, algorithm="dqn")
    if kind == "nfsp":
        return ProjectBot("nfsp", args.nfsp_model, algorithm="nfsp")
    raise ValueError(kind)


def run_pairing(
    pairing: Tuple[str, str],
    args: argparse.Namespace,
) -> Tuple[List[dict], dict]:
    left, right = pairing
    label = f"{left}_vs_{right}"
    missing = [
        str(path)
        for kind, path in (("dqn", args.dqn_model), ("nfsp", args.nfsp_model))
        if kind in pairing and not _resolved_model_path(path).is_file()
    ]
    if missing:
        raise FileNotFoundError(f"{label} 缺少模型文件: {missing}")

    env = ProjectHeadsUpEnv(
        starting_chips=args.chips,
        small_blind=args.sb,
        big_blind=args.bb,
        seed=args.seed,
    )
    bot_a = make_bot(left, args)
    bot_b = make_bot(right, args)
    deals = args.hands // 2
    rows: List[dict] = []
    pair_means: List[float] = []
    per_hand_nets: List[int] = []
    deal_wins: List[float] = []
    wins = 0
    pushes = 0

    for deal_index in range(deals):
        deck_seed = (args.seed + 1_000_003 * (deal_index + 1)) & 0x7FFFFFFF
        for assignment in (0, 1):
            if assignment == 0:
                agents = [bot_a, bot_b]
                bot_a_seat = 0
            else:
                agents = [bot_b, bot_a]
                bot_a_seat = 1

            np.random.seed(deck_seed)
            env.reset(seed=deck_seed, button=0)
            env.play_hand(agents, training=False)
            net0, net1 = env.raw_payoffs()
            net_a = net0 if bot_a_seat == 0 else net1
            net_b = net1 if bot_a_seat == 0 else net0
            if net_a + net_b != 0:
                raise RuntimeError(f"筹码不守恒: {net_a} + {net_b}")

            result = env.game.result or {}
            actions = env.game.actions
            action_types = Counter(item["type"] for item in actions)
            rows.append(
                {
                    "pairing": label,
                    "deal_index": deal_index,
                    "seat_assignment": assignment,
                    "seed": deck_seed,
                    "button": env.last_button,
                    "bot_a": left,
                    "bot_b": right,
                    "seat0_bot": left if bot_a_seat == 0 else right,
                    "seat1_bot": right if bot_a_seat == 0 else left,
                    "hole_a": " ".join(
                        card.code for card in env.game.players[bot_a_seat].hole_cards
                    ),
                    "hole_b": " ".join(
                        card.code for card in env.game.players[1 - bot_a_seat].hole_cards
                    ),
                    "board": " ".join(result.get("board") or []),
                    "reason": result.get("reason", ""),
                    "winner_seats": " ".join(
                        str(seat) for seat in result.get("winner_seats") or []
                    ),
                    "pot": int(result.get("pot", 0)),
                    "net_a": int(net_a),
                    "net_b": int(net_b),
                    "net_seat0": int(net0),
                    "net_seat1": int(net1),
                    "actions_count": len(actions),
                    "all_in": int(
                        any(
                            item["type"] == "all_in" or int(item.get("chips_after", 1)) == 0
                            for item in actions
                        )
                    ),
                    "fold": int(action_types.get("fold", 0)),
                    "call": int(action_types.get("call", 0)),
                    "raise": int(action_types.get("raise", 0)),
                    "bet": int(action_types.get("bet", 0)),
                }
            )
            per_hand_nets.append(int(net_a))
            wins += int(net_a > 0)
            pushes += int(net_a == 0)

        pair_means.append((per_hand_nets[-2] + per_hand_nets[-1]) / 2.0)
        deal_wins.append(1.0 if per_hand_nets[-2] + per_hand_nets[-1] > 0 else 0.0)

    mean_net = statistics.fmean(pair_means)
    sd = statistics.pstdev(pair_means) if len(pair_means) > 1 else 0.0
    ci = 1.96 * sd / math.sqrt(len(pair_means)) if pair_means else 0.0
    total_hands = len(per_hand_nets)
    win_rate = wins / total_hands if total_hands else 0.0
    deal_win_rate = statistics.fmean(deal_wins) if deal_wins else 0.0
    deal_sd = statistics.pstdev(deal_wins) if len(deal_wins) > 1 else 0.0
    win_ci = 1.96 * deal_sd / math.sqrt(len(deal_wins)) * 100.0 if deal_wins else 0.0
    total_pot = sum(row["pot"] for row in rows)
    total_actions = sum(row["actions_count"] for row in rows)
    summary = {
        "pairing": label,
        "bot_a": left,
        "bot_b": right,
        "hands": total_hands,
        "deals": len(pair_means),
        "a_mean_net": round(mean_net, 3),
        "a_mean_bb100": round(mean_net / max(1, args.bb) * 100.0, 3),
        "a_ci95_net": round(ci, 3),
        "a_ci95_bb100": round(ci / max(1, args.bb) * 100.0, 3),
        "a_win_pct": round(win_rate * 100.0, 3),
        "a_deal_win_pct": round(deal_win_rate * 100.0, 3),
        "a_win_ci_pp": round(win_ci, 3),
        "a_push_pct": round(pushes / total_hands * 100.0, 3) if total_hands else 0.0,
        "seat0_win_pct": round(
            sum(1 for row in rows if int(row["net_seat0"]) > 0) / total_hands * 100.0, 3
        )
        if total_hands
        else 0.0,
        "showdown_pct": round(
            sum(1 for row in rows if row["reason"] == "showdown") / total_hands * 100.0, 3
        )
        if total_hands
        else 0.0,
        "allin_hand_pct": round(
            sum(int(row["all_in"]) for row in rows) / total_hands * 100.0, 3
        )
        if total_hands
        else 0.0,
        "fold_end_pct": round(
            sum(1 for row in rows if row["reason"] == "fold") / total_hands * 100.0, 3
        )
        if total_hands
        else 0.0,
        "avg_pot": round(total_pot / total_hands, 3) if total_hands else 0.0,
        "avg_actions": round(total_actions / total_hands, 3) if total_hands else 0.0,
        "zero_sum_ok": int(all(int(row["net_a"]) + int(row["net_b"]) == 0 for row in rows)),
    }
    return rows, summary


def write_csv(path: Path, rows: Sequence[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    pairings = build_pairings(args.pairings)
    args.output.mkdir(parents=True, exist_ok=True)
    print(f"output: {args.output}")
    print(f"hands/pairing: {args.hands}, pairings: {len(pairings)}")

    all_rows: List[dict] = []
    summaries: List[dict] = []
    metadata: List[dict] = []
    skipped: List[dict] = []
    for pairing in pairings:
        label = f"{pairing[0]}_vs_{pairing[1]}"
        try:
            rows, summary = run_pairing(pairing, args)
        except FileNotFoundError as exc:
            print(f"skip {label}: {exc}")
            skipped.append({"pairing": label, "reason": str(exc)})
            continue
        all_rows.extend(rows)
        summaries.append(summary)
        metadata.append({"pairing": label, "hands": summary["hands"]})
        print(
            f"{label}: {summary['a_mean_bb100']:+.1f} bb/100 "
            f"(+/-{summary['a_ci95_bb100']:.1f}), "
            f"win {summary['a_win_pct']:.1f}%"
        )

    write_csv(args.output / "hands.csv", all_rows)
    write_csv(args.output / "summary.csv", summaries)
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "hands_per_pairing": args.hands,
        "chips": args.chips,
        "small_blind": args.sb,
        "big_blind": args.bb,
        "seed": args.seed,
        "pairings": [item["pairing"] for item in metadata],
        "skipped_pairings": skipped,
        "dqn_model": str(args.dqn_model),
        "nfsp_model": str(args.nfsp_model),
        "note": "同牌换座；a_mean 以 pairing 标签里的第一个 Bot 为 A。",
    }
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"summary: {args.output / 'summary.csv'}")
    print(f"hands:   {args.output / 'hands.csv'}")
    if not summaries:
        print("error: 没有任何组合成功运行", file=sys.stderr)
        return 2
    if skipped:
        print("error: 部分组合因缺少模型文件被跳过", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
