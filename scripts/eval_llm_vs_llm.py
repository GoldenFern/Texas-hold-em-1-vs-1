"""Play two LLM agents against each other, offline and in parallel.

This evaluation is built for controlled tool-use experiments.  One seat gets
only ``apply_action``.  The other seat additionally gets
``estimate_hand_probabilities``, ``estimate_equity``, ``get_random_number`` and
``call_ev``, and is required to call the hand-distribution estimator before
every action until the river (five community cards).
Each hand is an independent 100 BB heads-up game with fresh chips, and the same
deck is normally replayed with the seats swapped.

Examples
--------
Two-hand smoke test::

    python scripts/eval_llm_vs_llm.py --hands 2 --workers 2

The LongCat tool-use experiment::

    python scripts/eval_llm_vs_llm.py --hands 100 --workers 5

The script never starts Flask or Socket.IO.  It writes ``hands.csv``,
``hands.jsonl``, ``summary.json`` and ``report.md`` under
``tmp/llm_vs_llm/<timestamp>/``.
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
from src.analysis.hand_distribution import HandDistributionAnalyzer
from src.engine.actions import Action, ActionType
from src.engine.heads_up import HeadsUpGame, Street
from src.llm.agent import LLMAgent, select_tools
from src.llm.client import LLMClient
from src.llm.prompt import ALL_TOOL_NAMES, build_system_prompt
from src.llm.providers import ModelSpec, ProviderRegistry

GAME_CONFIG_PATH = PROJECT_ROOT / "config" / "game.json"
MAX_ACTIONS_PER_HAND = 500
CONTROL_TOOL_NAMES = ("apply_action",)
TREATMENT_TOOL_NAMES = ALL_TOOL_NAMES
CONTROL_NAME = "LongCat 2.5 Preview"
TREATMENT_NAME = "LongCat 2.5 Preview"
CONTROL_SYSTEM_PROMPT = build_system_prompt(CONTROL_TOOL_NAMES)
TREATMENT_SYSTEM_PROMPT = build_system_prompt(
    TREATMENT_TOOL_NAMES,
    require_estimate_each_turn=True,
)

INVALID_ACTION_REASONS = (
    "连续三次无效动作",
    "连续多次没有提交动作",
    "单回合超过",
)

CSV_FIELDS = [
    "hand_index",
    "deal_index",
    "treatment_seat",
    "button",
    "seed",
    "hole_treatment",
    "hole_control",
    "board",
    "reason",
    "winner_seats",
    "pot",
    "net_treatment",
    "net_control",
    "treatment_actions_count",
    "control_actions_count",
    "treatment_turns",
    "control_turns",
    "treatment_pre_river_turns",
    "treatment_pre_river_turns_with_estimate",
    "treatment_river_turns",
    "treatment_river_turns_with_estimate",
    "treatment_api_calls",
    "control_api_calls",
    "treatment_input_tokens",
    "control_input_tokens",
    "treatment_output_tokens",
    "control_output_tokens",
    "treatment_reasoning_tokens",
    "control_reasoning_tokens",
    "treatment_estimate_calls",
    "control_estimate_calls",
    "treatment_random_calls",
    "control_random_calls",
    "treatment_estimate_nudges",
    "treatment_auto_turns",
    "control_auto_turns",
    "invalid_action_auto",
    "api_errors",
    "fallbacks",
    "infra_error",
    "fatal_error",
    "duration_sec",
    "attempts",
]


@dataclass(frozen=True)
class HandTask:
    """One independent hand, including its deal and treatment-seat assignment."""

    index: int
    deal_index: int
    treatment_seat: int
    seed: int

    @property
    def label(self) -> str:
        return f"hand {self.index + 1:03d} / deal {self.deal_index + 1:03d}"


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run concurrent LLM vs LLM heads-up tool-use experiments."
    )
    parser.add_argument("--hands", type=int, default=100, help="Total hands to play.")
    parser.add_argument("--workers", type=int, default=5, help="Concurrent hands.")
    parser.add_argument("--provider", default="longcat")
    parser.add_argument("--model", default="LongCat-2.5-Preview")
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
            treatment_on_zero = sum(t.treatment_seat == 0 for t in tasks)
            treatment_on_one = sum(t.treatment_seat == 1 for t in tasks)
            seat = 0 if treatment_on_zero <= treatment_on_one else 1
            tasks.append(HandTask(len(tasks), deal_index, seat, seed))
    else:
        for hand_index in range(args.hands):
            tasks.append(
                HandTask(
                    index=hand_index,
                    deal_index=hand_index,
                    treatment_seat=hand_index % 2,
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
    name: str,
) -> AgentView:
    """Fair-information view for one seat."""
    player = game.players[seat]
    opponent = game.players[1 - seat]
    constraints = game.action_constraints(seat)
    return AgentView(
        seat=seat,
        name=name,
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
        hand_number=task.index + 1,
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
    """Drop token-level deltas; keep final messages and tool results."""
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


def make_agent(
    seat: int,
    name: str,
    spec: ModelSpec,
    params: dict,
    runtime: dict,
    client: LLMClient,
    analyzer: HandDistributionAnalyzer,
    game: HeadsUpGame,
    tool_names: Sequence[str],
    system_prompt: str,
) -> LLMAgent:
    return LLMAgent(
        seat=seat,
        name=name,
        spec=spec,
        params=params,
        client=client,
        analyzer=analyzer,
        validate_action=lambda action: game.preview(action),
        runtime=runtime,
        tools=select_tools(tool_names),
        system_prompt=system_prompt,
    )


def hand_usage(agent: LLMAgent) -> dict:
    return {
        "input_tokens": int(agent.total_usage.get("input_tokens", 0)),
        "output_tokens": int(agent.total_usage.get("output_tokens", 0)),
        "reasoning_tokens": int(agent.total_usage.get("reasoning_tokens", 0)),
    }


def hand_event_stats(events: Sequence[dict], seat: int) -> dict:
    stats = {
        "turns": 0,
        "pre_river_turns": 0,
        "pre_river_turns_with_estimate": 0,
        "river_turns": 0,
        "river_turns_with_estimate": 0,
        "estimate_calls": 0,
        "random_calls": 0,
        "estimate_nudges": 0,
        "api_calls": 0,
        "api_errors": 0,
        "auto_turns": 0,
        "invalid_auto_turns": 0,
    }
    estimate_turns: set[str] = set()
    for event in events:
        if int(event.get("seat", -1)) != seat:
            continue
        event_type = event.get("type")
        if event_type == "turn_start":
            stats["turns"] += 1
            street = str(event.get("street") or "")
            if street == "river":
                stats["river_turns"] += 1
            elif street in ("preflop", "flop", "turn"):
                stats["pre_river_turns"] += 1
        elif event_type == "tool_result":
            result = event.get("result") or {}
            name = event.get("name")
            if name == "estimate_hand_probabilities" and event.get("ok"):
                stats["estimate_calls"] += 1
                turn_id = str(event.get("turn_id") or "")
                if turn_id:
                    estimate_turns.add(turn_id)
            elif name == "get_random_number" and event.get("ok"):
                stats["random_calls"] += 1
            if result.get("required_tool") == "estimate_hand_probabilities":
                stats["estimate_nudges"] += 1
        elif event_type == "llm_request":
            stats["api_calls"] += 1
        elif event_type == "error":
            stats["api_errors"] += 1
        elif event_type == "turn_end" and event.get("auto"):
            stats["auto_turns"] += 1
            reason = str(event.get("reason") or "")
            if any(marker in reason for marker in INVALID_ACTION_REASONS):
                stats["invalid_auto_turns"] += 1

    river_turn_ids = {
        str(event.get("turn_id") or "")
        for event in events
        if event.get("type") == "turn_start"
        and int(event.get("seat", -1)) == seat
        and str(event.get("street") or "") == "river"
    }
    stats["river_turns_with_estimate"] = len(
        {turn_id for turn_id in river_turn_ids if turn_id in estimate_turns}
    )

    pre_river_turn_ids = {
        str(event.get("turn_id") or "")
        for event in events
        if event.get("type") == "turn_start"
        and int(event.get("seat", -1)) == seat
        and str(event.get("street") or "") in ("preflop", "flop", "turn")
    }
    stats["pre_river_turns_with_estimate"] = len(
        {turn_id for turn_id in pre_river_turn_ids if turn_id in estimate_turns}
    )
    return stats


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

    def make_emit(seat: int):
        def emit(event: dict) -> None:
            event = dict(event)
            event["seat"] = seat
            event["_ts"] = time.time()
            events.append(event)

        return emit

    try:
        treatment_seat = task.treatment_seat
        control_seat = 1 - treatment_seat
        game = HeadsUpGame(
            starting_chips=args.chips,
            small_blind=args.sb,
            big_blind=args.bb,
            seed=task.seed,
        )
        game.button_index = -1
        game.configure_players(
            {"name": CONTROL_NAME, "kind": "llm"},
            {"name": CONTROL_NAME, "kind": "llm"},
        )
        game.start_hand()

        analyzer = HandDistributionAnalyzer()
        params = resolve_params(spec, args)
        client = LLMClient(
            registry,
            connect_timeout=float(args.connect_timeout),
            read_timeout=float(args.timeout),
        )

        control_runtime = dict(runtime)
        control_runtime["require_estimate_first"] = False
        control_runtime["require_estimate_each_turn"] = False
        control_runtime["skip_estimate_on_complete_board"] = False

        treatment_runtime = dict(runtime)
        treatment_runtime["require_estimate_first"] = False
        treatment_runtime["require_estimate_each_turn"] = True
        treatment_runtime["skip_estimate_on_complete_board"] = True

        control = make_agent(
            seat=control_seat,
            name=CONTROL_NAME,
            spec=spec,
            params=params,
            runtime=control_runtime,
            client=client,
            analyzer=analyzer,
            game=game,
            tool_names=CONTROL_TOOL_NAMES,
            system_prompt=CONTROL_SYSTEM_PROMPT,
        )
        treatment = make_agent(
            seat=treatment_seat,
            name=TREATMENT_NAME,
            spec=spec,
            params=params,
            runtime=treatment_runtime,
            client=client,
            analyzer=analyzer,
            game=game,
            tool_names=TREATMENT_TOOL_NAMES,
            system_prompt=TREATMENT_SYSTEM_PROMPT,
        )
        agents = {control_seat: control, treatment_seat: treatment}
        emits = {control_seat: make_emit(control_seat), treatment_seat: make_emit(treatment_seat)}

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
            view = build_view(
                game,
                seat,
                task,
                args.hands,
                TREATMENT_NAME if seat == treatment_seat else CONTROL_NAME,
            )
            action = agents[seat].decide(view, emits[seat])
            result = game.apply(action)
            if not result.ok:
                fallbacks += 1
                emits[seat](
                    {
                        "type": "note",
                        "level": "error",
                        "text": f"动作被引擎拒绝，执行安全动作：{result.error}",
                    }
                )
                action = safe_action(game, seat)
                result = game.apply(action)
                if not result.ok:
                    raise RuntimeError(f"fallback action rejected: {result.error}")

        if game.result is None:
            raise RuntimeError("hand finished without a result")
        result = game.result
        players = {item["seat"]: item for item in result["players"]}
        net_treatment = int(players[treatment_seat]["net"])
        net_control = int(players[control_seat]["net"])
        if net_treatment + net_control != 0:
            raise RuntimeError(f"chips not conserved: {net_treatment} + {net_control}")

        treatment_actions = [item for item in game.actions if item["seat"] == treatment_seat]
        control_actions = [item for item in game.actions if item["seat"] == control_seat]
        treatment_stats = hand_event_stats(events, treatment_seat)
        control_stats = hand_event_stats(events, control_seat)
        auto_turns, invalid_auto = count_auto_turns(events)
        api_errors = sum(1 for event in events if event.get("type") == "error")
        treatment_usage = hand_usage(treatment)
        control_usage = hand_usage(control)

        row = {
            "hand_index": task.index,
            "deal_index": task.deal_index,
            "treatment_seat": treatment_seat,
            "button": game.button_index,
            "seed": task.seed,
            "hole_treatment": " ".join(
                card.code for card in game.players[treatment_seat].hole_cards
            ),
            "hole_control": " ".join(
                card.code for card in game.players[control_seat].hole_cards
            ),
            "board": " ".join(result.get("board") or []),
            "reason": result.get("reason", ""),
            "winner_seats": " ".join(
                str(seat) for seat in result.get("winner_seats") or []
            ),
            "pot": int(result.get("pot", 0)),
            "net_treatment": net_treatment,
            "net_control": net_control,
            "treatment_actions_count": len(treatment_actions),
            "control_actions_count": len(control_actions),
            "treatment_turns": treatment_stats["turns"],
            "control_turns": control_stats["turns"],
            "treatment_pre_river_turns": treatment_stats["pre_river_turns"],
            "treatment_pre_river_turns_with_estimate": treatment_stats[
                "pre_river_turns_with_estimate"
            ],
            "treatment_river_turns": treatment_stats["river_turns"],
            "treatment_river_turns_with_estimate": treatment_stats[
                "river_turns_with_estimate"
            ],
            "treatment_api_calls": treatment_stats["api_calls"],
            "control_api_calls": control_stats["api_calls"],
            "treatment_input_tokens": treatment_usage["input_tokens"],
            "control_input_tokens": control_usage["input_tokens"],
            "treatment_output_tokens": treatment_usage["output_tokens"],
            "control_output_tokens": control_usage["output_tokens"],
            "treatment_reasoning_tokens": treatment_usage["reasoning_tokens"],
            "control_reasoning_tokens": control_usage["reasoning_tokens"],
            "treatment_estimate_calls": treatment_stats["estimate_calls"],
            "control_estimate_calls": control_stats["estimate_calls"],
            "treatment_random_calls": treatment_stats["random_calls"],
            "control_random_calls": control_stats["random_calls"],
            "treatment_estimate_nudges": treatment_stats["estimate_nudges"],
            "treatment_auto_turns": treatment_stats["auto_turns"],
            "control_auto_turns": control_stats["auto_turns"],
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
            "treatment_seat": task.treatment_seat,
            "button": game.button_index if game else None,
            "seed": task.seed,
            "hole_treatment": "",
            "hole_control": "",
            "board": "",
            "reason": "",
            "winner_seats": "",
            "pot": 0,
            "net_treatment": 0,
            "net_control": 0,
            "treatment_actions_count": 0,
            "control_actions_count": 0,
            "treatment_turns": 0,
            "control_turns": 0,
            "treatment_pre_river_turns": 0,
            "treatment_pre_river_turns_with_estimate": 0,
            "treatment_river_turns": 0,
            "treatment_river_turns_with_estimate": 0,
            "treatment_api_calls": 0,
            "control_api_calls": 0,
            "treatment_input_tokens": 0,
            "control_input_tokens": 0,
            "treatment_output_tokens": 0,
            "control_output_tokens": 0,
            "treatment_reasoning_tokens": 0,
            "control_reasoning_tokens": 0,
            "treatment_estimate_calls": 0,
            "control_estimate_calls": 0,
            "treatment_random_calls": 0,
            "control_random_calls": 0,
            "treatment_estimate_nudges": 0,
            "treatment_auto_turns": 0,
            "control_auto_turns": 0,
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
            "treatment_total_net": 0,
            "treatment_mean_net": 0.0,
            "treatment_mean_bb100": 0.0,
            "treatment_win_pct": 0.0,
            "error": "all hands failed",
        }

    nets = [int(row["net_treatment"]) for row in valid]
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
        by_deal[int(row["deal_index"])].append(int(row["net_treatment"]))
    paired_means = [
        statistics.fmean(values) for values in by_deal.values() if len(values) == 2
    ]
    paired_win_values = [
        1.0 if sum(values) > 0 else 0.0
        for values in by_deal.values()
        if len(values) == 2
    ]
    paired_win_rate = statistics.fmean(paired_win_values) if paired_win_values else 0.0
    paired_win_sd = (
        statistics.pstdev(paired_win_values) if len(paired_win_values) > 1 else 0.0
    )
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

    treatment_counter: Counter = Counter()
    control_counter: Counter = Counter()
    for row in valid:
        treatment_seat = int(row["treatment_seat"])
        for action in row.get("actions") or []:
            if int(action.get("seat", -1)) == treatment_seat:
                treatment_counter[str(action.get("type"))] += 1
            else:
                control_counter[str(action.get("type"))] += 1

    treatment_total_actions = sum(treatment_counter.values())
    control_total_actions = sum(control_counter.values())
    total_pot = sum(int(row["pot"]) for row in valid)
    treatment_pre_river_turns = sum(
        int(row["treatment_pre_river_turns"]) for row in valid
    )
    treatment_pre_river_with_estimate = sum(
        int(row["treatment_pre_river_turns_with_estimate"]) for row in valid
    )
    treatment_river_turns = sum(int(row["treatment_river_turns"]) for row in valid)
    treatment_river_with_estimate = sum(
        int(row["treatment_river_turns_with_estimate"]) for row in valid
    )
    treatment_estimate_calls = sum(
        int(row["treatment_estimate_calls"]) for row in valid
    )

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
        "treatment_total_net": sum(nets),
        "treatment_mean_net": round(mean_net, 3),
        "treatment_mean_bb100": round(mean_net / args.bb * 100.0, 3),
        "treatment_sd_net": round(sd, 3),
        "treatment_ci95_net": round(ci95, 3),
        "treatment_ci95_bb100": round(ci95 / args.bb * 100.0, 3),
        "treatment_win_pct": round(win_rate * 100.0, 3),
        "treatment_win_ci_pp": round(
            paired_win_ci if paired_win_values else win_ci, 3
        ),
        "treatment_win_ci_binomial_pp": round(win_ci, 3),
        "treatment_paired_win_pct": round(paired_win_rate * 100.0, 3),
        "treatment_push_pct": round(pushes / n * 100.0, 3),
        "treatment_loss_pct": round((n - wins - pushes) / n * 100.0, 3),
        "treatment_paired_mean_net": round(paired_mean, 3),
        "treatment_paired_mean_bb100": round(paired_mean / args.bb * 100.0, 3),
        "treatment_paired_ci95_net": round(paired_ci, 3),
        "treatment_paired_ci95_bb100": round(paired_ci / args.bb * 100.0, 3),
        "showdown_pct": round(
            100.0 * sum(1 for row in valid if row.get("reason") == "showdown") / n,
            3,
        ),
        "fold_end_pct": round(
            100.0 * sum(1 for row in valid if row.get("reason") == "fold") / n,
            3,
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
        "avg_actions": round(
            sum(
                int(row["treatment_actions_count"]) + int(row["control_actions_count"])
                for row in valid
            )
            / n,
            3,
        ),
        "avg_hand_sec": round(
            statistics.fmean(float(row["duration_sec"]) for row in valid), 3
        ),
        "treatment_turns": sum(int(row["treatment_turns"]) for row in valid),
        "control_turns": sum(int(row["control_turns"]) for row in valid),
        "treatment_pre_river_turns": treatment_pre_river_turns,
        "treatment_pre_river_turns_with_estimate": treatment_pre_river_with_estimate,
        "treatment_estimate_compliance_pct": round(
            100.0 * treatment_pre_river_with_estimate / treatment_pre_river_turns,
            3,
        )
        if treatment_pre_river_turns
        else 0.0,
        "treatment_river_turns": treatment_river_turns,
        "treatment_river_turns_with_estimate": treatment_river_with_estimate,
        "treatment_estimate_calls": treatment_estimate_calls,
        "treatment_estimate_calls_per_turn": round(
            treatment_estimate_calls / max(1, sum(int(row["treatment_turns"]) for row in valid)),
            3,
        ),
        "treatment_estimate_nudges": sum(
            int(row["treatment_estimate_nudges"]) for row in valid
        ),
        "treatment_random_calls": sum(
            int(row["treatment_random_calls"]) for row in valid
        ),
        "control_estimate_calls": sum(
            int(row["control_estimate_calls"]) for row in valid
        ),
        "control_random_calls": sum(
            int(row["control_random_calls"]) for row in valid
        ),
        "treatment_api_calls": sum(int(row["treatment_api_calls"]) for row in valid),
        "control_api_calls": sum(int(row["control_api_calls"]) for row in valid),
        "treatment_input_tokens": sum(
            int(row["treatment_input_tokens"]) for row in valid
        ),
        "control_input_tokens": sum(
            int(row["control_input_tokens"]) for row in valid
        ),
        "treatment_output_tokens": sum(
            int(row["treatment_output_tokens"]) for row in valid
        ),
        "control_output_tokens": sum(
            int(row["control_output_tokens"]) for row in valid
        ),
        "treatment_reasoning_tokens": sum(
            int(row["treatment_reasoning_tokens"]) for row in valid
        ),
        "control_reasoning_tokens": sum(
            int(row["control_reasoning_tokens"]) for row in valid
        ),
        "api_errors": sum(int(row["api_errors"]) for row in valid),
        "hands_with_api_error": sum(
            1 for row in valid if int(row["api_errors"]) > 0
        ),
        "auto_turns": sum(
            int(row["treatment_auto_turns"]) + int(row["control_auto_turns"])
            for row in valid
        ),
        "treatment_auto_turns": sum(
            int(row["treatment_auto_turns"]) for row in valid
        ),
        "control_auto_turns": sum(int(row["control_auto_turns"]) for row in valid),
        "invalid_action_auto": sum(
            int(row["invalid_action_auto"]) for row in valid
        ),
        "avg_api_latency_ms": round(statistics.fmean(api_latencies), 1)
        if api_latencies
        else 0.0,
        "avg_turn_sec": round(statistics.fmean(turn_latencies), 3)
        if turn_latencies
        else 0.0,
        "treatment_total_actions": treatment_total_actions,
        "control_total_actions": control_total_actions,
        "treatment_fold_pct": _pct(treatment_counter, "fold", treatment_total_actions),
        "treatment_check_pct": _pct(treatment_counter, "check", treatment_total_actions),
        "treatment_call_pct": _pct(treatment_counter, "call", treatment_total_actions),
        "treatment_bet_pct": _pct(treatment_counter, "bet", treatment_total_actions),
        "treatment_raise_pct": _pct(treatment_counter, "raise", treatment_total_actions),
        "treatment_allin_pct": _pct(treatment_counter, "all_in", treatment_total_actions),
        "control_fold_pct": _pct(control_counter, "fold", control_total_actions),
        "control_check_pct": _pct(control_counter, "check", control_total_actions),
        "control_call_pct": _pct(control_counter, "call", control_total_actions),
        "control_bet_pct": _pct(control_counter, "bet", control_total_actions),
        "control_raise_pct": _pct(control_counter, "raise", control_total_actions),
        "control_allin_pct": _pct(control_counter, "all_in", control_total_actions),
        "zero_sum_ok": int(
            all(
                int(row["net_treatment"]) + int(row["net_control"]) == 0
                for row in valid
            )
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
        "# LongCat 2.5 Preview: 工具组 vs 无工具组",
        "",
        f"- 生成时间: {datetime.now(timezone.utc).isoformat()}",
        f"- 模型: `{args.provider}/{args.model}`",
        f"- 手数: {summary.get('hands', 0)}，同牌换座组: {summary.get('paired_deals', 0)}",
        f"- 规则: {args.chips} 筹码，盲注 {args.sb}/{args.bb}，每手重置筹码",
        "- 对照组: 只提供 `apply_action`",
        "- 工具组: 提供 `estimate_hand_probabilities`、`estimate_equity`、`get_random_number`、`call_ev`、`apply_action`",
        "- 工具组要求: 公共牌少于五张时，每次行动前至少估算一次；河牌不要求",
        "",
        "## 结果（工具组视角）",
        "",
        "| 指标 | 数值 |",
        "|---|---:|",
        f"| 工具组总净筹码 | {summary.get('treatment_total_net', 0):+d} |",
        f"| 工具组每手均值 | {summary.get('treatment_mean_net', 0):+.2f} |",
        f"| 工具组 bb/100 | {summary.get('treatment_mean_bb100', 0):+.1f} |",
        f"| 工具组胜率 | {summary.get('treatment_win_pct', 0):.1f}% |",
        f"| 同牌换座 bb/100 | {summary.get('treatment_paired_mean_bb100', 0):+.1f} |",
        f"| 同牌换座 95% CI | {summary.get('treatment_paired_ci95_bb100', 0):+.1f} |",
        f"| 摊牌率 | {summary.get('showdown_pct', 0):.1f}% |",
        f"| 全下手牌占比 | {summary.get('allin_hand_pct', 0):.1f}% |",
        f"| API 错误手数 | {summary.get('hands_with_api_error', 0)} |",
        "",
        "## 工具使用",
        "",
        "| 指标 | 工具组 | 对照组 |",
        "|---|---:|---:|",
        f"| 行动回合数 | {summary.get('treatment_turns', 0)} | {summary.get('control_turns', 0)} |",
        f"| API 调用 | {summary.get('treatment_api_calls', 0)} | {summary.get('control_api_calls', 0)} |",
        f"| 输入 token | {summary.get('treatment_input_tokens', 0)} | {summary.get('control_input_tokens', 0)} |",
        f"| 输出 token | {summary.get('treatment_output_tokens', 0)} | {summary.get('control_output_tokens', 0)} |",
        f"| 推理 token | {summary.get('treatment_reasoning_tokens', 0)} | {summary.get('control_reasoning_tokens', 0)} |",
        f"| estimate 调用 | {summary.get('treatment_estimate_calls', 0)} | {summary.get('control_estimate_calls', 0)} |",
        f"| random 调用 | {summary.get('treatment_random_calls', 0)} | {summary.get('control_random_calls', 0)} |",
        f"| 未估算提醒 | {summary.get('treatment_estimate_nudges', 0)} | 0 |",
        "",
        f"- 工具组河牌前回合估算覆盖率: {summary.get('treatment_estimate_compliance_pct', 0):.2f}% "
        f"({summary.get('treatment_pre_river_turns_with_estimate', 0)}/"
        f"{summary.get('treatment_pre_river_turns', 0)})",
        f"- 工具组河牌回合估算次数: {summary.get('treatment_river_turns_with_estimate', 0)}/"
        f"{summary.get('treatment_river_turns', 0)}（河牌不要求）",
        "",
        "## 行动分布",
        "",
        "| 动作 | 工具组 | 对照组 |",
        "|---|---:|---:|",
        f"| fold | {summary.get('treatment_fold_pct', 0):.1f}% | {summary.get('control_fold_pct', 0):.1f}% |",
        f"| check | {summary.get('treatment_check_pct', 0):.1f}% | {summary.get('control_check_pct', 0):.1f}% |",
        f"| call | {summary.get('treatment_call_pct', 0):.1f}% | {summary.get('control_call_pct', 0):.1f}% |",
        f"| bet | {summary.get('treatment_bet_pct', 0):.1f}% | {summary.get('control_bet_pct', 0):.1f}% |",
        f"| raise | {summary.get('treatment_raise_pct', 0):.1f}% | {summary.get('control_raise_pct', 0):.1f}% |",
        f"| all_in | {summary.get('treatment_allin_pct', 0):.1f}% | {summary.get('control_allin_pct', 0):.1f}% |",
        "",
        "## 说明",
        "",
        "- 每手都是独立牌局，筹码不跨手累积；总净筹码等价于带重购的 100 手总和。",
        "- 同牌换座只对偶数手生效；奇数手会多出一手未配对牌局。",
        "- 100 手样本仍然很小，置信区间很宽，结果只能作为快速对照。",
        "- token 数来自 Provider 返回的 usage；部分流式响应可能由客户端估算。",
        "- `invalid_action_auto` 记录模型连续非法动作或超步数导致的自动跟注或弃牌。",
        "- 工具组的估算要求是软提醒：未调用时系统会退回错误并提醒，但不直接改变动作。",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    tasks = build_tasks(args)
    if args.dry_run:
        for task in tasks:
            print(
                f"{task.index + 1:03d}: deal={task.deal_index + 1:03d} "
                f"treatment seat={task.treatment_seat} seed={task.seed}"
            )
        print(f"tasks={len(tasks)} workers={args.workers}")
        return 0

    registry = ProviderRegistry()
    spec = registry.resolve(args.provider, args.model)
    if not ProviderRegistry.key_present(spec):
        print(
            f"error: missing environment variable {spec.api_key_env} in .env",
            file=sys.stderr,
        )
        return 2

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    output = args.output or (PROJECT_ROOT / "tmp" / "llm_vs_llm" / timestamp)
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    runtime = load_runtime(args)
    params = resolve_params(spec, args)

    print(f"output: {output}")
    print(
        f"LLM: {spec.provider_name} / {spec.name} ({spec.id}), "
        f"thinking={params.get('thinking')}, "
        f"max_tokens={params.get('max_tokens')}"
    )
    print(
        f"experiment: treatment=estimate+random (required pre-river), "
        f"control=apply_action only, hands={args.hands}, workers={args.workers}"
    )
    print(
        f"table: {args.chips} chips, blinds {args.sb}/{args.bb}, "
        f"seed {args.seed}, duplicate={args.duplicate}"
    )

    started = time.perf_counter()
    rows: List[dict] = []
    cumulative = 0
    cumulative_tokens = 0
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
                    "treatment_seat": task.treatment_seat,
                    "fatal_error": f"{type(exc).__name__}: {exc}",
                    "infra_error": 0,
                    "api_errors": 0,
                    "net_treatment": 0,
                    "net_control": 0,
                    "pot": 0,
                    "treatment_turns": 0,
                    "control_turns": 0,
                    "duration_sec": 0.0,
                    "attempts": 0,
                    "events": [],
                    "actions": [],
                }
            rows.append(row)
            if not row.get("fatal_error"):
                cumulative += int(row.get("net_treatment", 0))
            hand_tokens = sum(
                int(row.get(key, 0) or 0)
                for key in (
                    "treatment_input_tokens",
                    "control_input_tokens",
                    "treatment_output_tokens",
                    "control_output_tokens",
                )
            )
            cumulative_tokens += hand_tokens
            if not args.quiet:
                if row.get("fatal_error"):
                    print(
                        f"[{len(rows):03d}/{len(tasks):03d}] {task.label}: "
                        f"FATAL {row['fatal_error']}",
                        flush=True,
                    )
                else:
                    seat = "BTN" if int(row["treatment_seat"]) == 0 else "BB"
                    print(
                        f"[{len(rows):03d}/{len(tasks):03d}] {task.label} "
                        f"treatment={seat}: {int(row['net_treatment']):+5d} chips, "
                        f"pot={int(row['pot']):4d}, "
                        f"turns={int(row['treatment_turns']):2d}/"
                        f"{int(row['control_turns']):2d}, "
                        f"est={int(row['treatment_estimate_calls']):2d}, "
                        f"{float(row['duration_sec']):5.1f}s, "
                        f"tok={hand_tokens:,}, cum={cumulative:+d} chips, "
                        f"{cumulative_tokens:,} tok"
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
    summary["chips"] = args.chips
    summary["small_blind"] = args.sb
    summary["big_blind"] = args.bb
    summary["seed"] = args.seed
    summary["duplicate"] = bool(args.duplicate)
    summary["workers"] = args.workers
    summary["llm_params"] = params
    summary["control_tools"] = list(CONTROL_TOOL_NAMES)
    summary["treatment_tools"] = list(TREATMENT_TOOL_NAMES)
    summary["treatment_estimate_requirement"] = "pre_river_each_turn"

    write_csv(output / "hands.csv", rows)
    write_jsonl(output / "hands.jsonl", rows)
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_report(output / "report.md", summary, args)

    print("")
    print(
        f"result: treatment {summary['treatment_total_net']:+d} chips over "
        f"{summary['hands']} hands, {summary['treatment_mean_bb100']:+.1f} bb/100, "
        f"win {summary['treatment_win_pct']:.1f}%"
    )
    print(
        f"paired: {summary.get('treatment_paired_mean_bb100', 0.0):+.1f} bb/100 "
        f"(95% CI {summary.get('treatment_paired_ci95_bb100', 0.0):+.1f}) over "
        f"{summary.get('paired_deals', 0)} mirrored deals"
    )
    print(
        f"tools: estimate {summary.get('treatment_estimate_calls', 0)} calls, "
        f"pre-river coverage {summary.get('treatment_estimate_compliance_pct', 0):.1f}%, "
        f"nudges {summary.get('treatment_estimate_nudges', 0)}, "
        f"random {summary.get('treatment_random_calls', 0)}"
    )
    print(
        f"api: {summary.get('treatment_api_calls', 0) + summary.get('control_api_calls', 0)} calls, "
        f"{summary.get('treatment_input_tokens', 0) + summary.get('control_input_tokens', 0)} input / "
        f"{summary.get('treatment_output_tokens', 0) + summary.get('control_output_tokens', 0)} output tokens, "
        f"{summary.get('hands_with_api_error', 0)} hands with errors"
    )
    print(f"elapsed: {elapsed:.1f}s")
    print(f"summary: {output / 'summary.json'}")
    print(f"report:  {output / 'report.md'}")
    return 0 if summary["fatal_hands"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
