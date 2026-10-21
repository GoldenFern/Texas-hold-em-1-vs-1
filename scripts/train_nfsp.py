"""Train an RLCard NFSP checkpoint for the heads-up bot.

Example:
    python scripts/train_nfsp.py --episodes 50000 --chips 1000

Training runs in RLCard's own ``no-limit-holdem`` sandbox. The saved
checkpoint can be loaded by ``RLCardBot(agent_type="nfsp", ...)``.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import logging
import time
from pathlib import Path

import rlcard
from rlcard.agents import NFSPAgent
from rlcard.utils import reorganize, set_seed

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = PROJECT_ROOT / "models" / "rlcard" / "nlh_nfsp.pth"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train an RLCard NFSP for heads-up NLHE")
    parser.add_argument("--episodes", type=int, default=50000)
    parser.add_argument("--chips", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--log-every", type=int, default=1000)
    parser.add_argument("--reward-scale", type=float, default=0.01)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--hidden", type=int, nargs="+", default=[128, 128])
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--min-buffer", type=int, default=500)
    return parser.parse_args()


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
        NFSPAgent(
            num_actions=env.num_actions,
            state_shape=env.state_shape[0],
            hidden_layers_sizes=list(args.hidden),
            q_mlp_layers=list(args.hidden),
            batch_size=args.batch_size,
            min_buffer_size_to_learn=args.min_buffer,
            device=args.device,
            evaluate_with="average_policy",
        )
        for _ in range(2)
    ]
    env.set_agents(agents)

    started = time.perf_counter()
    payoff_sum = 0.0
    payoff_count = 0
    for episode in range(1, args.episodes + 1):
        for agent in agents:
            agent.sample_episode_policy()
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
                f"avg_payoff={avg:+.3f}  "
                f"steps={agents[0].total_t}  "
                f"train_steps={agents[0].train_t}  "
                f"elapsed={elapsed:.1f}s"
            )
            payoff_sum = 0.0
            payoff_count = 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    import torch

    checkpoint = agents[0].checkpoint_attributes()
    # Buffers are only needed for further training and dominate the file size.
    checkpoint["reservoir_buffer"]["data"] = []
    checkpoint["reservoir_buffer"]["add_calls"] = 0
    checkpoint["rl_agent"]["memory"]["memory"] = []
    torch.save(checkpoint, args.output)
    print(f"saved {args.output} ({args.output.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
