"""Flask application factory and HTTP/Socket.IO routes."""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import secrets
import time
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory
from flask_socketio import SocketIO

from src.analysis.hand_distribution import HandDistributionAnalyzer
from src.llm.client import LLMClient, LLMError
from src.llm.providers import PROJECT_ROOT, ProviderRegistry
from src.server.session import GameSession

APP_VERSION = "0.1.0"
STATIC_DIR = PROJECT_ROOT / "static"
DIST_DIR = STATIC_DIR / "dist"
GAME_CONFIG_PATH = PROJECT_ROOT / "config" / "game.json"
MODELS_DIR = PROJECT_ROOT / "models" / "rlcard"

logger = logging.getLogger("hu.server")


def create_app() -> tuple[Flask, SocketIO, GameSession]:
    load_dotenv(PROJECT_ROOT / ".env")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    logging.getLogger("engineio").setLevel(logging.WARNING)
    logging.getLogger("socketio").setLevel(logging.WARNING)

    app = Flask(
        __name__,
        static_folder=str(STATIC_DIR),
        static_url_path="/static",
    )
    app.config["SECRET_KEY"] = os.environ.get("HU_SECRET_KEY") or secrets.token_hex(16)
    app.config["JSON_AS_ASCII"] = False
    app.json.ensure_ascii = False
    socketio = SocketIO(
        app,
        async_mode="threading",
        cors_allowed_origins="*",
        logger=False,
        engineio_logger=False,
    )

    registry = ProviderRegistry()
    client = LLMClient(
        registry,
        connect_timeout=float(os.environ.get("HU_CONNECT_TIMEOUT", "20")),
        read_timeout=float(os.environ.get("HU_READ_TIMEOUT", "300")),
    )
    analyzer = HandDistributionAnalyzer()
    session = GameSession(socketio, registry, client, analyzer)

    with GAME_CONFIG_PATH.open("r", encoding="utf-8") as handle:
        game_config = json.load(handle)

    # ------------------------------------------------------------------
    # Pages
    # ------------------------------------------------------------------

    @app.get("/")
    def index():
        index_path = DIST_DIR / "index.html"
        if index_path.exists():
            return send_from_directory(str(DIST_DIR), "index.html")
        return (
            "<html><head><meta charset='utf-8'><title>Heads-Up Lab</title></head>"
            "<body style='background:#0b1512;color:#e8e3d7;font-family:system-ui;padding:48px'>"
            "<h1>Heads-Up Lab</h1><p>前端尚未构建。</p>"
            "<pre style='background:#13211d;padding:16px;border-radius:12px'>"
            "cd frontend\nnpm install\nnpm run build\n</pre>"
            "<p>然后刷新本页。</p></body></html>"
        )

    # ------------------------------------------------------------------
    # Bootstrap
    # ------------------------------------------------------------------

    @app.get("/api/bootstrap")
    def bootstrap():
        return jsonify(
            {
                "app": {
                    "name": "Heads-Up Lab",
                    "version": APP_VERSION,
                    "description": "二人德州扑克 · Human / LLM / RLCard 实验室",
                },
                "providers": registry.list_providers(),
                "game": game_config,
                "rlcard": _rlcard_info(),
                "env": {
                    "god_view_default": bool(game_config.get("defaults", {}).get("god_view", True)),
                },
                "server_time": time.time(),
            }
        )

    # ------------------------------------------------------------------
    # Game
    # ------------------------------------------------------------------

    @app.get("/api/game/state")
    def game_state():
        return jsonify(session.get_state())

    @app.get("/api/game/transcripts")
    def game_transcripts():
        return jsonify(session.get_transcripts())

    @app.post("/api/game/new")
    def game_new():
        payload = request.get_json(silent=True) or {}
        try:
            state = session.start_match(payload)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except Exception as exc:  # noqa: BLE001
            logger.exception("start_match failed")
            return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500
        return jsonify({"ok": True, "state": state})

    @app.post("/api/game/action")
    def game_action():
        payload = request.get_json(silent=True) or {}
        try:
            result = session.human_action(payload)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        if not result.get("ok"):
            return jsonify(result), 400
        return jsonify(result)

    @app.post("/api/game/next-hand")
    def game_next_hand():
        try:
            state = session.next_hand()
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True, "state": state})

    @app.post("/api/game/god-view")
    def game_god_view():
        payload = request.get_json(silent=True) or {}
        state = session.set_god_view(bool(payload.get("enabled", True)))
        return jsonify({"ok": True, "state": state})

    @app.post("/api/game/analyze")
    def game_analyze():
        payload = request.get_json(silent=True) or {}
        try:
            seat = int(payload.get("seat", 0))
            iterations = int(payload.get("iterations", 30000))
            result = session.analyze(seat, iterations)
        except (ValueError, TypeError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        return jsonify({"ok": True, "analysis": result})

    # ------------------------------------------------------------------
    # LLM utilities
    # ------------------------------------------------------------------

    @app.post("/api/llm/test")
    def llm_test():
        payload = request.get_json(silent=True) or {}
        provider_id = str(payload.get("provider_id") or "")
        model_id = str(payload.get("model_id") or "")
        params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
        try:
            result = session.test_llm(provider_id, model_id, params)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except LLMError as exc:
            return jsonify({"ok": False, "error": exc.detail, "kind": exc.kind}), 400
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": f"{type(exc).__name__}: {exc}"}), 500
        return jsonify(result)

    @app.get("/api/health")
    def health():
        return jsonify({"ok": True, "version": APP_VERSION})

    # ------------------------------------------------------------------
    # Socket.IO
    # ------------------------------------------------------------------

    @socketio.on("connect")
    def on_connect():
        socketio.emit("state", session.get_state())

    @socketio.on("request_state")
    def on_request_state():
        socketio.emit("state", session.get_state())

    @socketio.on("request_transcripts")
    def on_request_transcripts():
        socketio.emit("transcripts", session.get_transcripts())

    # ------------------------------------------------------------------
    # Errors
    # ------------------------------------------------------------------

    @app.errorhandler(404)
    def not_found(error):
        if request.path.startswith("/api/") or request.path.startswith("/static/"):
            return jsonify({"ok": False, "error": "Not found"}), 404
        return index()

    @app.errorhandler(500)
    def server_error(error):
        return jsonify({"ok": False, "error": "Internal server error"}), 500

    logger.info("Heads-Up Lab ready. Static dist: %s", DIST_DIR)
    return app, socketio, session


def _rlcard_info() -> dict:
    available = importlib.util.find_spec("rlcard") is not None
    models = []
    if MODELS_DIR.exists():
        for path in sorted(MODELS_DIR.glob("*")):
            if path.suffix.lower() in (".pth", ".tar", ".pt"):
                models.append(
                    {
                        "name": path.name,
                        "path": str(path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
                        "size_kb": round(path.stat().st_size / 1024, 1),
                    }
                )
    return {
        "available": available,
        "agents": [
            {
                "id": "random",
                "name": "RandomAgent",
                "description": "RLCard 官方随机策略，无需模型文件，适合验证链路。",
                "needs_model": False,
            },
            {
                "id": "dqn",
                "name": "DQN",
                "description": "加载 RLCard DQN checkpoint。",
                "needs_model": True,
            },
            {
                "id": "nfsp",
                "name": "NFSP",
                "description": "加载 RLCard NFSP checkpoint。",
                "needs_model": True,
            },
            {
                "id": "dmc",
                "name": "DMC",
                "description": "加载 RLCard Deep Monte-Carlo checkpoint。",
                "needs_model": True,
            },
        ],
        "models": models,
        "model_dir": str(MODELS_DIR.relative_to(PROJECT_ROOT)).replace("\\", "/"),
    }


__all__ = ["create_app"]
