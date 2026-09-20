"""Small Chat Completions-compatible client; no vendor SDK required."""
from __future__ import annotations

import json
import math
import random
import re
from email.utils import parsedate_to_datetime
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


class AgentStepLimitExceeded(BudgetExceeded):
    def __init__(self, agent_id, role, limit):
        self.agent_id, self.role, self.limit = agent_id, role, limit
        super().__init__(f"{agent_id} reached its {limit}-step limit; run can be resumed.")


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


class RateLimitExceeded(ProviderError):
    """Temporary provider throttling exhausted the bounded retry window."""


def retry_delay(headers, retry):
    """Honor explicit server timing; otherwise use reset hints or jittered backoff."""
    for key, scale in (("retry-after-ms", .001), ("retry-after", 1)):
        value = headers.get(key)
        if value is None:
            continue
        try:
            seconds = float(value) * scale
        except (ValueError, TypeError):
            try:
                seconds = parsedate_to_datetime(value).timestamp() - time.time()
            except (ValueError, TypeError, OverflowError):
                continue
        if math.isfinite(seconds):
            return max(0, seconds)
    resets = []
    for resource in ("requests", "tokens", "project-tokens"):
        if headers.get("x-ratelimit-remaining-" + resource) != "0":
            continue
        value = headers.get("x-ratelimit-reset-" + resource, "")
        parts = re.findall(r"(\d+(?:\.\d+)?)(ms|s|m|h)", value)
        if parts and "".join(n + unit for n, unit in parts) == value:
            resets.append(sum(float(n) * {"ms": .001, "s": 1, "m": 60, "h": 3600}[unit] for n, unit in parts))
    return max(resets) if resets else min(60, 5 * 2 ** retry) + random.uniform(0, 1)


class RateGate:
    """A cooldown shared by a run's parallel clients, with staggered recovery."""
    def __init__(self):
        self.lock = Lock()
        self.until = 0.0
        self.paced = False

    def defer(self, seconds):
        with self.lock:
            self.until = max(self.until, time.monotonic() + seconds)
            self.paced = True

    def wait(self, deadline, check_cancelled, on_wait):
        announced = False
        while True:
            check_cancelled()
            with self.lock:
                now = time.monotonic()
                delay = self.until - now
                if delay <= 0:
                    if self.paced:
                        self.until = now + .5
                    return
            if now + delay > deadline:
                raise RateLimitExceeded("Provider cooldown exceeds the five-minute retry window. Resume later; no early retry was sent.")
            if not announced:
                on_wait({"kind": "provider_wait", "seconds": round(delay, 2)})
                announced = True
            time.sleep(min(delay, .25))


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
        self.rate_gate = RateGate()
        self.check_cancelled = lambda: None
        self.on_event = lambda event: None

    def fork(self) -> "Client":
        """Independent HTTP transport and conversations, one shared request budget."""
        child = Client(self.url.removesuffix("/chat/completions"), self.model, self.api_key,
                       self.budget, self.timeout, self.max_tokens)
        child.rate_gate = self.rate_gate
        return child

    def complete(self, messages: list[dict[str, Any]], *, tools: list[dict] | None = None) -> dict[str, Any]:
        body = {"model": self.model, "messages": messages, self.token_parameter: self.max_tokens}
        if self.native_tools and tools:
            body.update(tools=tools, tool_choice="required", parallel_tool_calls=False)
        payload = json.dumps(body).encode()
        deadline = time.monotonic() + 300
        for retry in range(7):
            if self.budget.used >= self.budget.limit:
                raise BudgetExceeded(f"LLM request budget ({self.budget.limit}) exhausted.")
            self.rate_gate.wait(deadline, self.check_cancelled, self.on_event)
            self.check_cancelled()
            self.budget.reserve()  # Atomic across workers; retries count too.
            request = urllib.request.Request(self.url, data=payload, headers={
                "Authorization": "Bearer " + self.api_key,
                "Content-Type": "application/json",
            })
            self.on_event({"kind": "provider_request", "retry": retry})
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
                # Inspect only machine-readable error codes; never log raw error bodies.
                try:
                    error = json.loads(exc.read(65536)).get("error", {})
                    quota = any(error.get(field) in {
                        "insufficient_quota", "credit_balance_exhausted",
                        "organization_spend_limit_exceeded", "project_spend_limit_exceeded",
                        "organization_usage_limit_exceeded", "billing_hard_limit_reached",
                    } for field in ("code", "type"))
                except (ValueError, TypeError, AttributeError):
                    quota = False
                finally:
                    exc.close()
                if exc.code == 429 and quota:
                    raise ProviderError("LLM quota or billing limit reached (HTTP 429). Update provider credits or limits before resuming; automatic retries cannot resolve this.") from None
                if exc.code in {429, 500, 502, 503, 504}:
                    delay = retry_delay(exc.headers, retry)
                    self.rate_gate.defer(delay)
                    if retry >= 6 or time.monotonic() + delay > deadline:
                        if exc.code == 429:
                            raise RateLimitExceeded("Provider rate limit (HTTP 429) persists beyond the retry limit. Resume later or reduce workers/request size.") from None
                        raise ProviderError(f"LLM service unavailable (HTTP {exc.code}) after bounded retries. Resume later.") from None
                    self.on_event({"kind": "provider_retry", "status": exc.code,
                                   "retry": retry + 1, "max_retries": 6, "seconds": round(delay, 2)})
                    continue
                # Error bodies may echo credentials or private input; omit them.
                raise ProviderError(f"LLM endpoint returned HTTP {exc.code}. Check endpoint, model, key and quota.") from None
            except (urllib.error.URLError, TimeoutError, OSError):
                raise ProviderError("LLM connection failed or timed out. Check your endpoint and network.") from None
            except (ValueError, KeyError, IndexError, TypeError):
                raise ProviderError("LLM response was not the expected JSON object. Use a model that follows JSON instructions.") from None
        raise ProviderError("LLM request failed.")
