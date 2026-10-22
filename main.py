"""Entry point for the Heads-Up Lab server."""

from __future__ import annotations

import argparse
import os
import sys

from src.server.app import APP_VERSION, create_app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Heads-Up Lab — 二人德州扑克实验室")
    parser.add_argument("--host", default=os.environ.get("HU_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("HU_PORT", "5050")))
    parser.add_argument("--debug", action="store_true", help="启用 Flask 调试模式")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    app, socketio, session = create_app()
    print(
        "\n"
        "  HEADS-UP LAB · 二人德州扑克实验室\n"
        "  Human / LLM / RLCard\n"
        f"  http://{args.host}:{args.port}\n"
        f"  version {APP_VERSION}\n"
    )
    try:
        socketio.run(
            app,
            host=args.host,
            port=args.port,
            debug=args.debug,
            allow_unsafe_werkzeug=True,
            use_reloader=args.debug,
        )
    except KeyboardInterrupt:
        pass
    finally:
        session.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
