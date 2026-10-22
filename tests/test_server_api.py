"""REST robustness tests using Flask's test client."""

from __future__ import annotations

from src.server.app import create_app


def make_client():
    app, _socketio, session = create_app()
    return app.test_client(), session


def test_non_object_json_is_rejected_with_400() -> None:
    client, session = make_client()
    try:
        for path in (
            "/api/game/new",
            "/api/game/action",
            "/api/game/god-view",
            "/api/game/analyze",
            "/api/llm/test",
        ):
            response = client.post(path, json=[1, 2, 3])
            assert response.status_code == 400, path
            assert response.get_json()["ok"] is False
    finally:
        session.shutdown()


def test_malformed_json_is_rejected_without_internal_text() -> None:
    client, session = make_client()
    try:
        response = client.post(
            "/api/game/new",
            data="{",
            content_type="application/json",
        )
        assert response.status_code == 400
        body = response.get_json()
        assert "AttributeError" not in body["error"]
        assert "Traceback" not in body["error"]
    finally:
        session.shutdown()


def test_god_view_accepts_only_explicit_booleans() -> None:
    client, session = make_client()
    try:
        response = client.post("/api/game/god-view", json={"enabled": "false"})
        assert response.status_code == 200
        assert session.god_view is False

        response = client.post("/api/game/god-view", json={"enabled": "maybe"})
        assert response.status_code == 400
    finally:
        session.shutdown()


def test_table_talk_requires_boolean_and_two_llm_seats() -> None:
    client, session = make_client()
    try:
        payload = {
            "seats": [
                {"kind": "human", "name": "You"},
                {"kind": "bot", "name": "Bot", "bot": {"agent_type": "random"}},
            ],
            "rules": {"starting_chips": 1000},
            "table_talk": "yes",
        }
        response = client.post("/api/game/new", json=payload)
        assert response.status_code == 400
        assert "table_talk" in response.get_json()["error"]

        payload["table_talk"] = True
        response = client.post("/api/game/new", json=payload)
        assert response.status_code == 200
        assert session.table_talk_enabled is False
        assert response.get_json()["state"]["match"]["table_talk"] is False
    finally:
        session.shutdown()


def test_invalid_rules_return_400() -> None:
    client, session = make_client()
    try:
        payload = {
            "seats": [
                {"kind": "human", "name": "You"},
                {"kind": "bot", "name": "Bot", "bot": {"agent_type": "random"}},
            ],
            "rules": {"starting_chips": 50},
        }
        response = client.post("/api/game/new", json=payload)
        assert response.status_code == 400
        assert "starting_chips" in response.get_json()["error"]
    finally:
        session.shutdown()
