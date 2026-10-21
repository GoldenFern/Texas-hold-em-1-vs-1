"""Streaming OpenAI-compatible chat client with tool-call accumulation."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import requests

from .providers import ModelSpec, ProviderRegistry

logger = logging.getLogger("hu.llm")
TRAFFIC = os.environ.get("HU_LLM_LOG_TRAFFIC", "").strip().lower() in ("1", "true", "yes", "on")


class LLMError(Exception):
    """Typed error surfaced to the UI."""

    def __init__(self, kind: str, detail: str, status: int = 0) -> None:
        super().__init__(f"[{kind}] {detail}")
        self.kind = kind
        self.detail = detail
        self.status = status


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str

    def parsed_arguments(self) -> Optional[dict]:
        text = (self.arguments or "").strip()
        if not text:
            return {}
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None


@dataclass
class ChatResult:
    content: str = ""
    reasoning: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    finish_reason: str = ""
    usage: Dict[str, int] = field(default_factory=dict)
    latency_ms: float = 0.0
    estimated_usage: bool = False

    def assistant_message(self, echo_reasoning: bool) -> dict:
        message: Dict[str, Any] = {"role": "assistant", "content": self.content or ""}
        if echo_reasoning and self.reasoning:
            message["reasoning_content"] = self.reasoning
        if self.tool_calls:
            message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments or "{}"},
                }
                for call in self.tool_calls
            ]
        return message

    @property
    def prompt_tokens(self) -> int:
        return int(self.usage.get("input_tokens", 0))

    @property
    def completion_tokens(self) -> int:
        return int(self.usage.get("output_tokens", 0))


def estimate_tokens(text: str) -> int:
    """Cheap bilingual token estimate (CJK chars count as one token)."""
    if not text:
        return 0
    cjk = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    other = len(text) - cjk
    return max(1, cjk + other // 4)


def _normalize_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        chunks: List[str] = []
        for part in value:
            if isinstance(part, str):
                chunks.append(part)
            elif isinstance(part, dict):
                if isinstance(part.get("text"), str):
                    chunks.append(part["text"])
                elif isinstance(part.get("content"), str):
                    chunks.append(part["content"])
        return "".join(chunks)
    return ""


def _usage_from(payload: dict) -> dict:
    usage = payload.get("usage") or {}
    if not isinstance(usage, dict):
        return {}
    input_tokens = usage.get("prompt_tokens", usage.get("input_tokens", 0))
    output_tokens = usage.get("completion_tokens", usage.get("output_tokens", 0))
    details = usage.get("completion_tokens_details") or {}
    reasoning_tokens = details.get("reasoning_tokens", 0) if isinstance(details, dict) else 0
    if not input_tokens and not output_tokens:
        return {}
    return {
        "input_tokens": int(input_tokens or 0),
        "output_tokens": int(output_tokens or 0),
        "reasoning_tokens": int(reasoning_tokens or 0),
        "total_tokens": int(usage.get("total_tokens", 0) or 0),
    }


def classify_http(status: int, body: str) -> str:
    text = (body or "").lower()
    if status in (401, 403) or "invalid api key" in text or "authentication" in text:
        return "auth"
    if status == 429 or "rate limit" in text or "quota" in text:
        return "rate_limit"
    if status in (400, 404, 415, 422) or "invalid parameter" in text or "invalid_request" in text:
        return "param"
    if status >= 500:
        return "server"
    return "http"


class LLMClient:
    """Thin client around ``/chat/completions`` with SSE streaming."""

    def __init__(
        self,
        registry: ProviderRegistry,
        connect_timeout: float = 20.0,
        read_timeout: float = 300.0,
    ) -> None:
        self.registry = registry
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self._session = requests.Session()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def stream(
        self,
        spec: ModelSpec,
        messages: List[dict],
        tools: Optional[List[dict]],
        params: dict,
        on_event: Callable[[dict], None],
    ) -> ChatResult:
        api_key = ProviderRegistry.get_api_key(spec)
        payload = self.registry.build_payload(spec, messages, tools, params, stream=True)
        self._log_request(spec, payload)

        attempts = 0
        last_error: Optional[LLMError] = None
        while attempts < 3:
            attempts += 1
            try:
                result = self._stream_once(spec, api_key, payload, on_event)
                self._log_response(spec, result)
                return result
            except LLMError as exc:
                last_error = exc
                if exc.kind == "rate_limit" and attempts == 1:
                    time.sleep(1.5)
                    continue
                if exc.kind == "param" and attempts < 3:
                    reduced = self._reduce_payload(payload, exc.detail)
                    if reduced is not None:
                        payload = reduced
                        on_event(
                            {
                                "type": "note",
                                "level": "warn",
                                "text": "厂商拒绝了部分可选参数，已自动降级重试。",
                            }
                        )
                        continue
                raise
            except requests.Timeout as exc:
                raise LLMError("timeout", f"请求超时: {exc}") from exc
            except requests.ConnectionError as exc:
                raise LLMError("network", f"网络连接失败: {exc}") from exc
            except Exception as exc:  # noqa: BLE001
                raise LLMError("unknown", f"{type(exc).__name__}: {exc}") from exc
        if last_error:
            raise last_error
        raise LLMError("unknown", "未知错误")

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    def _stream_once(
        self,
        spec: ModelSpec,
        api_key: str,
        payload: dict,
        on_event: Callable[[dict], None],
    ) -> ChatResult:
        start = time.perf_counter()
        response = self._session.post(
            self.registry.endpoint(spec),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
            json=payload,
            stream=True,
            timeout=(self.connect_timeout, self.read_timeout),
        )
        if response.status_code >= 400:
            body = response.text[:4000]
            raise LLMError(
                classify_http(response.status_code, body),
                f"HTTP {response.status_code}: {body}",
                response.status_code,
            )

        content_parts: List[str] = []
        reasoning_parts: List[str] = []
        tool_calls: Dict[int, dict] = {}
        finish_reason = ""
        usage: dict = {}
        saw_event = False

        for raw_line in response.iter_lines(decode_unicode=False):
            if raw_line is None:
                continue
            line = raw_line.decode("utf-8", errors="replace").strip() if isinstance(raw_line, bytes) else str(raw_line).strip()
            if not line:
                continue
            if line.startswith("data:"):
                data_text = line[5:].strip()
            elif line.startswith("{"):
                data_text = line
            else:
                continue
            if data_text == "[DONE]":
                break
            try:
                event = json.loads(data_text)
            except json.JSONDecodeError:
                continue
            saw_event = True
            if isinstance(event.get("error"), dict):
                detail = event["error"].get("message") or json.dumps(event["error"], ensure_ascii=False)
                raise LLMError("server", detail)

            chunk_usage = _usage_from(event)
            if chunk_usage:
                usage = chunk_usage

            choices = event.get("choices") or []
            if not choices:
                continue
            choice = choices[0] or {}
            if choice.get("finish_reason"):
                finish_reason = str(choice["finish_reason"])
            delta = choice.get("delta") or choice.get("message") or {}

            reasoning = _normalize_text(delta.get("reasoning_content") or delta.get("reasoning"))
            if reasoning:
                reasoning_parts.append(reasoning)
                on_event({"type": "reasoning_delta", "delta": reasoning})

            text = _normalize_text(delta.get("content"))
            if text:
                content_parts.append(text)
                on_event({"type": "content_delta", "delta": text})

            calls = delta.get("tool_calls")
            if isinstance(calls, list):
                for raw_call in calls:
                    self._accumulate_tool_call(tool_calls, raw_call, on_event)
            legacy = delta.get("function_call")
            if isinstance(legacy, dict):
                self._accumulate_tool_call(
                    tool_calls,
                    {"index": 0, "function": legacy},
                    on_event,
                )

        response.close()
        latency_ms = (time.perf_counter() - start) * 1000.0

        if not saw_event:
            # Some gateways silently return an empty SSE stream. Fall back to
            # a regular JSON completion once.
            return self._non_stream_once(spec, api_key, payload, start, on_event)

        content = "".join(content_parts)
        reasoning = "".join(reasoning_parts)
        content, extra_reasoning = _split_think_tags(content)
        reasoning = (reasoning + extra_reasoning).strip()
        content = content.strip()

        if not usage:
            prompt_text = json.dumps(payload.get("messages", []), ensure_ascii=False)
            usage = {
                "input_tokens": estimate_tokens(prompt_text),
                "output_tokens": estimate_tokens(content + reasoning),
                "reasoning_tokens": 0,
                "total_tokens": 0,
            }
            estimated = True
        else:
            estimated = False

        return ChatResult(
            content=content,
            reasoning=reasoning,
            tool_calls=self._finalize_tool_calls(tool_calls),
            finish_reason=finish_reason,
            usage=usage,
            latency_ms=latency_ms,
            estimated_usage=estimated,
        )

    def _non_stream_once(
        self,
        spec: ModelSpec,
        api_key: str,
        payload: dict,
        start: float,
        on_event: Callable[[dict], None],
    ) -> ChatResult:
        fallback = dict(payload)
        fallback["stream"] = False
        fallback.pop("stream_options", None)
        fallback.pop("tool_stream", None)
        response = self._session.post(
            self.registry.endpoint(spec),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=fallback,
            timeout=(self.connect_timeout, self.read_timeout),
        )
        if response.status_code >= 400:
            body = response.text[:4000]
            raise LLMError(
                classify_http(response.status_code, body),
                f"HTTP {response.status_code}: {body}",
                response.status_code,
            )
        data = response.json()
        choice = (data.get("choices") or [{}])[0] or {}
        message = choice.get("message") or {}
        content = _normalize_text(message.get("content"))
        reasoning = _normalize_text(message.get("reasoning_content") or message.get("reasoning"))
        content, extra = _split_think_tags(content)
        reasoning = (reasoning + extra).strip()
        if content:
            on_event({"type": "content_delta", "delta": content})
        if reasoning:
            on_event({"type": "reasoning_delta", "delta": reasoning})
        tool_calls: Dict[int, dict] = {}
        for index, raw_call in enumerate(message.get("tool_calls") or []):
            self._accumulate_tool_call(tool_calls, {**raw_call, "index": index}, on_event)
        usage = _usage_from(data)
        estimated = not bool(usage)
        if not usage:
            usage = {
                "input_tokens": estimate_tokens(json.dumps(payload.get("messages", []), ensure_ascii=False)),
                "output_tokens": estimate_tokens(content + reasoning),
                "reasoning_tokens": 0,
                "total_tokens": 0,
            }
        return ChatResult(
            content=content.strip(),
            reasoning=reasoning,
            tool_calls=self._finalize_tool_calls(tool_calls),
            finish_reason=str(choice.get("finish_reason", "")),
            usage=usage,
            latency_ms=(time.perf_counter() - start) * 1000.0,
            estimated_usage=estimated,
        )

    # ------------------------------------------------------------------
    # Tool call fragments
    # ------------------------------------------------------------------

    @staticmethod
    def _accumulate_tool_call(
        store: Dict[int, dict],
        raw_call: dict,
        on_event: Callable[[dict], None],
    ) -> None:
        if not isinstance(raw_call, dict):
            return
        index = raw_call.get("index", 0)
        try:
            index = int(index)
        except (TypeError, ValueError):
            index = 0
        entry = store.setdefault(
            index,
            {"id": "", "name": "", "arguments": "", "started": False},
        )
        call_id = raw_call.get("id")
        if call_id:
            entry["id"] = str(call_id)
        function = raw_call.get("function") or {}
        name_fragment = _normalize_text(function.get("name"))
        if name_fragment:
            current = entry["name"]
            if not current:
                entry["name"] = name_fragment
            elif name_fragment != current and not current.endswith(name_fragment):
                entry["name"] = current + name_fragment
        if not entry["started"] and (entry["name"] or entry["id"]):
            entry["started"] = True
            on_event(
                {
                    "type": "tool_call_start",
                    "index": index,
                    "call_id": entry["id"] or f"call_{index}",
                    "name": entry["name"],
                }
            )
        args = function.get("arguments")
        if isinstance(args, dict):
            args = json.dumps(args, ensure_ascii=False)
        if isinstance(args, str) and args:
            entry["arguments"] += args
            on_event(
                {
                    "type": "tool_call_args_delta",
                    "index": index,
                    "call_id": entry["id"] or f"call_{index}",
                    "delta": args,
                }
            )

    @staticmethod
    def _finalize_tool_calls(store: Dict[int, dict]) -> List[ToolCall]:
        calls: List[ToolCall] = []
        for index in sorted(store):
            entry = store[index]
            if not entry.get("name"):
                continue
            calls.append(
                ToolCall(
                    id=entry.get("id") or f"call_{index}",
                    name=entry["name"],
                    arguments=entry.get("arguments") or "{}",
                )
            )
        return calls

    # ------------------------------------------------------------------
    # Payload degradation on 400
    # ------------------------------------------------------------------

    @staticmethod
    def _reduce_payload(payload: dict, detail: str) -> Optional[dict]:
        """Drop optional vendor fields when a provider rejects them."""
        reduced = json.loads(json.dumps(payload))
        changed = False
        text = (detail or "").lower()

        if "max_tokens" in text and reduced.get("max_tokens", 0) > 8192:
            reduced["max_tokens"] = 8192
            changed = True

        for key in ("stream_options", "tool_stream", "preserve_thinking", "thinking", "reasoning_effort", "enable_thinking"):
            if key in reduced:
                reduced.pop(key, None)
                changed = True

        if "reasoning_content" in text:
            messages = reduced.get("messages", [])
            for message in messages:
                if isinstance(message, dict):
                    message.pop("reasoning_content", None)
            changed = True

        if changed:
            return reduced
        # Last resort: strip reasoning_content as vendors sometimes reject it
        # without naming the field.
        messages = reduced.get("messages", [])
        for message in messages:
            if isinstance(message, dict) and "reasoning_content" in message:
                message.pop("reasoning_content", None)
                changed = True
        return reduced if changed else None

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    @staticmethod
    def _log_request(spec: ModelSpec, payload: dict) -> None:
        if not TRAFFIC:
            return
        summary = {
            "provider": spec.provider_id,
            "model": spec.id,
            "stream": payload.get("stream"),
            "max_tokens": payload.get("max_tokens"),
            "thinking": payload.get("thinking") or payload.get("enable_thinking"),
            "reasoning_effort": payload.get("reasoning_effort"),
            "messages": len(payload.get("messages", [])),
        }
        logger.info("LLM request %s", json.dumps(summary, ensure_ascii=False))

    @staticmethod
    def _log_response(spec: ModelSpec, result: ChatResult) -> None:
        if not TRAFFIC:
            return
        logger.info(
            "LLM response provider=%s model=%s latency=%.0fms usage=%s tools=%d content=%dchars reasoning=%dchars",
            spec.provider_id,
            spec.id,
            result.latency_ms,
            result.usage,
            len(result.tool_calls),
            len(result.content),
            len(result.reasoning),
        )

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self._session.close()


def _split_think_tags(content: str) -> tuple[str, str]:
    """Handle providers that embed reasoning in ``<think>`` tags."""
    if "<think" not in content:
        return content, ""
    import re

    pattern = r"<think(?:ing)?[^>]*>(.*?)(?:</think(?:ing)?>|<｜end▁of▁thinking｜>)"
    found = re.findall(pattern, content, flags=re.S | re.I)
    cleaned = re.sub(pattern, "", content, flags=re.S | re.I)
    return cleaned.strip(), "\n".join(part.strip() for part in found if part.strip())


__all__ = ["ChatResult", "LLMClient", "LLMError", "ToolCall", "estimate_tokens"]
