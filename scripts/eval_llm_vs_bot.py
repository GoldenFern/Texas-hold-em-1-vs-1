"""Play an LLM agent against the strongest project bot, offline and in parallel.

Each hand is an independent 100 BB heads-up game with fresh chips.  A fresh
LLM conversation is created per hand, so the 25 hands can run concurrently
without sharing provider history.  Deals are normally paired: the same deck is
played twice with the LLM on each seat, except for a final odd hand.

Examples
--------
Smoke test (one hand, one worker)::

    python scripts/eval_llm_vs_bot.py --hands 1 --workers 1

Full run against the default Project DQN checkpoint::

    python scripts/eval_llm_vs_bot.py --hands 25 --workers 25

The script never starts Flask or Socket.IO.  It writes ``hands.csv``,
``hands.jsonl``, ``summary.json`` and ``report.md`` under
``tmp/llm_eval/<timestamp>/``.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
import time
import traceback
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

from src.agents.base import AgentView
from src.agents.project_bot import ProjectBot
from src.analysis.hand_distribution import HandDistributionAnalyzer
from src.engine.actions import Action, ActionType
from src.engine.heads_up import HeadsUpGame, Street
from src.llm.agent import LLMAgent
from src.llm.client import LLMClient
from src.llm.providers import ModelSpec, ProviderRegistry

DEFAULT_BOT_MODEL = PROJECT_ROOT / "models" / "rlcard" / "nlh_dqn_project.pth"
GAME_CONFIG_PATH = PROJECT_ROOT / "config" / "game.json"
MAX_ACTIONS_PER_HAND = 500

INVALID_ACTION_REASONS = (
    "连续三次无效动作",
    "连续多次没有提交动作",
    "单回合超过",
)

CSV_FIELDS = [
    "hand_index",
    "deal_index",
    "assignment",
    "llm_seat",
    "button",
    "seed",
    "hole_llm",
    "hole_bot",
    "board",
    "reason",
    "winner_seats",
    "pot",
    "net_llm",
    "net_bot",
    "llm_actions_count",
    "bot_actions_count",
    "llm_turns",
    "api_calls",
    "input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "auto_turns",
    "invalid_action_auto",
    "api_errors",
    "infra_error",
    "fatal_error",
    "fallbacks",
    "duration_sec",
    "attempts",
]


@dataclass(frozen=True)
class HandTask:
    """One independent hand, including its deal and seat assignment."""

    index: int
    deal_index: int
    assignment: int
    seed: int

    @property
    def llm_seat(self) -> int:
        return self.assignment

    @property
    def label(self) -> str:
        return f"hand {self.index + 1:02d} / deal {self.deal_index + 1:02d}"


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run concurrent LLM vs project-bot heads-up hands."
    )
    parser.add_argument("--hands", type=int, default=25, help="Total hands to play.")
    parser.add_argument("--workers", type=int, default=25, help="Concurrent hands.")
    parser.add_argument("--provider", default="deepseek")
    parser.add_argument("--model", default="deepseek-flash")
    parser.add_argument("--bot-model", type=Path, default=DEFAULT_BOT_MODEL)
    parser.add_argument("--bot-algorithm", default="dqn", choices=("dqn", "nfsp"))
    parser.add_argument("--chips", type=int, default=1000)
    parser.add_argument("--sb", type=int, default=5)
    parser.add_argument("--bb", type=int, default=10)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--top-p", type=float, default=None)
    parser.add_argument("--max-tokens", type=int, default=None)
    parser.add_argument("--reasoning-effort", default=None)
    thinking = parser.add_mutually_exclusive_group()
    thinking.add_argument("--thinking", dest="thinking", action="store_true")
    thinking.add_argument("--no-thinking", dest="thinking", action="store_false")
    parser.set_defaults(thinking=None)
    parser.add_argument("--max-steps", type=int, default=None)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--connect-timeout", type=float, default=20.0)
    parser.add_argument("--retries", type=int, default=1, help="Retries for API/fatal failures.")
    parser.add_argument(
        "--no-duplicate",
        dest="duplicate",
        action="store_false",
        help="Do not replay each deck with the seats swapped.",
    )
    parser.set_defaults(duplicate=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--quiet", action="store_true")

    args = parser.parse_args(argv)
    if args.hands <= 0:
        parser.error("--hands must be positive")
    if args.workers <= 0:
        parser.error("--workers must be positive")
    if args.chips <= 0:
        parser.error("--chips must be positive")
    if args.sb <= 0 or args.bb < args.sb:
        parser.error("blinds must satisfy 0 < sb <= bb")
    if args.retries < 0:
        parser.error("--retries cannot be negative")
    if args.timeout <= 0 or args.connect_timeout <= 0:
        parser.error("timeouts must be positive")
    if args.max_steps is not None and args.max_steps <= 0:
        parser.error("--max-steps must be positive")
    if args.max_tokens is not None and args.max_tokens <= 0:
        parser.error("--max-tokens must be positive")
    if args.temperature is not None and not 0 <= args.temperature <= 2:
        parser.error("--temperature must be between 0 and 2")
    if args.top_p is not None and not 0 < args.top_p <= 1:
        parser.error("--top-p must be in (0, 1]")
    args.workers = min(args.workers, args.hands)
    return args


def derive_seed(master: int, deal_index: int) -> int:
    return (int(master) + 1_000_003 * (deal_index + 1)) & 0x7FFFFFFF


def build_tasks(args: argparse.Namespace) -> List[HandTask]:
    """Build hand tasks, mirroring decks across seats when possible."""
    tasks: List[HandTask] = []
    if args.duplicate:
        pairs = args.hands // 2
        for deal_index in range(pairs):
            seed = derive_seed(args.seed, deal_index)
            tasks.append(HandTask(len(tasks), deal_index, 0, seed))
            tasks.append(HandTask(len(tasks), deal_index, 1, seed))
        if args.hands % 2:
            deal_index = pairs
            seed = derive_seed(args.seed, deal_index)
            # Keep the seat split within one hand.
            assignment = 0 if sum(t.assignment == 0 for t in tasks) <= sum(
                t.assignment == 1 for t in tasks
            ) else 1
            tasks.append(HandTask(len(tasks), deal_index, assignment, seed))
    else:
        for hand_index in range(args.hands):
            tasks.append(
                HandTask(
                    index=hand_index,
                    deal_index=hand_index,
                    assignment=hand_index % 2,
                    seed=derive_seed(args.seed, hand_index),
                )
            )
    return tasks


def load_runtime(args: argparse.Namespace) -> dict:
    with GAME_CONFIG_PATH.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    runtime = dict(config.get("llm_runtime", {}))
    if args.max_steps is not None:
        runtime["max_steps_per_turn"] = int(args.max_steps)
    return runtime


def resolve_params(spec: ModelSpec, args: argparse.Namespace) -> dict:
    raw: Dict[str, Any] = {}
    if args.temperature is not None:
        raw["temperature"] = args.temperature
    if args.top_p is not None:
        raw["top_p"] = args.top_p
    if args.max_tokens is not None:
        raw["max_tokens"] = args.max_tokens
    if args.reasoning_effort is not None:
        raw["reasoning_effort"] = args.reasoning_effort
    if args.thinking is not None:
        raw["thinking"] = args.thinking
    return spec.normalize_params(raw)


def build_view(
    game: HeadsUpGame,
    seat: int,
    task: HandTask,
    total_hands: int,
) -> AgentView:
    """Fair-information view, matching ``eval_bots.build_view`` plus session text."""
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
        match_note=(
            f"这是一次独立评估牌局（第 {task.index + 1}/{total_hands} 手）。"
            f"盲注 {game.small_blind}/{game.big_blind}，起始筹码 {game.starting_chips}。"
            f"你当前 {player.chips}，对手 {opponent.chips}。"
        ),
        history_summary=[],
    )


def safe_action(game: HeadsUpGame, seat: int) -> Action:
    legal = {action.value for action in game.legal_actions(seat)}
    if "check" in legal:
        return Action(seat, ActionType.CHECK, source="system", auto=True)
    if "call" in legal:
        return Action(seat, ActionType.CALL, source="system", auto=True)
    return Action(seat, ActionType.FOLD, source="system", auto=True)


def compact_action(action: dict) -> dict:
    return {
        key: action.get(key)
        for key in (
            "hand_id",
            "street",
            "seat",
            "type",
            "label",
            "amount",
            "pot_after",
            "chips_after",
            "source",
            "auto",
        )
    }


def compact_events(events: Sequence[dict]) -> List[dict]:
    """Drop token-level deltas; keep the final messages and tool results."""
    dropped = {"reasoning_delta", "content_delta", "tool_call_start", "tool_call_args_delta"}
    return [dict(event) for event in events if event.get("type") not in dropped]


def count_auto_turns(events: Sequence[dict]) -> tuple[int, int]:
    auto_turns = 0
    invalid_auto = 0
    for event in events:
        if event.get("type") != "turn_end" or not event.get("auto"):
            continue
        auto_turns += 1
        reason = str(event.get("reason") or "")
        if any(marker in reason for marker in INVALID_ACTION_REASONS):
            invalid_auto += 1
    return auto_turns, invalid_auto


def make_llm_agent(
    task: HandTask,
    game: HeadsUpGame,
    spec: ModelSpec,
    analyzer: HandDistributionAnalyzer,
    params: dict,
    runtime: dict,
    client: LLMClient,
) -> LLMAgent:
    return LLMAgent(
        seat=task.llm_seat,
        name="DeepSeek V4.1 Flash",
        spec=spec,
        params=params,
        client=client,
        analyzer=analyzer,
        validate_action=lambda action: game.preview(action),
        runtime=runtime,
    )


def play_hand(
    task: HandTask,
    spec: ModelSpec,
    registry: ProviderRegistry,
    runtime: dict,
    args: argparse.Namespace,
) -> dict:
    """Play one hand. Returns a row even when the hand fails."""
    started = time.perf_counter()
    events: List[dict] = []
    game: Optional[HeadsUpGame] = None
    client: Optional[LLMClient] = None

    def emit(event: dict) -> None:
        event = dict(event)
        event["_ts"] = time.time()
        events.append(event)

    try:
        llm_seat = task.llm_seat
        bot_seat = 1 - llm_seat
        game = HeadsUpGame(
            starting_chips=args.chips,
            small_blind=args.sb,
            big_blind=args.bb,
            seed=task.seed,
        )
        game.button_index = -1
        names = {
            llm_seat: ("DeepSeek V4.1 Flash", "llm"),
            bot_seat: ("Project DQN", "bot"),
        }
        game.configure_players(
            {"name": names[0][0], "kind": names[0][1]},
            {"name": names[1][0], "kind": names[1][1]},
        )
        game.start_hand()

        analyzer = HandDistributionAnalyzer()
        params = resolve_params(spec, args)

        client = LLMClient(
            registry,
            connect_timeout=float(args.connect_timeout),
            read_timeout=float(args.timeout),
        )
        llm = make_llm_agent(task, game, spec, analyzer, params, runtime, client)
        bot = ProjectBot(
            name="Project DQN",
            model_path=str(args.bot_model),
            algorithm=args.bot_algorithm,
        )

        action_count = 0
        fallbacks = 0
        while game.street != Street.FINISHED:
            seat = game.actor
            if seat is None:
                raise RuntimeError("actor is None before the hand finished")
            action_count += 1
            if action_count > MAX_ACTIONS_PER_HAND:
                raise RuntimeError(
                    f"hand exceeded {MAX_ACTIONS_PER_HAND} actions; possible loop"
                )
            view = build_view(game, seat, task, args.hands)
            agent = llm if seat == llm_seat else bot
            action = agent.decide(view, emit)
            result = game.apply(action)
            if not result.ok:
                fallbacks += 1
                action = safe_action(game, seat)
                result = game.apply(action)
                if not result.ok:
                    raise RuntimeError(f"fallback action rejected: {result.error}")

        if game.result is None:
            raise RuntimeError("hand finished without a result")
        result = game.result
        players = {item["seat"]: item for item in result["players"]}
        net_llm = int(players[llm_seat]["net"])
        net_bot = int(players[bot_seat]["net"])
        if net_llm + net_bot != 0:
            raise RuntimeError(f"chips not conserved: {net_llm} + {net_bot}")

        llm_actions = [item for item in game.actions if item["seat"] == llm_seat]
        bot_actions = [item for item in game.actions if item["seat"] == bot_seat]
        auto_turns, invalid_auto = count_auto_turns(events)
        api_errors = sum(1 for event in events if event.get("type") == "error")
        llm_turns = sum(1 for event in events if event.get("type") == "turn_start")
        api_calls = sum(1 for event in events if event.get("type") == "llm_request")

        row = {
            "hand_index": task.index,
            "deal_index": task.deal_index,
            "assignment": task.assignment,
            "llm_seat": llm_seat,
            "button": game.button_index,
            "seed": task.seed,
            "hole_llm": " ".join(card.code for card in game.players[llm_seat].hole_cards),
            "hole_bot": " ".join(card.code for card in game.players[bot_seat].hole_cards),
            "board": " ".join(result.get("board") or []),
            "reason": result.get("reason", ""),
            "winner_seats": " ".join(str(seat) for seat in result.get("winner_seats") or []),
            "pot": int(result.get("pot", 0)),
            "net_llm": net_llm,
            "net_bot": net_bot,
            "llm_actions_count": len(llm_actions),
            "bot_actions_count": len(bot_actions),
            "llm_turns": llm_turns,
            "api_calls": api_calls,
            "input_tokens": int(llm.total_usage.get("input_tokens", 0)),
            "output_tokens": int(llm.total_usage.get("output_tokens", 0)),
            "reasoning_tokens": int(llm.total_usage.get("reasoning_tokens", 0)),
            "auto_turns": auto_turns,
            "invalid_action_auto": invalid_auto,
            "api_errors": api_errors,
            "infra_error": 0,
            "fatal_error": "",
            "duration_sec": round(time.perf_counter() - started, 3),
            "fallbacks": fallbacks,
            "actions": [compact_action(item) for item in game.actions],
            "events": compact_events(events),
        }
        return row
    except Exception as exc:  # noqa: BLE001
        return {
            "hand_index": task.index,
            "deal_index": task.deal_index,
            "assignment": task.assignment,
            "llm_seat": task.llm_seat,
            "button": game.button_index if game else None,
            "seed": task.seed,
            "hole_llm": "",
            "hole_bot": "",
            "board": "",
            "reason": "",
            "winner_seats": "",
            "pot": 0,
            "net_llm": 0,
            "net_bot": 0,
            "llm_actions_count": 0,
            "bot_actions_count": 0,
            "llm_turns": 0,
            "api_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "reasoning_tokens": 0,
            "auto_turns": 0,
            "invalid_action_auto": 0,
            "api_errors": 0,
            "infra_error": 0,
            "fatal_error": f"{type(exc).__name__}: {exc}",
            "duration_sec": round(time.perf_counter() - started, 3),
            "fallbacks": 0,
            "actions": [],
            "events": compact_events(events),
            "traceback": traceback.format_exc(),
        }
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def run_task(
    task: HandTask,
    spec: ModelSpec,
    registry: ProviderRegistry,
    runtime: dict,
    args: argparse.Namespace,
) -> dict:
    """Run one hand, retrying only API/fatal failures."""
    row: dict = {}
    for attempt in range(args.retries + 1):
        row = play_hand(task, spec, registry, runtime, args)
        row["attempts"] = attempt + 1
        retryable = bool(row.get("fatal_error")) or bool(row.get("infra_error"))
        if not retryable:
            break
        if attempt < args.retries:
            time.sleep(1.5 + attempt)
    return row


def _pct(counter: Counter, key: str, total: int) -> float:
    return round(100.0 * counter.get(key, 0) / total, 3) if total else 0.0


def summarize(rows: Sequence[dict], args: argparse.Namespace) -> dict:
    valid = [row for row in rows if not row.get("fatal_error")]
    failed = [row for row in rows if row.get("fatal_error")]
    if not valid:
        return {
            "hands": 0,
            "fatal_hands": len(failed),
            "total_net": 0,
            "mean_net": 0.0,
            "mean_bb100": 0.0,
            "win_pct": 0.0,
            "error": "all hands failed",
        }

    nets = [int(row["net_llm"]) for row in valid]
    n = len(nets)
    mean_net = statistics.fmean(nets)
    sd = statistics.pstdev(nets) if n > 1 else 0.0
    ci95 = 1.96 * sd / math.sqrt(n) if n else 0.0
    wins = sum(1 for value in nets if value > 0)
    pushes = sum(1 for value in nets if value == 0)
    win_rate = wins / n
    win_ci = 1.96 * math.sqrt(win_rate * (1.0 - win_rate) / n) * 100.0

    by_deal: Dict[int, List[int]] = defaultdict(list)
    for row in valid:
        by_deal[int(row["deal_index"])].append(int(row["net_llm"]))
    paired_means = [
        statistics.fmean(values) for values in by_deal.values() if len(values) == 2
    ]
    paired_win_values = [
        1.0 if sum(values) > 0 else 0.0
        for values in by_deal.values()
        if len(values) == 2
    ]
    paired_win_rate = statistics.fmean(paired_win_values) if paired_win_values else 0.0
    paired_win_sd = statistics.pstdev(paired_win_values) if len(paired_win_values) > 1 else 0.0
    paired_win_ci = (
        1.96 * paired_win_sd / math.sqrt(len(paired_win_values)) * 100.0
        if paired_win_values
        else 0.0
    )
    paired_mean = statistics.fmean(paired_means) if paired_means else 0.0
    paired_sd = statistics.pstdev(paired_means) if len(paired_means) > 1 else 0.0
    paired_ci = (
        1.96 * paired_sd / math.sqrt(len(paired_means)) if paired_means else 0.0
    )

    llm_counter: Counter = Counter()
    bot_counter: Counter = Counter()
    for row in valid:
        llm_seat = int(row["llm_seat"])
        for action in row.get("actions") or []:
            if int(action.get("seat", -1)) == llm_seat:
                llm_counter[str(action.get("type"))] += 1
            else:
                bot_counter[str(action.get("type"))] += 1

    total_actions = sum(int(row["llm_actions_count"]) + int(row["bot_actions_count"]) for row in valid)
    llm_total_actions = sum(llm_counter.values())
    bot_total_actions = sum(bot_counter.values())
    total_pot = sum(int(row["pot"]) for row in valid)
    total_net = sum(nets)
    api_errors = sum(int(row["api_errors"]) for row in valid)
    auto_turns = sum(int(row["auto_turns"]) for row in valid)
    invalid_auto = sum(int(row["invalid_action_auto"]) for row in valid)
    api_latencies = [
        float(event.get("latency_ms", 0.0))
        for row in valid
        for event in row.get("events") or []
        if event.get("type") == "message_end" and event.get("latency_ms") is not None
    ]
    turn_latencies = [
        float(event.get("latency_ms", 0.0)) / 1000.0
        for row in valid
        for event in row.get("events") or []
        if event.get("type") == "turn_end" and event.get("latency_ms") is not None
    ]

    return {
        "hands": n,
        "deals": len(by_deal),
        "paired_deals": len(paired_means),
        "fatal_hands": len(failed),
        "total_net": total_net,
        "mean_net": round(mean_net, 3),
        "mean_bb100": round(mean_net / args.bb * 100.0, 3),
        "sd_net": round(sd, 3),
        "ci95_net": round(ci95, 3),
        "ci95_bb100": round(ci95 / args.bb * 100.0, 3),
        "win_pct": round(win_rate * 100.0, 3),
        "win_ci_pp": round(paired_win_ci if paired_win_values else win_ci, 3),
        "win_ci_binomial_pp": round(win_ci, 3),
        "paired_win_pct": round(paired_win_rate * 100.0, 3),
        "push_pct": round(pushes / n * 100.0, 3),
        "loss_pct": round((n - wins - pushes) / n * 100.0, 3),
        "paired_mean_net": round(paired_mean, 3),
        "paired_mean_bb100": round(paired_mean / args.bb * 100.0, 3),
        "paired_ci95_net": round(paired_ci, 3),
        "paired_ci95_bb100": round(paired_ci / args.bb * 100.0, 3),
        "showdown_pct": round(
            100.0 * sum(1 for row in valid if row.get("reason") == "showdown") / n, 3
        ),
        "fold_end_pct": round(
            100.0 * sum(1 for row in valid if row.get("reason") == "fold") / n, 3
        ),
        "allin_hand_pct": round(
            100.0
            * sum(
                1
                for row in valid
                if any(
                    item.get("type") == "all_in"
                    or int(item.get("chips_after", 1)) == 0
                    for item in row.get("actions") or []
                )
            )
            / n,
            3,
        ),
        "avg_pot": round(total_pot / n, 3),
        "avg_actions": round(total_actions / n, 3),
        "avg_hand_sec": round(
            statistics.fmean(float(row["duration_sec"]) for row in valid), 3
        ),
        "llm_turns": sum(int(row["llm_turns"]) for row in valid),
        "api_calls": sum(int(row["api_calls"]) for row in valid),
        "input_tokens": sum(int(row["input_tokens"]) for row in valid),
        "output_tokens": sum(int(row["output_tokens"]) for row in valid),
        "reasoning_tokens": sum(int(row["reasoning_tokens"]) for row in valid),
        "api_errors": api_errors,
        "hands_with_api_error": sum(1 for row in valid if int(row["api_errors"]) > 0),
        "auto_turns": auto_turns,
        "invalid_action_auto": invalid_auto,
        "hands_with_auto": sum(1 for row in valid if int(row["auto_turns"]) > 0),
        "avg_api_latency_ms": round(statistics.fmean(api_latencies), 1)
        if api_latencies
        else 0.0,
        "avg_turn_sec": round(statistics.fmean(turn_latencies), 3)
        if turn_latencies
        else 0.0,
        "llm_total_actions": llm_total_actions,
        "bot_total_actions": bot_total_actions,
        "llm_fold_pct": _pct(llm_counter, "fold", llm_total_actions),
        "llm_check_pct": _pct(llm_counter, "check", llm_total_actions),
        "llm_call_pct": _pct(llm_counter, "call", llm_total_actions),
        "llm_bet_pct": _pct(llm_counter, "bet", llm_total_actions),
        "llm_raise_pct": _pct(llm_counter, "raise", llm_total_actions),
        "llm_allin_pct": _pct(llm_counter, "all_in", llm_total_actions),
        "bot_fold_pct": _pct(bot_counter, "fold", bot_total_actions),
        "bot_check_pct": _pct(bot_counter, "check", bot_total_actions),
        "bot_call_pct": _pct(bot_counter, "call", bot_total_actions),
        "bot_bet_pct": _pct(bot_counter, "bet", bot_total_actions),
        "bot_raise_pct": _pct(bot_counter, "raise", bot_total_actions),
        "bot_allin_pct": _pct(bot_counter, "all_in", bot_total_actions),
        "zero_sum_ok": int(
            all(int(row["net_llm"]) + int(row["net_bot"]) == 0 for row in valid)
        ),
    }


def write_csv(path: Path, rows: Sequence[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in CSV_FIELDS})


def write_jsonl(path: Path, rows: Sequence[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_report(path: Path, summary: dict, args: argparse.Namespace) -> None:
    lines = [
        "# DeepSeek vs Project DQN",
        "",
        f"- 生成时间: {datetime.now(timezone.utc).isoformat()}",
        f"- 模型: `{args.provider}/{args.model}`",
        f"- Bot: `{args.bot_algorithm}` / `{args.bot_model}`",
        f"- 手数: {summary.get('hands', 0)}，同牌换座组: {summary.get('paired_deals', 0)}",
        f"- 规则: {args.chips} 筹码，盲注 {args.sb}/{args.bb}，每手重置筹码",
        "",
        "## 结果",
        "",
        "| 指标 | 数值 |",
        "|---|---:|",
        f"| DeepSeek 总净筹码 | {summary.get('total_net', 0):+d} |",
        f"| DeepSeek 每手均值 | {summary.get('mean_net', 0):+.2f} |",
        f"| DeepSeek bb/100 | {summary.get('mean_bb100', 0):+.1f} |",
        f"| 胜率 | {summary.get('win_pct', 0):.1f}% |",
        f"| 同牌换座 bb/100 | {summary.get('paired_mean_bb100', 0):+.1f} |",
        f"| 摊牌率 | {summary.get('showdown_pct', 0):.1f}% |",
        f"| 全下手牌占比 | {summary.get('allin_hand_pct', 0):.1f}% |",
        f"| API 错误手数 | {summary.get('hands_with_api_error', 0)} |",
        "",
        "## 行动分布",
        "",
        "| 动作 | DeepSeek | Project DQN |",
        "|---|---:|---:|",
        f"| fold | {summary.get('llm_fold_pct', 0):.1f}% | {summary.get('bot_fold_pct', 0):.1f}% |",
        f"| check | {summary.get('llm_check_pct', 0):.1f}% | {summary.get('bot_check_pct', 0):.1f}% |",
        f"| call | {summary.get('llm_call_pct', 0):.1f}% | {summary.get('bot_call_pct', 0):.1f}% |",
        f"| bet | {summary.get('llm_bet_pct', 0):.1f}% | {summary.get('bot_bet_pct', 0):.1f}% |",
        f"| raise | {summary.get('llm_raise_pct', 0):.1f}% | {summary.get('bot_raise_pct', 0):.1f}% |",
        f"| all_in | {summary.get('llm_allin_pct', 0):.1f}% | {summary.get('bot_allin_pct', 0):.1f}% |",
        "",
        "## 说明",
        "",
        "- 每手都是独立牌局，筹码不跨手累积；总净筹码等价于带重购的 25 手总和。",
        "- 25 手样本很小，置信区间很宽，结果只能作为快速对照，不能当成稳定强弱结论。",
        "- 同牌换座只对偶数手生效；奇数手会多出一手未配对牌局。",
        "- `invalid_action_auto` 记录模型连续非法动作或超步数导致的自动跟注/弃牌。",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    tasks = build_tasks(args)
    if args.dry_run:
        for task in tasks:
            print(
                f"{task.index + 1:02d}: deal={task.deal_index + 1:02d} "
                f"LLM seat={task.llm_seat} seed={task.seed}"
            )
        print(f"tasks={len(tasks)} workers={args.workers}")
        return 0

    registry = ProviderRegistry()
    spec = registry.resolve(args.provider, args.model)
    bot_model = Path(args.bot_model)
    if not bot_model.is_absolute():
        bot_model = (PROJECT_ROOT / bot_model).resolve()
    args.bot_model = bot_model

    if not ProviderRegistry.key_present(spec):
        print(
            f"error: missing environment variable {spec.api_key_env} in .env",
            file=sys.stderr,
        )
        return 2
    if not bot_model.is_file():
        print(f"error: bot model not found: {bot_model}", file=sys.stderr)
        return 2

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    output = args.output or (PROJECT_ROOT / "tmp" / "llm_eval" / timestamp)
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    runtime = load_runtime(args)
    params = resolve_params(spec, args)

    print(f"output: {output}")
    print(
        f"LLM: {spec.provider_name} / {spec.name} ({spec.id}), "
        f"thinking={params.get('thinking')}, "
        f"effort={params.get('reasoning_effort') or '-'}, "
        f"max_tokens={params.get('max_tokens')}"
    )
    print(
        f"bot: {args.bot_algorithm} / {bot_model.name}, "
        f"hands={args.hands}, workers={args.workers}, duplicate={args.duplicate}"
    )
    print(
        f"table: {args.chips} chips, blinds {args.sb}/{args.bb}, seed {args.seed}"
    )

    started = time.perf_counter()
    rows: List[dict] = []
    cumulative = 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(run_task, task, spec, registry, runtime, args): task
            for task in tasks
        }
        for future in as_completed(futures):
            task = futures[future]
            try:
                row = future.result()
            except Exception as exc:  # noqa: BLE001
                row = {
                    "hand_index": task.index,
                    "deal_index": task.deal_index,
                    "assignment": task.assignment,
                    "llm_seat": task.llm_seat,
                    "fatal_error": f"{type(exc).__name__}: {exc}",
                    "infra_error": 0,
                    "api_errors": 0,
                    "net_llm": 0,
                    "net_bot": 0,
                    "pot": 0,
                    "llm_turns": 0,
                    "duration_sec": 0.0,
                    "attempts": 0,
                    "events": [],
                    "actions": [],
                }
            rows.append(row)
            if not row.get("fatal_error"):
                cumulative += int(row.get("net_llm", 0))
            if not args.quiet:
                if row.get("fatal_error"):
                    print(
                        f"[{len(rows):02d}/{len(tasks):02d}] {task.label}: "
                        f"FATAL {row['fatal_error']}",
                        flush=True,
                    )
                else:
                    seat = "BTN" if int(row["llm_seat"]) == 0 else "BB"
                    print(
                        f"[{len(rows):02d}/{len(tasks):02d}] {task.label} "
                        f"LLM={seat}: {int(row['net_llm']):+5d} chips, "
                        f"pot={int(row['pot']):4d}, turns={int(row['llm_turns']):2d}, "
                        f"{float(row['duration_sec']):5.1f}s, cum={cumulative:+d}"
                        + (
                            f", attempts={row['attempts']}"
                            if int(row.get("attempts", 1)) > 1
                            else ""
                        ),
                        flush=True,
                    )

    elapsed = time.perf_counter() - started
    rows.sort(key=lambda item: int(item.get("hand_index", 0)))
    summary = summarize(rows, args)
    summary["elapsed_sec"] = round(elapsed, 3)
    summary["provider"] = args.provider
    summary["model"] = args.model
    summary["bot_model"] = str(bot_model)
    summary["bot_algorithm"] = args.bot_algorithm
    summary["chips"] = args.chips
    summary["small_blind"] = args.sb
    summary["big_blind"] = args.bb
    summary["seed"] = args.seed
    summary["duplicate"] = bool(args.duplicate)
    summary["workers"] = args.workers
    summary["llm_params"] = params

    write_csv(output / "hands.csv", rows)
    write_jsonl(output / "hands.jsonl", rows)
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_report(output / "report.md", summary, args)

    print("")
    print(
        f"result: DeepSeek {summary['total_net']:+d} chips over {summary['hands']} hands, "
        f"{summary['mean_bb100']:+.1f} bb/100, win {summary['win_pct']:.1f}%"
    )
    print(
        f"paired: {summary.get('paired_mean_bb100', 0.0):+.1f} bb/100 "
        f"(95% CI {summary.get('paired_ci95_bb100', 0.0):+.1f}) over "
        f"{summary.get('paired_deals', 0)} mirrored deals"
    )
    print(
        f"api: {summary.get('api_calls', 0)} calls, "
        f"{summary.get('input_tokens', 0)} input / {summary.get('output_tokens', 0)} output tokens, "
        f"{summary.get('hands_with_api_error', 0)} hands with errors, "
        f"{summary.get('invalid_action_auto', 0)} invalid-action auto turns"
    )
    print(f"elapsed: {elapsed:.1f}s")
    print(f"summary: {output / 'summary.json'}")
    print(f"report:  {output / 'report.md'}")
    return 0 if summary["fatal_hands"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
