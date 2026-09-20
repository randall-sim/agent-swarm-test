"""Small Chat Completions-compatible client; no vendor SDK required."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from threading import Lock
from typing import Any


class ProviderError(RuntimeError):
    pass


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class Budget:
    limit: int
    used: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    _lock: Lock = field(default_factory=Lock, repr=False, compare=False)

    def reserve(self) -> None:
        with self._lock:
            if self.used >= self.limit:
                raise BudgetExceeded(f"LLM request budget ({self.limit}) exhausted.")
            self.used += 1

    def record(self, usage: dict) -> None:
        prompt = int(usage.get("prompt_tokens", 0) or 0)
        completion = int(usage.get("completion_tokens", 0) or 0)
        with self._lock:
            self.input_tokens += prompt
            self.output_tokens += completion


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward a bearer token to an unexpected host.
        return None


class Client:
    def __init__(self, base_url: str, model: str, api_key: str, budget: Budget,
                 timeout: float = 90, max_tokens: int = 4096):
        url = urllib.parse.urlsplit(base_url)
        if url.scheme not in {"https", "http"} or not url.hostname:
            raise ValueError("Base URL must be an HTTP(S) URL.")
        if url.username or url.password or url.query or url.fragment:
            raise ValueError("Base URL must not contain credentials, a query, or a fragment.")
        if url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Use HTTPS for non-local LLM endpoints.")
        self.native_tools = url.hostname == "api.openai.com"
        self.token_parameter = "max_completion_tokens" if url.hostname == "api.openai.com" else "max_tokens"
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model, self.api_key, self.budget = model, api_key, budget
        self.timeout, self.max_tokens = timeout, max_tokens
        self.opener = urllib.request.build_opener(NoRedirect())

    def fork(self) -> "Client":
        """Independent HTTP transport and conversations, one shared request budget."""
        return Client(self.url.removesuffix("/chat/completions"), self.model, self.api_key,
                      self.budget, self.timeout, self.max_tokens)

    def complete(self, messages: list[dict[str, Any]], *, tools: list[dict] | None = None) -> dict[str, Any]:
        body = {"model": self.model, "messages": messages, self.token_parameter: self.max_tokens}
        if self.native_tools and tools:
            body.update(tools=tools, tool_choice="required", parallel_tool_calls=False)
        payload = json.dumps(body).encode()
        for retry in range(3):
            self.budget.reserve()  # Atomic across workers; retries count too.
            request = urllib.request.Request(self.url, data=payload, headers={
                "Authorization": "Bearer " + self.api_key,
                "Content-Type": "application/json",
            })
            try:
                with self.opener.open(request, timeout=self.timeout) as response:
                    data = json.loads(response.read(2_000_000))
                usage = data.get("usage", {})
                self.budget.record(usage)
                message = data["choices"][0]["message"]
                calls = message.get("tool_calls")
                if self.native_tools and tools and calls:
                    if len(calls) != 1:
                        raise ValueError("Expected one tool call")
                    call = calls[0]
                    name = call["function"]["name"]
                    if name not in {tool["function"]["name"] for tool in tools}:
                        raise ValueError("Unknown function")
                    arguments = json.loads(call["function"]["arguments"])
                    if not isinstance(arguments, dict) or not isinstance(call.get("id"), str):
                        raise ValueError("Invalid tool arguments or call ID")
                    result = {"final": arguments} if name == "finish" else {"tool": name, "args": arguments}
                    result["_native_message"] = {"role": "assistant", "content": message.get("content"), "tool_calls": calls}
                    result["_native_call_id"] = call["id"]
                    return result
                content = message["content"]
                if not isinstance(content, str):
                    raise ValueError("Expected text content")
                content = content.strip()
                if content.startswith("```") and content.endswith("```"):
                    content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                result = json.loads(content)
                if not isinstance(result, dict):
                    raise ValueError("Expected a JSON object")
                result.pop("_native_message", None)
                result.pop("_native_call_id", None)
                return result
            except urllib.error.HTTPError as exc:
                if exc.code in {429, 500, 502, 503, 504} and retry < 2:
                    time.sleep(2 ** retry)
                    continue
                # Error bodies may echo credentials or private input; omit them.
                raise ProviderError(f"LLM endpoint returned HTTP {exc.code}. Check endpoint, model, key and quota.") from None
            except (urllib.error.URLError, TimeoutError, OSError):
                raise ProviderError("LLM connection failed or timed out. Check your endpoint and network.") from None
            except (ValueError, KeyError, IndexError, TypeError):
                raise ProviderError("LLM response was not the expected JSON object. Use a model that follows JSON instructions.") from None
        raise ProviderError("LLM request failed.")
