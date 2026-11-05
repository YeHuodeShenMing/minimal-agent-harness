"""OpenAI-compatible chat completions transport; no provider SDK or agent framework."""
import http.client
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


class ModelError(RuntimeError):
    pass


class ProtocolError(ValueError):
    pass


@dataclass
class Decision:
    content: str
    calls: list
    reasoning_summary: str = ""

    def message(self):
        message = {"role": "assistant", "content": self.content or None}
        if self.calls:
            message["tool_calls"] = self.calls
        return message


def parse(response):
    try:
        if not isinstance(response, dict):
            raise ProtocolError("response must be an object")
        choice = response["choices"][0]
        if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
            raise ProtocolError("invalid message")
        if choice.get("finish_reason") in ("length", "content_filter"):
            raise ProtocolError("incomplete or filtered output")
        message = choice["message"]
        content = message.get("content")
        content = "" if content is None else content
        calls = message.get("tool_calls")
        calls = [] if calls is None else calls
        if message.get("role", "assistant") != "assistant":
            raise ProtocolError("expected assistant message")
        if choice.get("finish_reason") not in ("stop", "tool_calls"):
            raise ProtocolError("unsupported finish reason")
        if (choice["finish_reason"] == "tool_calls") != bool(calls):
            raise ProtocolError("finish reason does not match calls")
        if not isinstance(content, str) or not isinstance(calls, list) or len(calls) > 8:
            raise ProtocolError("invalid output shape")
        seen = set()
        for call in calls:
            if not isinstance(call, dict) or not isinstance(call.get("function"), dict):
                raise ProtocolError("invalid call shape")
            if not isinstance(call["id"], str) or not call["id"] or call["id"] in seen:
                raise ProtocolError("invalid or duplicate call id")
            seen.add(call["id"])
            if call.get("type") != "function" or not isinstance(call["function"]["name"], str):
                raise ProtocolError("invalid function call")
            if not isinstance(call["function"]["arguments"], str):
                raise ProtocolError("arguments must be a JSON string")
        if not content.strip() and not calls:
            raise ProtocolError("empty assistant output")
        # Only an explicitly supplied short decision summary is accepted. Hidden chain-of-thought
        # (reasoning_content, thinking blocks) is neither requested, persisted, nor replayed.
        summary = message.get("reasoning_summary", "")
        return Decision(content, calls, summary[:500] if isinstance(summary, str) else "")
    except (KeyError, IndexError, TypeError) as error:
        raise ProtocolError("malformed model response") from error


class ChatModel:
    def __init__(self):
        self.key = os.environ.get("LLM_API_KEY", "")
        self.model = os.environ.get("LLM_MODEL", "")
        self.base = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        if not self.key or not self.model:
            raise ModelError("Set LLM_API_KEY and LLM_MODEL; see .env.example")
        if not self.base.startswith("https://") and not self.base.startswith("http://localhost:"):
            raise ModelError("Use HTTPS (or localhost for a local model)")

    def complete(self, messages, tools):
        body = {"model": self.model, "messages": messages, "max_tokens": 1600}
        if tools:
            body.update(tools=tools, tool_choice="auto")
        payload = json.dumps(body).encode()
        request = urllib.request.Request(self.base + "/chat/completions", data=payload,
            headers={"Authorization": "Bearer " + self.key, "Content-Type": "application/json"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=40) as response:
                    data = response.read(2_000_001)
                    if len(data) > 2_000_000:
                        raise ModelError("response exceeds size limit")
                    return json.loads(data)
            except urllib.error.HTTPError as error:
                status = error.code
                error.close()
                if status not in (429, 500, 502, 503, 504) or attempt == 2:
                    raise ModelError(f"LLM HTTP {status}") from None
            except (urllib.error.URLError, OSError, http.client.HTTPException):
                if attempt == 2:
                    raise ModelError("LLM network timeout/unavailable") from None
            except (ValueError, UnicodeError):
                raise ModelError("LLM returned invalid JSON") from None
            time.sleep(0.25 * 2 ** attempt)
