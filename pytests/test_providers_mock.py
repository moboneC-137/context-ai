"""FR-22: the Mock Provider is deterministic and can be forced into every typed error state."""

from __future__ import annotations

import pytest

from contextai.providers import (
    APIError,
    MockProvider,
    NoNetwork,
    NotConfigured,
    Prompt,
    Provider,
    ProviderError,
    RateLimit,
    SelectionTooLong,
    Timeout,
)

PROMPT = Prompt(system="sys", user="Hello there\nsecond line")

ALL_ERRORS: list[ProviderError] = [
    NoNetwork("offline"),
    Timeout("slow"),
    RateLimit("429", retry_after=2.0),
    APIError("500", status=500),
    NotConfigured("no key"),
    SelectionTooLong(10, 5),
]


def test_satisfies_the_provider_protocol():
    assert isinstance(MockProvider(), Provider)


def test_same_prompt_same_completion():
    mock = MockProvider()
    a, b = mock.complete(PROMPT), mock.complete(PROMPT)
    assert a == b
    assert a.text == "[mock] Hello there"
    assert a.provider == "mock" and a.model == "mock-1"
    assert mock.calls == [PROMPT, PROMPT]


def test_custom_responder_and_scripted_queue_take_precedence():
    mock = MockProvider(responder=lambda p: p.user.upper())
    assert mock.complete(PROMPT).text == "HELLO THERE\nSECOND LINE"
    mock.scripted.append("scripted first")
    assert mock.complete(PROMPT).text == "scripted first"
    assert mock.complete(PROMPT).text == "HELLO THERE\nSECOND LINE"


@pytest.mark.parametrize("error", ALL_ERRORS, ids=lambda e: type(e).__name__)
def test_every_typed_error_can_be_forced(error: ProviderError):
    mock = MockProvider(fail_with=error)
    with pytest.raises(type(error)) as info:
        mock.complete(PROMPT)
    assert info.value is error
    assert isinstance(info.value, ProviderError)


def test_fail_once_then_recover_models_the_retry_path():
    mock = MockProvider()
    mock.fail_once(Timeout("first try"))
    with pytest.raises(Timeout):
        mock.complete(PROMPT)
    assert mock.complete(PROMPT).text == "[mock] Hello there"
    assert len(mock.calls) == 2


def test_retryable_flags_follow_fr24():
    retryable = {type(e).__name__: e.retryable for e in ALL_ERRORS}
    assert retryable == {
        "NoNetwork": True,
        "Timeout": True,
        "RateLimit": True,
        "APIError": True,
        "NotConfigured": False,
        "SelectionTooLong": False,
    }
    assert RateLimit("x", retry_after=2.0).retry_after == 2.0
    assert SelectionTooLong(10, 5).cap == 5
