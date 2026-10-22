"""Train DQN / NFSP bots on the project's strict heads-up rules.

Examples
--------
Quick smoke test::

    python scripts/train_project_bot.py --algorithm dqn --hands 2000 \
        --eval-every 1000 --eval-hands 200 --output tmp/training/dqn_smoke.pth

Main run::

    python scripts/train_project_bot.py --algorithm dqn --hands 100000 \
        --output models/rlcard/nlh_dqn_project.pth

The environment and evaluation both use ``HeadsUpGame``, so the trained model
sees the same button order, blinds, min-raise and short-all-in rules as the
live server.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import io
import json
import math
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import torch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import rlcard
from rlcard.agents import DQNAgent, NFSPAgent, RandomAgent
from rlcard.utils import set_seed

from src.agents.project_bot import save_project_checkpoint
from src.training import NUM_ACTIONS, OBS_DIM, ProjectHeadsUpEnv


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train a project-rules DQN/NFSP bot")
    parser.add_argument("--algorithm", choices=("dqn", "nfsp"), default="dqn")
    parser.add_argument("--hands", type=int, default=100000)
    parser.add_argument("--chips", type=int, default=1000)
    parser.add_argument("--sb", type=int, default=5)
    parser.add_argument("--bb", type=int, default=10)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--log-dir", type=Path, default=None)
    parser.add_argument("--eval-every", type=int, default=10000)
    parser.add_argument("--eval-hands", type=int, default=1000)
    parser.add_argument("--eval-seed", type=int, default=900001)
    parser.add_argument("--opponent", choices=("self", "random", "mix"), default="self")
    parser.add_argument("--random-prob", type=float, default=0.2)
    parser.add_argument("--log-every", type=int, default=1000)
    parser.add_argument("--mlp", type=int, nargs="+", default=[128, 128])
    parser.add_argument("--learning-rate", type=float, default=1e-4)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--gamma", type=float, default=0.99)
    parser.add_argument("--epsilon-start", type=float, default=1.0)
    parser.add_argument("--epsilon-end", type=float, default=0.05)
    parser.add_argument("--epsilon-decay", type=int, default=100000)
    parser.add_argument("--target-update", type=int, default=2000)
    parser.add_argument("--replay-size", type=int, default=200000)
    parser.add_argument("--replay-init", type=int, default=1000)
    parser.add_argument("--reservoir-size", type=int, default=300000)
    parser.add_argument("--min-buffer", type=int, default=500)
    parser.add_argument("--nfsp-batch", type=int, default=128)
    parser.add_argument("--nfsp-q-batch", type=int, default=64)
    parser.add_argument("--nfsp-q-lr", type=float, default=1e-4)
    parser.add_argument("--nfsp-sl-lr", type=float, default=1e-3)
    parser.add_argument("--anticipatory", type=float, default=0.1)
    parser.add_argument("--max-actions", type=int, default=200)
    args = parser.parse_args(argv)

    if args.hands <= 0:
        parser.error("--hands 必须为正")
    if args.eval_hands <= 0 or args.eval_hands % 2:
        parser.error("--eval-hands 必须是正偶数，因为要同牌换座")
    if args.chips <= 0 or args.sb <= 0 or args.bb < args.sb:
        parser.error("筹码和盲注必须满足 chips > 0, 0 < sb <= bb")
    if args.eval_every <= 0:
        parser.error("--eval-every 必须为正")
    if args.log_every <= 0:
        parser.error("--log-every 必须为正")
    if args.batch_size <= 0 or args.nfsp_batch <= 0 or args.nfsp_q_batch <= 0:
        parser.error("--batch-size 和 NFSP batch 参数必须为正")
    if args.replay_init < args.batch_size:
        parser.error("--replay-init 不能小于 --batch-size")
    if args.algorithm == "nfsp" and args.replay_init < args.nfsp_q_batch:
        parser.error("--replay-init 不能小于 --nfsp-q-batch")
    if args.target_update <= 0:
        parser.error("--target-update 必须为正")
    if args.epsilon_decay <= 0:
        parser.error("--epsilon-decay 必须为正")
    if not 0.0 <= args.epsilon_end <= args.epsilon_start <= 1.0:
        parser.error("必须满足 0 <= epsilon_end <= epsilon_start <= 1")
    if args.max_actions <= 0 or args.reservoir_size <= 0 or args.replay_size <= 0:
        parser.error("--max-actions / --replay-size / --reservoir-size 必须为正")
    if args.min_buffer < 0:
        parser.error("--min-buffer 不能为负")
    if not 0.0 <= args.random_prob <= 1.0:
        parser.error("--random-prob 必须在 0 到 1 之间")
    if not 0.0 <= args.anticipatory <= 1.0:
        parser.error("--anticipatory 必须在 0 到 1 之间")
    if not 0.0 < args.gamma <= 1.0:
        parser.error("--gamma 必须在 (0, 1] 之间")
    if args.learning_rate <= 0 or args.nfsp_q_lr <= 0 or args.nfsp_sl_lr <= 0:
        parser.error("学习率必须为正")
    if not args.mlp or any(size <= 0 for size in args.mlp):
        parser.error("--mlp 每一层必须为正")
    if args.output is None:
        args.output = PROJECT_ROOT / "models" / "rlcard" / f"nlh_{args.algorithm}_project.pth"
    if args.log_dir is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        args.log_dir = PROJECT_ROOT / "tmp" / "training" / f"{args.algorithm}_{stamp}"
    return args


def resolve_device(name: str) -> str:
    if name.startswith("cuda") and not torch.cuda.is_available():
        print("warning: CUDA 不可用，回退到 CPU")
        return "cpu"
    return name


def make_dqn(args: argparse.Namespace) -> DQNAgent:
    return DQNAgent(
        num_actions=NUM_ACTIONS,
        state_shape=[OBS_DIM],
        mlp_layers=list(args.mlp),
        learning_rate=args.learning_rate,
        batch_size=args.batch_size,
        replay_memory_size=args.replay_size,
        replay_memory_init_size=args.replay_init,
        epsilon_start=args.epsilon_start,
        epsilon_end=args.epsilon_end,
        epsilon_decay_steps=args.epsilon_decay,
        update_target_estimator_every=args.target_update,
        discount_factor=args.gamma,
        train_every=1,
        device=args.device,
    )


def make_nfsp(args: argparse.Namespace) -> NFSPAgent:
    return NFSPAgent(
        num_actions=NUM_ACTIONS,
        state_shape=[OBS_DIM],
        hidden_layers_sizes=list(args.mlp),
        reservoir_buffer_capacity=args.reservoir_size,
        anticipatory_param=args.anticipatory,
        batch_size=args.nfsp_batch,
        min_buffer_size_to_learn=args.min_buffer,
        q_replay_memory_size=args.replay_size,
        q_replay_memory_init_size=args.replay_init,
        q_update_target_estimator_every=args.target_update,
        q_batch_size=args.nfsp_q_batch,
        q_train_every=1,
        q_mlp_layers=list(args.mlp),
        q_discount_factor=args.gamma,
        rl_learning_rate=args.nfsp_q_lr,
        sl_learning_rate=args.nfsp_sl_lr,
        evaluate_with="average_policy",
        device=args.device,
    )


def make_agent(algorithm: str, args: argparse.Namespace):
    return make_dqn(args) if algorithm == "dqn" else make_nfsp(args)


def epsilon_of(agent: object) -> float:
    try:
        values = getattr(agent, "epsilons")
        index = min(int(getattr(agent, "total_t", 0)), len(values) - 1)
        return float(values[index])
    except Exception:
        return 0.0


def save_agent(
    path: Path,
    algorithm: str,
    agent: object,
    metadata: dict,
) -> Path:
    if algorithm == "dqn":
        state_dict = agent.q_estimator.qnet.state_dict()
        mlp_layers = list(agent.q_estimator.qnet.mlp_layers)
    else:
        state_dict = agent.policy_network.state_dict()
        mlp_layers = list(agent.policy_network.mlp_layers)
    return save_project_checkpoint(
        path,
        algorithm=algorithm,
        state_dict=state_dict,
        obs_dim=OBS_DIM,
        num_actions=NUM_ACTIONS,
        mlp_layers=mlp_layers,
        metadata=metadata,
    )


def evaluate_vs_random(
    learner: object,
    env: ProjectHeadsUpEnv,
    hands: int,
    seed: int,
    bb: int,
) -> dict:
    """Duplicate-deal evaluation: same deck, seats swapped."""
    if hands % 2:
        raise ValueError("hands 必须是偶数")
    random_agent = RandomAgent(num_actions=NUM_ACTIONS)
    deals = hands // 2
    pair_means: List[float] = []
    per_hand_nets: List[int] = []
    deal_wins: List[float] = []
    wins = 0
    pushes = 0

    # Evaluation must not consume or reseed the training RNG stream.
    rng_state = np.random.get_state()
    try:
        for deal_index in range(deals):
            deck_seed = (seed + 1_000_003 * (deal_index + 1)) & 0x7FFFFFFF
            np.random.seed(deck_seed)
            env.reset(seed=deck_seed, button=0)
            env.play_hand([learner, random_agent], training=False)
            net0 = int(env.raw_payoffs()[0])

            np.random.seed(deck_seed)
            env.reset(seed=deck_seed, button=0)
            env.play_hand([random_agent, learner], training=False)
            net1 = int(env.raw_payoffs()[1])

            pair_means.append((net0 + net1) / 2.0)
            deal_wins.append(1.0 if net0 + net1 > 0 else 0.0)
            per_hand_nets.extend((net0, net1))
            wins += int(net0 > 0) + int(net1 > 0)
            pushes += int(net0 == 0) + int(net1 == 0)
    finally:
        np.random.set_state(rng_state)

    mean_net = statistics.fmean(pair_means)
    sd = statistics.pstdev(pair_means) if len(pair_means) > 1 else 0.0
    ci = 1.96 * sd / math.sqrt(len(pair_means)) if pair_means else 0.0
    total_hands = len(per_hand_nets)
    win_rate = wins / total_hands if total_hands else 0.0
    deal_win_rate = statistics.fmean(deal_wins) if deal_wins else 0.0
    deal_sd = statistics.pstdev(deal_wins) if len(deal_wins) > 1 else 0.0
    win_ci = 1.96 * deal_sd / math.sqrt(len(deal_wins)) * 100.0 if deal_wins else 0.0
    return {
        "hands": total_hands,
        "deals": len(pair_means),
        "mean_net": mean_net,
        "mean_bb100": mean_net / max(1, bb) * 100.0,
        "ci_net": ci,
        "ci_bb100": ci / max(1, bb) * 100.0,
        "win_pct": win_rate * 100.0,
        "deal_win_pct": deal_win_rate * 100.0,
        "win_ci_pp": win_ci,
        "push_pct": pushes / total_hands * 100.0 if total_hands else 0.0,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    args.device = resolve_device(args.device)
    set_seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)

    args.log_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_dir = args.log_dir / "checkpoints"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)

    env = ProjectHeadsUpEnv(
        starting_chips=args.chips,
        small_blind=args.sb,
        big_blind=args.bb,
        seed=args.seed,
        max_actions_per_hand=args.max_actions,
    )
    eval_env = ProjectHeadsUpEnv(
        starting_chips=args.chips,
        small_blind=args.sb,
        big_blind=args.bb,
        seed=args.eval_seed,
        max_actions_per_hand=args.max_actions,
    )

    learner0 = make_agent(args.algorithm, args)
    learner1 = make_agent(args.algorithm, args) if args.opponent in ("self", "mix") else None
    random_agent = RandomAgent(num_actions=NUM_ACTIONS)

    print(
        f"algorithm={args.algorithm} hands={args.hands} chips={args.chips} "
        f"blinds={args.sb}/{args.bb} device={args.device} opponent={args.opponent}"
    )
    print(f"obs_dim={OBS_DIM} actions={NUM_ACTIONS} output={args.output}")
    print(f"log_dir={args.log_dir}")

    hand_rows: List[dict] = []
    eval_rows: List[dict] = []
    window: List[float] = []
    best_score = -float("inf")
    best_eval: Optional[dict] = None
    best_hand: Optional[int] = None
    best_path = args.output.with_name(f"{args.output.stem}_best{args.output.suffix}")
    started = time.perf_counter()

    for hand in range(1, args.hands + 1):
        use_random = args.opponent == "random" or (
            args.opponent == "mix" and float(np.random.rand()) < args.random_prob
        )
        if use_random:
            active = [learner0, random_agent]
            learning_seats = [0]
        else:
            if learner1 is None:
                raise RuntimeError("self/mix 模式需要 learner1")
            active = [learner0, learner1]
            learning_seats = [0, 1]

        env.reset()
        if args.algorithm == "nfsp":
            for seat in learning_seats:
                active[seat].sample_episode_policy()

        with contextlib.redirect_stdout(io.StringIO()):
            payoffs, transitions = env.play_hand(active, training=True)
            env.feed_transitions(active, transitions, payoffs, seats=learning_seats)

        net_chips0 = float(payoffs[0]) * (args.chips / 5.0)
        net_chips1 = float(payoffs[1]) * (args.chips / 5.0) if 1 in learning_seats else 0.0
        hand_rows.append(
            {
                "hand": hand,
                "random_opponent": int(use_random),
                "net_chips0": round(net_chips0, 2),
                "net_chips1": round(net_chips1, 2),
                "bb100_0": round(net_chips0 / max(1, args.bb) * 100.0, 2),
                "bb100_1": round(net_chips1 / max(1, args.bb) * 100.0, 2),
            }
        )
        window.append(net_chips0)

        if hand % args.log_every == 0 or hand == args.hands:
            avg_net = statistics.fmean(window) if window else 0.0
            elapsed = time.perf_counter() - started
            extra = ""
            if args.algorithm == "dqn":
                extra = f" epsilon={epsilon_of(learner0):.3f}"
            print(
                f"hand {hand:>7}/{args.hands}  "
                f"avg_net={avg_net:+.1f} chips ({avg_net / max(1, args.bb) * 100:+.1f} bb/100)  "
                f"total_t={getattr(learner0, 'total_t', 0)}  "
                f"train_t={getattr(learner0, 'train_t', 0)}  "
                f"elapsed={elapsed:.1f}s{extra}",
                flush=True,
            )
            window = []

        if hand % args.eval_every == 0 or hand == args.hands:
            metrics = evaluate_vs_random(
                learner0,
                eval_env,
                hands=args.eval_hands,
                seed=args.eval_seed + hand,
                bb=args.bb,
            )
            eval_row = {"hand": hand, **metrics}
            eval_rows.append(eval_row)
            print(
                f"  eval vs random: {metrics['mean_bb100']:+.1f} bb/100 "
                f"(+/-{metrics['ci_bb100']:.1f}), win {metrics['win_pct']:.1f}%, "
                f"push {metrics['push_pct']:.1f}%",
                flush=True,
            )
            checkpoint_path = checkpoint_dir / f"{args.algorithm}_{hand}.pth"
            save_agent(
                checkpoint_path,
                args.algorithm,
                learner0,
                metadata={
                    "hand": hand,
                    "eval": metrics,
                    "evaluation": "duplicate_deals_vs_random",
                    "learner_policy": "sample" if args.algorithm == "nfsp" else "argmax",
                },
            )
            score = metrics["mean_bb100"] - metrics["ci_bb100"]
            if score > best_score:
                best_score = score
                best_eval = metrics
                best_hand = hand
                save_agent(
                    best_path,
                    args.algorithm,
                    learner0,
                    metadata={
                        "hand": hand,
                        "eval": metrics,
                        "best": True,
                        "evaluation": "duplicate_deals_vs_random",
                    },
                )

    elapsed = time.perf_counter() - started
    save_agent(
        args.output,
        args.algorithm,
        learner0,
        metadata={
            "hand": args.hands,
            "final": True,
            "best_hand": best_hand,
            "evaluation": "duplicate_deals_vs_random",
        },
    )

    _write_csv(args.log_dir / "hands.csv", hand_rows)
    _write_csv(args.log_dir / "eval.csv", eval_rows)
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "algorithm": args.algorithm,
        "hands": args.hands,
        "chips": args.chips,
        "small_blind": args.sb,
        "big_blind": args.bb,
        "seed": args.seed,
        "opponent": args.opponent,
        "random_prob": args.random_prob,
        "obs_dim": OBS_DIM,
        "num_actions": NUM_ACTIONS,
        "mlp_layers": list(args.mlp),
        "device": args.device,
        "duration_sec": round(elapsed, 3),
        "final_output": str(args.output),
        "best_output": str(best_path),
        "best_eval": best_eval,
        "eval_history": eval_rows,
        "python": sys.version.split()[0],
        "torch": torch.__version__,
        "rlcard": getattr(rlcard, "__version__", None),
    }
    (args.log_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"done in {elapsed:.1f}s")
    print(f"final: {args.output}")
    print(f"best:  {best_path}")
    print(f"logs:  {args.log_dir}")
    return 0


def _write_csv(path: Path, rows: Sequence[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


if __name__ == "__main__":
    raise SystemExit(main())
