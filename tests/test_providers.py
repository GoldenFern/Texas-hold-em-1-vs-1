"""Provider registry regression tests for newly added LLM vendors."""

from __future__ import annotations

from src.llm.providers import ProviderRegistry


def test_new_models_are_registered() -> None:
    registry = ProviderRegistry()

    mimo = registry.resolve("mimo", "mimo-v2.6-flash")
    gpt = registry.resolve("4router", "gpt-6-luna")

    assert mimo.api_key_env == "MIMO_API_KEY"
    assert gpt.api_key_env == "FOURROUTER_API_KEY"
    assert registry.endpoint(mimo) == "https://api.xiaomimimo.com/v1/chat/completions"
    assert registry.endpoint(gpt) == "https://4router.net/v1/chat/completions"
    assert gpt.provider_icon == "4router.svg"
    assert gpt.model_icon == "openai.svg"
    assert gpt.public_dict(True)["icon"] == "openai.svg"


def test_mimo_payload_uses_thinking_toggle() -> None:
    registry = ProviderRegistry()
    spec = registry.resolve("mimo", "mimo-v2.6-flash")

    enabled = registry.build_payload(spec, [], None, spec.normalize_params({}), True)
    assert enabled["thinking"] == {"type": "enabled"}
    assert enabled["stream_options"] == {"include_usage": True}
    assert "reasoning_effort" not in enabled

    disabled = registry.build_payload(
        spec, [], None, spec.normalize_params({"thinking": False}), True
    )
    assert disabled["thinking"] == {"type": "disabled"}


def test_gpt_payload_uses_reasoning_effort_only() -> None:
    registry = ProviderRegistry()
    spec = registry.resolve("4router", "gpt-6-luna")

    payload = registry.build_payload(
        spec, [], None, spec.normalize_params({"reasoning_effort": "max"}), True
    )
    assert payload["reasoning_effort"] == "max"
    assert "thinking" not in payload
    assert "enable_thinking" not in payload
    assert "tool_stream" not in payload
    assert payload["stream_options"] == {"include_usage": True}
