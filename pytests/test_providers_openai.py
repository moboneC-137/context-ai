"""FR-21 / FR-24: OpenAI transport failures and HTTP statuses map to the typed errors; the key never leaks."""

from __future__ import annotations

import json
import socket
import urllib.error
from typing import Mapping

import pytest

from contextai.providers import (
    APIError,
    NoNetwork,
    NotConfigured,
    OpenAIProvider,
    Prompt,
    Provider,
    RateLimit,
    Timeout,
    resolve_api_key,
)

PROMPT = Prompt(system="be brief", user="hi", max_output_tokens=50)
SECRET = "sk-test-secret-value"


def ok_body(text: str = "hello", model: str = "gpt-test") -> bytes:
    return json.dumps(
        {
            "model": model,
            "choices": [{"message": {"content": text}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1},
        }
    ).encode()


def make(transport, **kwargs) -> OpenAIProvider:
    return OpenAIProvider(api_key=SECRET, transport=transport, timeout=1.5, **kwargs)


def test_satisfies_the_provider_protocol():
    assert isinstance(OpenAIProvider(), Provider)


def test_success_sends_chat_payload_and_parses_completion():
    seen: dict = {}

    def transport(url: str, body: bytes, headers: Mapping[str, str], timeout: float):
        seen.update(url=url, body=json.loads(body), headers=dict(headers), timeout=timeout)
        return 200, ok_body("bonjour")

    completion = make(transport, model="gpt-x").complete(PROMPT)
    assert completion.text == "bonjour"
    assert completion.provider == "openai" and completion.model == "gpt-test"
    assert (completion.input_tokens, completion.output_tokens) == (3, 1)
    assert seen["url"].endswith("/chat/completions")
    assert seen["timeout"] == 1.5
    assert seen["headers"]["Authorization"] == f"Bearer {SECRET}"
    assert seen["body"]["model"] == "gpt-x"
    assert seen["body"]["messages"] == [{"role": "system", "content": "be brief"}, {"role": "user", "content": "hi"}]
    assert seen["body"]["max_completion_tokens"] == 50


@pytest.mark.parametrize(
    ("raised", "expected"),
    [
        (urllib.error.URLError(ConnectionRefusedError("refused")), NoNetwork),
        (urllib.error.URLError(socket.gaierror("dns")), NoNetwork),
        (urllib.error.URLError(TimeoutError("slow")), Timeout),
        (TimeoutError("slow"), Timeout),
        (ConnectionResetError("reset"), NoNetwork),
    ],
    ids=lambda x: getattr(x, "__name__", type(x).__name__),
)
def test_transport_exceptions_map_to_typed_errors(raised, expected):
    def transport(*_):
        raise raised

    with pytest.raises(expected):
        make(transport).complete(PROMPT)


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (429, json.dumps({"error": {"message": "Rate limit. Please try again in 20s."}}).encode(), RateLimit),
        (401, b'{"error":{"message":"Incorrect API key"}}', NotConfigured),
        (403, b"", NotConfigured),
        (500, b'{"error":{"message":"server exploded"}}', APIError),
        (502, b"<html>bad gateway</html>", APIError),
        (200, b"not json", APIError),
        (200, b'{"choices": []}', APIError),
        (200, b'{"choices": [{"message": {"content": null}}]}', APIError),
    ],
)
def test_http_statuses_and_malformed_bodies_map_to_typed_errors(status, body, expected):
    with pytest.raises(expected) as info:
        make(lambda *_: (status, body)).complete(PROMPT)
    if expected is RateLimit:
        assert info.value.retry_after == 20.0
    if expected is APIError:
        assert info.value.status == status
    assert SECRET not in str(info.value)


def test_missing_key_raises_not_configured_before_any_request():
    calls = []
    provider = OpenAIProvider(key_source=lambda: None, transport=lambda *a: calls.append(a) or (200, ok_body()))
    with pytest.raises(NotConfigured):
        provider.complete(PROMPT)
    assert calls == []


def test_key_is_absent_from_repr_and_str():
    provider = make(lambda *_: (200, ok_body()))
    assert SECRET not in repr(provider) and SECRET not in str(provider)


def test_resolve_api_key_prefers_env_then_keychain():
    assert resolve_api_key(env={"OPENAI_API_KEY": " from-env "}, keychain=lambda: "from-keychain") == "from-env"
    assert resolve_api_key(env={}, keychain=lambda: "from-keychain") == "from-keychain"
    assert resolve_api_key(env={"OPENAI_API_KEY": ""}, keychain=lambda: None) is None
