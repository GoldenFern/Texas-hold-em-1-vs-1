"""Train a RLCard DQN checkpoint for the heads-up bot.

Example:
    python scripts/train_rlcard.py --episodes 20000 --chips 1000

The saved checkpoint can be selected in the setup modal (RLCard -> DQN) or
loaded by a custom bot config. Training runs entirely in RLCard's own
``no-limit-holdem`` sandbox; the web engine is not involved.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import logging
import time
from pathlib import Path

import rlcard
from rlcard.agents import DQNAgent
from rlcard.utils import reorganize, set_seed

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / "models" / "rlcard" / "nlh_dqn.pth"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train an RLCard DQN for heads-up NLHE")
    parser.add_argument("--episodes", type=int, default=20000)
    parser.add_argument("--chips", type=int, default=1000, help="chips_for_each in the RLCard sandbox")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--log-every", type=int, default=1000)
    parser.add_argument("--learning-rate", type=float, default=1e-4)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--epsilon-decay-steps", type=int, default=20000)
    parser.add_argument(
        "--reward-scale",
        type=float,
        default=1.0,
        help="multiply chip payoffs before feeding the DQN (try 0.01 for calmer Q-values)",
    )
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--mlp", type=int, nargs="+", default=[128, 128])
    args = parser.parse_args()
    if args.episodes <= 0:
        parser.error("--episodes 必须为正")
    if args.chips <= 0:
        parser.error("--chips 必须为正")
    if args.log_every <= 0:
        parser.error("--log-every 必须为正")
    if args.batch_size <= 0:
        parser.error("--batch-size 必须为正")
    if args.epsilon_decay_steps <= 0:
        parser.error("--epsilon-decay-steps 必须为正")
    if args.reward_scale <= 0:
        parser.error("--reward-scale 必须为正")
    if args.learning_rate <= 0:
        parser.error("--learning-rate 必须为正")
    if not args.mlp or any(size <= 0 for size in args.mlp):
        parser.error("--mlp 每一层必须为正")
    return args


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    set_seed(args.seed)
    env = rlcard.make(
        "no-limit-holdem",
        config={
            "seed": args.seed,
            "game_num_players": 2,
            "chips_for_each": args.chips,
        },
    )
    print(f"RLCard env: state_shape={env.state_shape[0]} actions={env.num_actions} chips={args.chips}")

    agents = [
        DQNAgent(
            num_actions=env.num_actions,
            state_shape=env.state_shape[0],
            mlp_layers=list(args.mlp),
            learning_rate=args.learning_rate,
            batch_size=args.batch_size,
            replay_memory_init_size=min(500, max(100, args.episodes // 4)),
            epsilon_decay_steps=args.epsilon_decay_steps,
            device=args.device,
            train_every=1,
        )
        for _ in range(2)
    ]
    env.set_agents(agents)

    started = time.perf_counter()
    payoff_sum = 0.0
    payoff_count = 0
    for episode in range(1, args.episodes + 1):
        # RLCard's DQN prints a line for every training step. Keep the
        # progress readable by swallowing those prints.
        with contextlib.redirect_stdout(io.StringIO()):
            trajectories, payoffs = env.run(is_training=True)
            if args.reward_scale != 1.0:
                payoffs = [float(value) * args.reward_scale for value in payoffs]
            reorganized = reorganize(trajectories, payoffs)
            for index, agent in enumerate(agents):
                for transition in reorganized[index]:
                    agent.feed(transition)
        payoff_sum += float(payoffs[0])
        payoff_count += 1

        if episode % args.log_every == 0 or episode == args.episodes:
            elapsed = time.perf_counter() - started
            avg = payoff_sum / max(1, payoff_count)
            print(
                f"episode {episode:>7}/{args.episodes}  "
                f"avg_payoff={avg:+.2f}  "
                f"steps={agents[0].total_t}  "
                f"train_steps={agents[0].train_t}  "
                f"elapsed={elapsed:.1f}s"
            )
            payoff_sum = 0.0
            payoff_count = 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    import torch

    checkpoint = agents[0].checkpoint_attributes()
    # Replay memory is only needed for further training and makes the file
    # huge. Keep the estimator weights for serving.
    checkpoint["memory"]["memory"] = []
    torch.save(checkpoint, args.output)
    print(f"saved {args.output} ({args.output.stat().st_size / 1024:.0f} KB)")
    print("Select it in the setup modal: RLCard -> DQN -> model file.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
