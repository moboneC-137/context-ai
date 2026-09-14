"""OpenAI provider over the Chat Completions HTTP API (PRD FR-21).

Deliberately SDK-free: one `urllib` POST behind an injectable `transport`, so the error mapping —
the part FR-24 depends on — is tested without a network. The key is resolved lazily from the
environment or the login Keychain (`security` CLI) and is never part of `repr`, logs or errors (NFR-5).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Mapping

from .base import (
    APIError,
    Completion,
    NoNetwork,
    NotConfigured,
    Prompt,
    RateLimit,
    Timeout,
)

DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_ENDPOINT = "https://api.openai.com/v1/chat/completions"
DEFAULT_TIMEOUT_SECONDS = 30.0
ENV_VAR = "OPENAI_API_KEY"
KEYCHAIN_SERVICE = "ContextAI"
KEYCHAIN_ACCOUNT = "openai"

Transport = Callable[[str, bytes, Mapping[str, str], float], tuple[int, bytes]]
"""(url, body, headers, timeout) -> (status, body). Raises urllib/OS errors for transport failures."""


def urllib_transport(url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as err:
        # Non-2xx is still an answer; let the provider classify it by status.
        return err.code, err.read()


def read_keychain_key(service: str = KEYCHAIN_SERVICE, account: str = KEYCHAIN_ACCOUNT) -> str | None:
    """Generic password lookup via the `security` CLI; None when absent or when not on macOS."""
    try:
        proc = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    key = proc.stdout.strip()
    return key if proc.returncode == 0 and key else None


def resolve_api_key(
    env: Mapping[str, str] | None = None,
    keychain: Callable[[], str | None] = read_keychain_key,
) -> str | None:
    """Environment first (developer override), then the Keychain (the shipped path)."""
    env = os.environ if env is None else env
    from_env = env.get(ENV_VAR, "").strip()
    return from_env or keychain()


@dataclass
class OpenAIProvider:
    model: str = DEFAULT_MODEL
    api_key: str | None = field(default=None, repr=False)
    """Explicit key for tests; production leaves it None and `key_source` resolves it per request."""
    key_source: Callable[[], str | None] = field(default=resolve_api_key, repr=False)
    endpoint: str = DEFAULT_ENDPOINT
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    transport: Transport = field(default=urllib_transport, repr=False)

    @property
    def name(self) -> str:
        return "openai"

    def complete(self, prompt: Prompt) -> Completion:
        key = self.api_key or self.key_source()
        if not key:
            raise NotConfigured("no OpenAI API key: set it in Settings (Keychain) or OPENAI_API_KEY")

        payload: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": prompt.system},
                {"role": "user", "content": prompt.user},
            ],
        }
        if prompt.max_output_tokens is not None:
            payload["max_completion_tokens"] = prompt.max_output_tokens
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

        try:
            status, body = self.transport(self.endpoint, json.dumps(payload).encode(), headers, self.timeout)
        except TimeoutError as err:
            raise Timeout(f"OpenAI did not answer within {self.timeout:g} s") from err
        except urllib.error.URLError as err:
            if isinstance(err.reason, TimeoutError):
                raise Timeout(f"OpenAI did not answer within {self.timeout:g} s") from err
            raise NoNetwork(f"cannot reach OpenAI: {err.reason}") from err
        except OSError as err:
            raise NoNetwork(f"cannot reach OpenAI: {err}") from err

        return self._parse(status, body)

    def _parse(self, status: int, body: bytes) -> Completion:
        if status == 429:
            raise RateLimit("OpenAI rate limit reached", retry_after=_retry_after(body))
        if status in (401, 403):
            # A rejected key is a setup problem, not a transient one.
            raise NotConfigured(f"OpenAI rejected the API key (HTTP {status})")
        if status < 200 or status >= 300:
            raise APIError(f"OpenAI error (HTTP {status}): {_error_message(body)}", status=status)
        try:
            data = json.loads(body)
            choice = data["choices"][0]["message"]
            text = choice["content"]
            if not isinstance(text, str):
                raise TypeError("content is not a string")
        except (ValueError, KeyError, IndexError, TypeError) as err:
            raise APIError(f"OpenAI returned an unexpected response: {err}", status=status) from err
        usage = data.get("usage") or {}
        return Completion(
            text=text,
            provider=self.name,
            model=str(data.get("model", self.model)),
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
        )


def _error_message(body: bytes) -> str:
    try:
        message = json.loads(body)["error"]["message"]
        return str(message)[:200]
    except (ValueError, KeyError, TypeError):
        return body[:200].decode(errors="replace") or "empty body"


def _retry_after(body: bytes) -> float | None:
    # OpenAI puts the wait in the message ("Please try again in 20s"); headers are not exposed by the
    # transport on purpose (keeps its signature trivial). Best effort, None when absent.
    match = re.search(r"try again in ([\d.]+)\s*(ms|s)", _error_message(body))
    if not match:
        return None
    value = float(match.group(1))
    return value / 1000 if match.group(2) == "ms" else value
