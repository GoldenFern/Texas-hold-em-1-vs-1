"""Select the most robust checkpoint from a project-bot training run.

The training script already saves a ``_best`` checkpoint using 2,000-hand
evaluations.  Early 2k evaluations are noisy, so this script re-evaluates every
milestone checkpoint with duplicate deals and a larger sample, then copies the
best one to a ``_selected`` path for final evaluation.
"""

from __future__ import annotations

import argparse
import csv
import math
import shutil
import statistics
import sys
from pathlib import Path
from typing import List, Optional, Sequence

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rlcard.agents import RandomAgent

from src.agents.project_bot import ProjectBot
from src.training import NUM_ACTIONS, ProjectHeadsUpEnv


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Select a project-bot checkpoint")
    parser.add_argument("--algorithm", choices=("dqn", "nfsp"), required=True)
    parser.add_argument("--checkpoint-dir", type=Path, required=True)
    parser.add_argument("--hands", type=int, default=10000, help="Total hands per candidate; even")
    parser.add_argument("--chips", type=int, default=1000)
    parser.add_argument("--sb", type=int, default=5)
    parser.add_argument("--bb", type=int, default=10)
    parser.add_argument("--seed", type=int, default=700001)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--summary", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.hands <= 0 or args.hands % 2:
        parser.error("--hands 必须是正偶数")
    if args.chips <= 0 or args.sb <= 0 or args.bb < args.sb:
        parser.error("筹码和盲注必须满足 chips > 0, 0 < sb <= bb")
    if args.output is None:
        args.output = PROJECT_ROOT / "models" / "rlcard" / f"nlh_{args.algorithm}_project_selected.pth"
    if args.summary is None:
        args.summary = args.checkpoint_dir / "selection.csv"
    return args


def checkpoint_hand(path: Path, algorithm: str) -> int:
    stem = path.stem
    prefix = f"{algorithm}_"
    if stem.startswith(prefix):
        stem = stem[len(prefix):]
    try:
        return int(stem)
    except ValueError:
        return -1


def evaluate_checkpoint(
    path: Path,
    algorithm: str,
    env: ProjectHeadsUpEnv,
    hands: int,
    seed: int,
    bb: int,
) -> dict:
    bot = ProjectBot("candidate", path, algorithm=algorithm)
    random_agent = RandomAgent(num_actions=NUM_ACTIONS)
    deals = hands // 2
    pair_means: List[float] = []
    wins = 0
    for deal_index in range(deals):
        deck_seed = (seed + 1_000_003 * (deal_index + 1)) & 0x7FFFFFFF
        np.random.seed(deck_seed)
        env.reset(seed=deck_seed, button=0)
        env.play_hand([bot, random_agent], training=False)
        net0 = int(env.raw_payoffs()[0])

        np.random.seed(deck_seed)
        env.reset(seed=deck_seed, button=0)
        env.play_hand([random_agent, bot], training=False)
        net1 = int(env.raw_payoffs()[1])

        pair_means.append((net0 + net1) / 2.0)
        wins += int(net0 > 0) + int(net1 > 0)

    mean_net = statistics.fmean(pair_means)
    sd = statistics.pstdev(pair_means) if len(pair_means) > 1 else 0.0
    ci = 1.96 * sd / math.sqrt(len(pair_means)) if pair_means else 0.0
    total_hands = deals * 2
    return {
        "checkpoint": str(path),
        "hands": total_hands,
        "mean_net": round(mean_net, 3),
        "mean_bb100": round(mean_net / max(1, bb) * 100.0, 3),
        "ci95_net": round(ci, 3),
        "ci95_bb100": round(ci / max(1, bb) * 100.0, 3),
        "lower_bb100": round((mean_net - ci) / max(1, bb) * 100.0, 3),
        "win_pct": round(wins / total_hands * 100.0, 3),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    candidates = sorted(
        (
            path
            for path in args.checkpoint_dir.glob(f"{args.algorithm}_*.pth")
            if path.is_file() and not path.stem.endswith("_best")
        ),
        key=lambda path: checkpoint_hand(path, args.algorithm),
    )
    if not candidates:
        raise FileNotFoundError(f"{args.checkpoint_dir} 下没有 {args.algorithm}_*.pth")

    env = ProjectHeadsUpEnv(
        starting_chips=args.chips,
        small_blind=args.sb,
        big_blind=args.bb,
        seed=args.seed,
    )
    rows = []
    print(f"algorithm={args.algorithm} candidates={len(candidates)} hands={args.hands}")
    for path in candidates:
        row = evaluate_checkpoint(path, args.algorithm, env, args.hands, args.seed, args.bb)
        rows.append(row)
        print(
            f"{path.name}: {row['mean_bb100']:+.1f} bb/100 "
            f"(+/-{row['ci95_bb100']:.1f}), lower={row['lower_bb100']:+.1f}, "
            f"win {row['win_pct']:.1f}%"
        )

    best = max(rows, key=lambda row: row["lower_bb100"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(best["checkpoint"], args.output)
    print(f"selected: {best['checkpoint']} -> {args.output}")

    args.summary.parent.mkdir(parents=True, exist_ok=True)
    with args.summary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    print(f"summary: {args.summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
