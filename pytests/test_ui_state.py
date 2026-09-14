"""FR-24: every failure state is reachable as data, and retryable ones carry the text to retry on."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from contextai.actions import ActionEngine, MissingParameter, load_templates
from contextai.capture import (
    AccessibilityNotGranted,
    CaptureResult,
    CaptureSpikeError,
    CaptureTimeout,
    TierAttempt,
)
from contextai.providers import (
    APIError,
    MockProvider,
    NoNetwork,
    NotConfigured,
    RateLimit,
    SelectionTooLong,
    Timeout,
)
from contextai.ui import (
    Error,
    ErrorKind,
    Result,
    state_for_capture_miss,
    state_for_exception,
    state_for_result,
)

TS = datetime(2026, 9, 14, tzinfo=timezone.utc)


def miss(error: str, attempts=()) -> CaptureResult:
    return CaptureResult(
        ts=TS,
        app="com.apple.Safari",
        tier=None,
        secure_input=False,
        attempts=tuple(attempts),
        total_ms=12.0,
        error=error,
    )


def test_capture_miss_states():
    assert state_for_capture_miss(miss("no-selection")).kind is ErrorKind.NOTHING_SELECTED
    assert state_for_capture_miss(miss("no-frontmost-app")).kind is ErrorKind.CAPTURE_FAILED
    failed = state_for_capture_miss(
        miss("exhausted", [TierAttempt(1, False, 3.0, error="empty"), TierAttempt(2, False, 40.0, error="no-change")])
    )
    assert failed.kind is ErrorKind.CAPTURE_FAILED
    assert "com.apple.Safari" in failed.message and "tier 2: no-change" in failed.message
    assert "support-tier table" in failed.message
    assert not failed.retryable


@pytest.mark.parametrize(
    ("exc", "kind", "retryable"),
    [
        (AccessibilityNotGranted("Visual Studio Code", "stderr"), ErrorKind.NOT_SET_UP, False),
        (CaptureTimeout("no line"), ErrorKind.CAPTURE_FAILED, False),
        (CaptureSpikeError("boom"), ErrorKind.INTERNAL, False),
        (SelectionTooLong(9000, 8000), ErrorKind.TOO_LONG, False),
        (NotConfigured("no OpenAI API key"), ErrorKind.NOT_SET_UP, False),
        (NoNetwork("offline"), ErrorKind.NO_NETWORK, True),
        (Timeout("slow"), ErrorKind.TIMEOUT, True),
        (RateLimit("429", retry_after=20), ErrorKind.RATE_LIMIT, True),
        (APIError("HTTP 500", status=500), ErrorKind.API_ERROR, True),
        (MissingParameter("translate", ["target_language"]), ErrorKind.NOT_SET_UP, False),
        (RuntimeError("weird"), ErrorKind.INTERNAL, False),
    ],
    ids=lambda x: type(x).__name__ if isinstance(x, BaseException) else str(x),
)
def test_every_exception_maps_to_a_visible_state(exc, kind, retryable):
    state = state_for_exception(exc, selection="the text", action="summarize")
    assert isinstance(state, Error)
    assert state.kind is kind
    assert state.retryable is retryable
    assert state.message  # never blank


def test_messages_carry_what_the_user_needs():
    assert "Visual Studio Code" in state_for_exception(AccessibilityNotGranted("Visual Studio Code", "")).message
    assert "9,000" in state_for_exception(SelectionTooLong(9000, 8000)).message
    assert "20 s" in state_for_exception(RateLimit("x", retry_after=20)).message
    assert "target language" in state_for_exception(MissingParameter("translate", ["target_language"])).message


def test_retryable_state_keeps_the_captured_text_and_action():
    state = state_for_exception(Timeout("slow"), selection="same text", action="translate")
    assert (state.selection, state.action) == ("same text", "translate")


def test_result_state_and_same_language_message():
    engine = ActionEngine(MockProvider(), load_templates())
    result = engine.run("summarize", "Some text")
    state = state_for_result(result, "Some text", "Chinese")
    assert isinstance(state, Result) and state.display_text == "[mock] Some text"

    engine.provider.scripted.append("<<SAME_LANGUAGE>>")
    result = engine.run("translate", "Already Chinese", {"target_language": "Chinese"})
    state = state_for_result(result, "Already Chinese", "Chinese")
    assert state.same_language and state.display_text == "The selection is already in Chinese."
