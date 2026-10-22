"""In-memory match orchestrator for the heads-up table."""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from src.agents.base import AgentView
from src.agents.project_bot import ProjectBot
from src.agents.rlcard_bot import RLCardBot
from src.analysis.equity import EquityAnalyzer
from src.analysis.hand_distribution import HandDistributionAnalyzer
from src.engine.actions import Action, ActionType
from src.engine.heads_up import HeadsUpGame, Street
from src.llm.agent import LLMAgent
from src.llm.client import LLMClient
from src.llm.providers import ProviderRegistry

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GAME_CONFIG_PATH = PROJECT_ROOT / "config" / "game.json"

BOT_DELAY_DEFAULT = 3.0
BOT_DELAY_MIN = 0.0
BOT_DELAY_MAX = 10.0


@dataclass
class SeatConfig:
    kind: str
    name: str
    provider_id: str = ""
    model_id: str = ""
    params: dict = None  # type: ignore[assignment]
    bot_agent_type: str = "random"
    bot_model_path: Optional[str] = None
    bot_delay_seconds: float = BOT_DELAY_DEFAULT

    def __post_init__(self) -> None:
        if self.params is None:
            self.params = {}


class GameSession:
    """Owns the engine, agents, transcript buffers and the game thread."""

    def __init__(
        self,
        socketio,
        registry: ProviderRegistry,
        client: LLMClient,
        analyzer: HandDistributionAnalyzer,
        equity_analyzer: Optional[EquityAnalyzer] = None,
    ) -> None:
        self.socketio = socketio
        self.registry = registry
        self.client = client
        self.analyzer = analyzer
        self.equity_analyzer = equity_analyzer or EquityAnalyzer()
        self.lock = threading.RLock()
        self._start_lock = threading.Lock()
        self.game: Optional[HeadsUpGame] = None
        self.agents: Dict[int, object] = {}
        self.seat_configs: Dict[int, SeatConfig] = {}
        self.match_id = ""
        self.god_view = True
        self.max_hands = 0
        self.hand_history: List[dict] = []
        self.transcripts: Dict[int, List[dict]] = {0: [], 1: []}
        self.event_seq = 0
        self.state_seq = 0
        self.match_error = ""
        self.started_at = 0.0
        self._generation = 0
        self._stop_event = threading.Event()
        self._human_event = threading.Event()
        self._next_hand_event = threading.Event()
        self._human_token: Optional[tuple] = None
        self._thread: Optional[threading.Thread] = None
        self._last_result_hand_id = -1
        self._game_config = self._load_game_config()

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    @staticmethod
    def _load_game_config() -> dict:
        with GAME_CONFIG_PATH.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def _normalize_rules(self, raw: Optional[dict]) -> dict:
        defaults = dict(self._game_config.get("defaults", {}))
        limits = self._game_config.get("limits", {})
        raw = raw or {}
        rules = dict(defaults)
        rules.update({key: value for key, value in raw.items() if value is not None})

        def int_value(key: str, default: int) -> int:
            value = rules.get(key, default)
            if isinstance(value, bool):
                raise ValueError(f"{key} 必须是整数")
            if isinstance(value, float) and not value.is_integer():
                raise ValueError(f"{key} 必须是整数")
            try:
                return int(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{key} 必须是整数") from exc

        for key, default in (
            ("starting_chips", 1000),
            ("small_blind", 5),
            ("big_blind", 10),
            ("rebuy_amount", 1000),
        ):
            limit = limits.get(key, {})
            minimum = int(limit.get("min", 1))
            maximum = int(limit.get("max", 100000))
            value = int_value(key, default)
            if not minimum <= value <= maximum:
                raise ValueError(f"{key} 必须在 {minimum} 到 {maximum} 之间")
            rules[key] = value

        max_hands_limit = int(limits.get("max_hands", {}).get("max", 1000))
        max_hands = int_value("max_hands", 0)
        if not 0 <= max_hands <= max_hands_limit:
            raise ValueError(f"max_hands 必须在 0 到 {max_hands_limit} 之间")
        rules["max_hands"] = max_hands

        def bool_value(key: str, default: bool) -> bool:
            value = rules.get(key, default)
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                lowered = value.strip().lower()
                if lowered in ("true", "1", "yes", "on"):
                    return True
                if lowered in ("false", "0", "no", "off"):
                    return False
            raise ValueError(f"{key} 必须是布尔值")

        rules["auto_rebuy"] = bool_value("auto_rebuy", False)
        rules["god_view"] = bool_value("god_view", True)
        seed = rules.get("seed")
        if seed in ("", None):
            rules["seed"] = None
        else:
            if isinstance(seed, bool) or (isinstance(seed, float) and not seed.is_integer()):
                raise ValueError("seed 必须是整数或留空")
            try:
                rules["seed"] = int(seed)
            except (TypeError, ValueError) as exc:
                raise ValueError("seed 必须是整数或留空") from exc
        if rules["big_blind"] < rules["small_blind"]:
            raise ValueError("大盲必须不小于小盲")
        if rules["starting_chips"] < rules["big_blind"]:
            raise ValueError("起始筹码不能低于大盲")
        return rules

    def _parse_seats(self, payload: dict) -> List[SeatConfig]:
        seats_raw = payload.get("seats")
        if not isinstance(seats_raw, list) or len(seats_raw) != 2:
            raise ValueError("必须提供两个座位")
        seats: List[SeatConfig] = []
        for index, raw in enumerate(seats_raw):
            if not isinstance(raw, dict):
                raise ValueError(f"座位 {index} 配置无效")
            kind = str(raw.get("kind") or "human").lower()
            if kind not in ("human", "llm", "bot"):
                raise ValueError(f"座位 {index} 的类型必须是 human / llm / bot")
            name = str(raw.get("name") or "").strip()
            if kind == "human":
                name = name or "你"
            elif kind == "llm":
                provider_id = str(raw.get("provider_id") or "").strip()
                model_id = str(raw.get("model_id") or "").strip()
                if not provider_id or not model_id:
                    raise ValueError(f"座位 {index} 的 LLM 需要 provider_id 和 model_id")
                spec = self.registry.resolve(provider_id, model_id)
                name = name or f"{spec.provider_name} · {spec.name}"
                seats.append(
                    SeatConfig(
                        kind="llm",
                        name=name,
                        provider_id=provider_id,
                        model_id=model_id,
                        params=dict(raw.get("params") or {}),
                    )
                )
                continue
            elif kind == "bot":
                bot_raw = raw.get("bot") if isinstance(raw.get("bot"), dict) else {}
                agent_type = str(bot_raw.get("agent_type") or "random").lower()
                if agent_type not in (
                    "random",
                    "dqn",
                    "nfsp",
                    "dmc",
                    "project_dqn",
                    "project_nfsp",
                ):
                    raise ValueError(f"座位 {index} 的 Bot 类型无效: {agent_type}")
                model_path = bot_raw.get("model_path")
                if agent_type != "random" and not model_path:
                    raise ValueError(f"座位 {index} 的 {agent_type} 需要模型文件")
                delay_raw = bot_raw.get("delay_seconds", BOT_DELAY_DEFAULT)
                if isinstance(delay_raw, bool):
                    raise ValueError(f"座位 {index} 的 Bot 延迟必须是数字")
                try:
                    delay_seconds = float(delay_raw)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"座位 {index} 的 Bot 延迟必须是数字") from exc
                if not BOT_DELAY_MIN <= delay_seconds <= BOT_DELAY_MAX:
                    raise ValueError(
                        f"座位 {index} 的 Bot 延迟必须在 {BOT_DELAY_MIN:g} 到 {BOT_DELAY_MAX:g} 秒之间"
                    )
                name = name or ("Project Bot" if agent_type.startswith("project_") else "RLCard Bot")
                seats.append(
                    SeatConfig(
                        kind="bot",
                        name=name,
                        bot_agent_type=agent_type,
                        bot_model_path=str(model_path) if model_path else None,
                        bot_delay_seconds=delay_seconds,
                    )
                )
                continue
            seats.append(SeatConfig(kind="human", name=name))
        if sum(1 for seat in seats if seat.kind == "human") >= 2:
            raise ValueError("不能同时选择两个人类玩家")
        if seats[0].name == seats[1].name:
            seats[1].name = seats[1].name + " B"
        return seats

    # ------------------------------------------------------------------
    # Match lifecycle
    # ------------------------------------------------------------------

    def start_match(self, payload: dict) -> dict:
        # Serialize match starts so two requests cannot race on the same state.
        with self._start_lock:
            seats = self._parse_seats(payload)
            rules = self._normalize_rules(
                payload.get("rules") if isinstance(payload.get("rules"), dict) else {}
            )

            # Validate LLM keys before building anything.
            for seat in seats:
                if seat.kind == "llm":
                    spec = self.registry.resolve(seat.provider_id, seat.model_id)
                    if not ProviderRegistry.key_present(spec):
                        raise ValueError(
                            f"{spec.provider_name} 缺少环境变量 {spec.api_key_env}，请在 .env 中配置后重启服务"
                        )

            # Build the new game and all agents in local variables. A failure
            # here leaves the running match untouched.
            new_game = HeadsUpGame(
                starting_chips=rules["starting_chips"],
                small_blind=rules["small_blind"],
                big_blind=rules["big_blind"],
                auto_rebuy=rules["auto_rebuy"],
                rebuy_amount=rules["rebuy_amount"],
                seed=rules["seed"],
                max_hands=rules["max_hands"],
            )
            new_game.configure_players(
                {"name": seats[0].name, "kind": seats[0].kind},
                {"name": seats[1].name, "kind": seats[1].kind},
            )
            base_seed = rules.get("seed")
            if base_seed is None:
                base_seed = int(time.time())
            generation_holder = {"value": 0}
            new_agents: Dict[int, object] = {}
            for seat_index, seat in enumerate(seats):
                if seat.kind == "llm":
                    spec = self.registry.resolve(seat.provider_id, seat.model_id)
                    new_agents[seat_index] = LLMAgent(
                        seat=seat_index,
                        name=seat.name,
                        spec=spec,
                        params=seat.params,
                        client=self.client,
                        analyzer=self.analyzer,
                        equity_analyzer=self.equity_analyzer,
                        validate_action=self._validator_for(generation_holder),
                        runtime=self._game_config.get("llm_runtime", {}),
                    )
                elif seat.kind == "bot":
                    if str(seat.bot_agent_type).startswith("project_"):
                        algorithm = str(seat.bot_agent_type).split("_", 1)[1]
                        model_path = seat.bot_model_path
                        try:
                            new_agents[seat_index] = ProjectBot(
                                name=seat.name,
                                model_path=model_path or "",
                                algorithm=algorithm,
                            )
                        except (FileNotFoundError, ValueError, RuntimeError) as exc:
                            raise ValueError(
                                f"座位 {seat_index} 的 Project Bot 加载失败: {exc}"
                            ) from exc
                    else:
                        bot = RLCardBot(
                            name=seat.name,
                            agent_type=seat.bot_agent_type,
                            model_path=seat.bot_model_path,
                            seed=int(base_seed) + seat_index * 97,
                        )
                        if not bot.describe().get("available"):
                            raise ValueError(
                                f"座位 {seat_index} 的 RLCard Bot 加载失败: "
                                f"{bot.describe().get('warning')}"
                            )
                        new_agents[seat_index] = bot

            new_match_id = uuid.uuid4().hex[:12]
            with self.lock:
                self._stop_current_thread()
                generation = self._generation
                generation_holder["value"] = generation
                self.match_id = new_match_id
                self.god_view = rules["god_view"]
                self.max_hands = rules["max_hands"]
                self.hand_history = []
                self.transcripts = {0: [], 1: []}
                self.event_seq = 0
                self.state_seq = 0
                self.match_error = ""
                self.started_at = time.time()
                self._last_result_hand_id = -1
                self._human_token = None
                self._human_action = None
                self._human_event.clear()
                self._next_hand_event.clear()
                self.game = new_game
                self.seat_configs = {0: seats[0], 1: seats[1]}
                self._stop_event.clear()
                self.agents = new_agents
                self._thread = threading.Thread(target=self._run, args=(generation,), daemon=True)
                self._thread.start()

        # Give the thread a moment to deal the first hand.
        for _ in range(120):
            with self.lock:
                if self.game and self.game.hand_id > 0:
                    break
            time.sleep(0.025)
        return self.get_state()

    def _validator_for(self, generation_holder: dict):
        def validate(action: Action) -> Optional[str]:
            return self._validate_for_generation(action, int(generation_holder["value"]))

        return validate

    def _stop_current_thread(self) -> None:
        with self.lock:
            for agent in self.agents.values():
                cancel = getattr(agent, "cancel", None)
                if callable(cancel):
                    try:
                        cancel()
                    except Exception:  # noqa: BLE001
                        pass
            self._generation += 1
            self._stop_event.set()
            self._human_event.set()
            self._next_hand_event.set()
        # Do not join: an LLM request may still be in flight. The old thread
        # checks the generation before touching the new match.

    def shutdown(self) -> None:
        self._stop_current_thread()
        close = getattr(self.client, "close", None)
        if callable(close):
            try:
                close()
            except Exception:  # noqa: BLE001
                pass

    def next_hand(self) -> dict:
        with self.lock:
            if not self.game:
                raise ValueError("还没有开始比赛")
            if self.match_error or not self._thread_alive_locked():
                raise ValueError("对局线程已停止，请开始新比赛")
            if self.game.street != Street.FINISHED:
                raise ValueError("当前手牌还没有结束")
            if self.game.match_over:
                raise ValueError("比赛已经结束，请开始新比赛")
            self._next_hand_event.set()
        return self.get_state()

    def set_god_view(self, enabled: bool) -> dict:
        with self.lock:
            self.god_view = bool(enabled)
            self._broadcast_state()
        return self.get_state()

    def human_action(self, payload: dict) -> dict:
        with self.lock:
            if not self.game:
                raise ValueError("还没有开始比赛")
            if self.match_error or not self._thread_alive_locked():
                raise ValueError("对局线程已停止，请开始新比赛")
            actor = self.game.actor
            if actor is None:
                raise ValueError("当前没有轮到人类行动")
            player = self.game.players[actor]
            if player.kind != "human":
                raise ValueError("当前不是人类玩家的回合")
            token = (self.game.hand_id, len(self.game.actions), actor)
            if self._human_token != token:
                raise ValueError("牌局状态已变化，请刷新后重试")
            action = Action.from_payload(actor, payload, source="human")
            result = self.game.apply(action)
            if not result.ok:
                return {"ok": False, "error": result.error, "state": self._snapshot_locked()}
            self._human_token = None
            self._human_event.set()
            snapshot = self._emit_state_locked()
            if self.game.street == Street.FINISHED:
                self._finish_hand_locked()
            return {"ok": True, "state": snapshot}

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def _run(self, generation: int) -> None:
        try:
            with self.lock:
                if not self._alive(generation):
                    return
                self._next_hand_event.clear()
                self.game.start_hand()
                self._broadcast_state()
                if self.game.street == Street.FINISHED:
                    self._finish_hand_locked()
            while self._alive(generation):
                with self.lock:
                    if not self._alive(generation) or not self.game:
                        return
                    finished = self.game.street == Street.FINISHED
                    match_over = self.game.match_over
                    actor = self.game.actor
                    kind = self.game.players[actor].kind if actor is not None else None
                if finished:
                    if match_over:
                        self._emit(
                            {
                                "type": "match_over",
                                "match_id": self.match_id,
                                "text": "比赛结束。",
                            }
                        )
                        return
                    while self._alive(generation) and not self._next_hand_event.wait(0.4):
                        pass
                    if not self._alive(generation):
                        return
                    with self.lock:
                        if not self.game or self.game.street != Street.FINISHED:
                            continue
                        self._next_hand_event.clear()
                        try:
                            self.game.start_hand()
                        except Exception as exc:  # noqa: BLE001
                            self.match_error = f"无法开始下一手：{exc}"
                            self.game.match_over = True
                            self._broadcast_state()
                            return
                        self._broadcast_state()
                        if self.game.street == Street.FINISHED:
                            self._finish_hand_locked()
                    continue

                if actor is None:
                    time.sleep(0.02)
                    continue
                if kind == "human":
                    self._wait_for_human(generation, actor)
                else:
                    self._run_agent_turn(generation, actor)
        except Exception as exc:  # noqa: BLE001
            self.match_error = f"{type(exc).__name__}: {exc}"
            try:
                self._emit(
                    {
                        "type": "error",
                        "match_id": self.match_id,
                        "kind": "server",
                        "text": self.match_error,
                    }
                )
                with self.lock:
                    self._broadcast_state()
            except Exception:
                pass

    def _wait_bot_delay(self, generation: int, seconds: float) -> bool:
        """Pause before a bot acts; returns False when the match was reset."""
        deadline = time.monotonic() + seconds
        while self._alive(generation):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return True
            self._stop_event.wait(min(0.2, remaining))
        return False

    def _run_agent_turn(self, generation: int, seat: int) -> None:
        with self.lock:
            if not self._alive(generation) or not self.game:
                return
            view = self._build_view(seat)
            agent = self.agents.get(seat)
            if agent is None:
                action = self._safe_action(seat)
            else:
                action = None
            config = self.seat_configs.get(seat)
            delay = config.bot_delay_seconds if config and config.kind == "bot" else 0.0
            token = (self.game.hand_id, len(self.game.actions), seat)
        if delay > 0 and not self._wait_bot_delay(generation, delay):
            return
        if agent is not None:
            emit = self._make_emit(generation, seat)
            try:
                action = agent.decide(view, emit)
            except Exception as exc:  # noqa: BLE001
                emit(
                    {
                        "type": "error",
                        "kind": "agent",
                        "text": f"{type(exc).__name__}: {exc}",
                    }
                )
                action = None

        with self.lock:
            if not self._alive(generation) or not self.game:
                return
            if token != (self.game.hand_id, len(self.game.actions), self.game.actor):
                return
            if action is None:
                action = self._safe_action(seat, source="system")
            result = self.game.apply(action)
            if not result.ok:
                self._emit(
                    {
                        "type": "error",
                        "kind": "engine",
                        "match_id": self.match_id,
                        "text": f"Agent 动作被拒绝：{result.error}",
                    }
                )
                fallback = self._safe_action(seat, source="system")
                result = self.game.apply(fallback)
                if not result.ok:
                    self.match_error = f"无法执行兜底动作：{result.error}"
            self._broadcast_state()
            if self.game.street == Street.FINISHED:
                self._finish_hand_locked()

    def _wait_for_human(self, generation: int, seat: int) -> None:
        with self.lock:
            if not self._alive(generation) or not self.game:
                return
            self._human_token = (self.game.hand_id, len(self.game.actions), seat)
            self._human_event.clear()
            snapshot = self._snapshot_locked()
        self.socketio.emit("state", snapshot)
        while self._alive(generation):
            if self._human_event.wait(0.4):
                return

    # ------------------------------------------------------------------
    # State / views
    # ------------------------------------------------------------------

    def _snapshot_locked(self) -> dict:
        if not self.game:
            return {"has_match": False, "match": None}
        reveal_all = self.god_view
        human_seats = self.game.human_seats
        snapshot = self.game.snapshot(reveal_all=reveal_all, reveal_seats=human_seats)
        snapshot["has_match"] = True
        snapshot["match"] = {
            "id": self.match_id,
            "hands_played": self.game.hands_played,
            "max_hands": self.game.max_hands,
            "match_over": self.game.match_over,
            "god_view": self.god_view,
            "can_next_hand": self.game.street == Street.FINISHED and not self.game.match_over,
            "started_at": self.started_at,
            "error": self.match_error,
            "rules": {
                "starting_chips": self.game.starting_chips,
                "small_blind": self.game.small_blind,
                "big_blind": self.game.big_blind,
                "auto_rebuy": self.game.auto_rebuy,
                "rebuy_amount": self.game.rebuy_amount,
                "max_hands": self.game.max_hands,
                "seed": self.game.seed,
            },
            "seats": [self._seat_public(seat) for seat in (0, 1)],
            "history": [self._history_item(item) for item in self.hand_history[-12:]],
            "last_result": self.game.result,
        }
        snapshot["seq"] = self.state_seq
        snapshot["server_time"] = time.time()
        return snapshot

    def _seat_public(self, seat: int) -> dict:
        config = self.seat_configs.get(seat)
        player = self.game.players[seat] if self.game else None
        agent = self.agents.get(seat)
        info = {
            "seat": seat,
            "name": config.name if config else (player.name if player else f"Seat {seat}"),
            "kind": config.kind if config else (player.kind if player else "human"),
            "is_human": (config.kind if config else "") == "human",
            "provider_id": config.provider_id if config else "",
            "model_id": config.model_id if config else "",
            "model_name": "",
            "agent": {},
        }
        if config and config.kind == "llm":
            try:
                spec = self.registry.resolve(config.provider_id, config.model_id)
                info["model_name"] = spec.name
                info["provider_name"] = spec.provider_name
                info["icon"] = spec.provider_icon
                info["accent"] = spec.provider_accent
            except ValueError:
                pass
        if agent is not None and hasattr(agent, "describe"):
            info["agent"] = agent.describe()
        return info

    def _build_view(self, seat: int) -> AgentView:
        assert self.game is not None
        player = self.game.players[seat]
        opponent = self.game.players[1 - seat]
        constraints = self.game.action_constraints(seat)
        return AgentView(
            seat=seat,
            name=player.name,
            hand_id=self.game.hand_id,
            street=self.game.street.value,
            hole_cards=[card.code for card in player.hole_cards],
            community=[card.code for card in self.game.community],
            pot=self.game.pot,
            my_chips=player.chips,
            opponent_chips=opponent.chips,
            my_street_bet=player.street_bet,
            opponent_street_bet=opponent.street_bet,
            my_total_bet=player.total_bet,
            opponent_total_bet=opponent.total_bet,
            current_bet=self.game.current_bet,
            to_call=max(0, constraints.get("to_call", 0)),
            call_amount=int(constraints.get("call_amount", 0)),
            min_raise_to=int(constraints.get("min_raise_to", self.game.big_blind)),
            max_raise_to=int(constraints.get("max_raise_to", player.chips)),
            legal_actions=[action.value for action in self.game.legal_actions(seat)],
            action_history=list(self.game.actions[-12:]),
            opponent_last_action=opponent.last_action,
            button=self.game.button_index,
            small_blind=self.game.small_blind,
            big_blind=self.game.big_blind,
            hand_number=self.game.hand_id,
            match_note=self._match_note(seat),
            history_summary=[self._llm_history_item(item, seat) for item in self.hand_history[-3:]],
        )

    def _match_note(self, seat: int) -> str:
        assert self.game is not None
        total = len(self.hand_history)
        return (
            f"这是本次比赛的第 {self.game.hand_id} 手，已完成 {total} 手。"
            f"盲注 {self.game.small_blind}/{self.game.big_blind}，起始筹码 {self.game.starting_chips}。"
            f"你当前 {self.game.players[seat].chips}，对手 {self.game.players[1 - seat].chips}。"
        )

    def _history_item(self, item: dict) -> dict:
        return {
            "hand_id": item.get("hand_id"),
            "reason": item.get("reason"),
            "winner_seats": item.get("winner_seats", []),
            "pot": item.get("pot", 0),
            "board": item.get("board", []),
            "players": item.get("players", []),
        }

    def _llm_history_item(self, item: dict, seat: int) -> dict:
        players = {entry.get("seat"): entry for entry in item.get("players", [])}
        mine = players.get(seat, {})
        return {
            "hand_id": item.get("hand_id"),
            "you_won": seat in (item.get("winner_seats") or []),
            "reason": item.get("reason"),
            "pot": item.get("pot", 0),
            "board": item.get("board", []),
            "your_net": mine.get("net", 0),
            "your_chips_after": mine.get("chips_after", 0),
        }

    def _safe_action(self, seat: int, source: str = "system") -> Action:
        assert self.game is not None
        legal = set(self.game.legal_actions(seat))
        if ActionType.CALL.value in legal:
            return Action(seat, ActionType.CALL, source=source, auto=True)
        if ActionType.CHECK.value in legal:
            return Action(seat, ActionType.CHECK, source=source, auto=True)
        return Action(seat, ActionType.FOLD, source=source, auto=True)

    def _validate_for_generation(self, action: Action, generation: int) -> Optional[str]:
        with self.lock:
            if not self._alive(generation) or not self.game:
                return "牌局已结束或被重置"
            return self.game.preview(action)

    # ------------------------------------------------------------------
    # Events / transcripts
    # ------------------------------------------------------------------

    def _alive(self, generation: int) -> bool:
        return generation == self._generation and not self._stop_event.is_set()

    def _thread_alive_locked(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _sync_human_token_locked(self) -> None:
        """Keep the action token in step with the state that was broadcast."""
        if not self.game or self.game.actor is None:
            self._human_token = None
            return
        actor = self.game.actor
        if self.game.players[actor].kind != "human":
            self._human_token = None
            return
        if self._human_token is None:
            self._human_token = (self.game.hand_id, len(self.game.actions), actor)

    def _make_emit(self, generation: int, seat: int):
        def emit(event: dict) -> None:
            if not self._alive(generation):
                return
            payload = dict(event)
            payload["match_id"] = self.match_id
            payload["seat"] = seat
            with self.lock:
                self.event_seq += 1
                payload["event_id"] = self.event_seq
                bucket = self.transcripts.setdefault(seat, [])
                bucket.append(payload)
                if len(bucket) > 6000:
                    del bucket[:1000]
            self.socketio.emit("agent_event", payload)

        return emit

    def _emit(self, event: dict) -> None:
        payload = dict(event)
        payload.setdefault("match_id", self.match_id)
        with self.lock:
            self.event_seq += 1
            payload.setdefault("event_id", self.event_seq)
            seat = payload.get("seat", 0)
            try:
                seat = int(seat)
            except (TypeError, ValueError):
                seat = 0
            bucket = self.transcripts.setdefault(seat, [])
            stored = dict(payload)
            stored["seat"] = seat
            bucket.append(stored)
            if len(bucket) > 6000:
                del bucket[:1000]
        self.socketio.emit("agent_event", payload)

    def _emit_state_locked(self) -> dict:
        """Increment the state version, snapshot and emit while holding the lock."""
        self._sync_human_token_locked()
        self.state_seq += 1
        snapshot = self._snapshot_locked()
        self.socketio.emit("state", snapshot)
        return snapshot

    def _broadcast_state(self) -> None:
        with self.lock:
            self._emit_state_locked()

    def _finish_hand_locked(self) -> None:
        if not self.game or not self.game.result:
            return
        if self._last_result_hand_id == self.game.hand_id:
            return
        self._last_result_hand_id = self.game.hand_id
        result = self.game.result
        self.hand_history.append(result)
        for agent in self.agents.values():
            if isinstance(agent, LLMAgent):
                try:
                    agent.on_hand_finished(result)
                except Exception:
                    pass
        self._emit(
            {
                "type": "hand_result",
                "hand_id": self.game.hand_id,
                "result": result,
                "match_over": self.game.match_over,
            }
        )
        self._broadcast_state()

    # ------------------------------------------------------------------
    # REST helpers
    # ------------------------------------------------------------------

    def get_state(self) -> dict:
        with self.lock:
            snapshot = self._snapshot_locked()
            snapshot["transcript_counts"] = {
                str(seat): len(events) for seat, events in self.transcripts.items()
            }
            return snapshot

    def get_transcripts(self) -> dict:
        with self.lock:
            return {
                "match_id": self.match_id,
                "seats": {str(seat): list(events) for seat, events in self.transcripts.items()},
            }

    def analyze(self, seat: int, iterations: int = 30000) -> dict:
        with self.lock:
            if not self.game:
                raise ValueError("还没有开始比赛")
            if seat not in (0, 1):
                raise ValueError("seat 必须是 0 或 1")
            player = self.game.players[seat]
            if player.kind != "human" and not self.god_view:
                raise ValueError("需要开启上帝视角才能分析该玩家")
            if len(player.hole_cards) != 2:
                raise ValueError("当前没有可分析的底牌")
            hole = list(player.hole_cards)
            community = list(self.game.community)
            hand_id = self.game.hand_id
            street = self.game.street.value
        result = self.analyzer.analyze(hole, community, hero_seat=seat, iterations=iterations)
        payload = result.to_dict()
        payload["hand_id"] = hand_id
        payload["street"] = street
        payload["seat"] = seat
        return payload

    def test_llm(self, provider_id: str, model_id: str, params: Optional[dict]) -> dict:
        spec = self.registry.resolve(provider_id, model_id)
        if not ProviderRegistry.key_present(spec):
            raise ValueError(f"缺少环境变量 {spec.api_key_env}")
        normalized = spec.normalize_params(params or {})
        test_params = dict(normalized)
        test_params["max_tokens"] = min(int(test_params.get("max_tokens", 1024)), 2048)
        messages = [
            {"role": "system", "content": "你是一个连通性测试助手。"},
            {"role": "user", "content": "请只回复四个字：连接成功"},
        ]
        runtime = self._game_config.get("llm_runtime", {})
        test_client = LLMClient(
            self.registry,
            connect_timeout=min(10.0, float(runtime.get("connect_timeout_seconds", 20))),
            read_timeout=min(60.0, float(runtime.get("request_timeout_seconds", 300))),
        )
        try:
            result = test_client.stream(spec, messages, None, test_params, lambda event: None)
        finally:
            test_client.close()
        return {
            "ok": True,
            "provider_id": provider_id,
            "model_id": model_id,
            "text": (result.content or result.reasoning or "")[:300],
            "latency_ms": round(result.latency_ms, 1),
            "usage": result.usage,
            "estimated_usage": result.estimated_usage,
        }


__all__ = ["GameSession"]
