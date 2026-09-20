"""Small Chat Completions-compatible client; no vendor SDK required."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
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
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model, self.api_key, self.budget = model, api_key, budget
        self.timeout, self.max_tokens = timeout, max_tokens
        self.opener = urllib.request.build_opener(NoRedirect())

    def complete(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        payload = json.dumps({"model": self.model, "messages": messages,
                              "max_tokens": self.max_tokens}).encode()
        for retry in range(3):
            if self.budget.used >= self.budget.limit:
                raise BudgetExceeded(f"LLM request budget ({self.budget.limit}) exhausted.")
            self.budget.used += 1  # Retries consume the same hard budget.
            request = urllib.request.Request(self.url, data=payload, headers={
                "Authorization": "Bearer " + self.api_key,
                "Content-Type": "application/json",
            })
            try:
                with self.opener.open(request, timeout=self.timeout) as response:
                    data = json.loads(response.read(2_000_000))
                usage = data.get("usage", {})
                self.budget.input_tokens += int(usage.get("prompt_tokens", 0) or 0)
                self.budget.output_tokens += int(usage.get("completion_tokens", 0) or 0)
                content = data["choices"][0]["message"]["content"]
                if not isinstance(content, str):
                    raise ValueError("Expected text content")
                content = content.strip()
                if content.startswith("```") and content.endswith("```"):
                    content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                result = json.loads(content)
                if not isinstance(result, dict):
                    raise ValueError("Expected a JSON object")
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
