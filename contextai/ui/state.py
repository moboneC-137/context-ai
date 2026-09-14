"""The Panel's states as plain data (PRD FR-24) and the mapping from every failure to one of them.

The AppKit panel only renders these; deciding *which* state a failure produces lives here so each state
is reachable in tests without a display. "Never a silent failure": every exception the app can see maps
to an `Error` with a message the user can act on.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Union

from ..actions import ActionEngineError, ActionResult, MissingParameter
from ..capture import (
    AccessibilityNotGranted,
    CaptureResult,
    CaptureSpikeError,
    CaptureTimeout,
)
from ..providers import (
    APIError,
    NoNetwork,
    NotConfigured,
    ProviderError,
    RateLimit,
    SelectionTooLong,
    Timeout,
)

SUPPORT_TABLE_HINT = "See the support-tier table in the README."


class ErrorKind(str, Enum):
    NOTHING_SELECTED = "nothing_selected"
    CAPTURE_FAILED = "capture_failed"
    NO_NETWORK = "no_network"
    API_ERROR = "api_error"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    TOO_LONG = "too_long"
    NOT_SET_UP = "not_set_up"
    INTERNAL = "internal"


@dataclass(frozen=True, slots=True)
class Actions:
    """Captured text is ready; offer the Actions."""

    selection: str
    app: str
    tier: int | None


@dataclass(frozen=True, slots=True)
class Loading:
    action: str
    selection: str


@dataclass(frozen=True, slots=True)
class Result:
    action: str
    text: str
    selection: str
    same_language: bool = False
    target_language: str | None = None

    @property
    def display_text(self) -> str:
        if self.same_language:
            target = self.target_language or "the target language"
            return f"The selection is already in {target}."
        return self.text


@dataclass(frozen=True, slots=True)
class Error:
    kind: ErrorKind
    message: str
    retryable: bool = False
    """Retry re-runs the last Action on the same captured text, never a new capture (FR-24)."""
    selection: str | None = None
    action: str | None = None


PanelState = Union[Actions, Loading, Result, Error]


def state_for_capture_miss(result: CaptureResult) -> Error:
    """A parsed JSON line with no text (exit 1): distinguish "nothing selected" from "could not read"."""
    if result.error == "no-selection":
        return Error(ErrorKind.NOTHING_SELECTED, "Nothing is selected.")
    if result.error == "no-frontmost-app":
        return Error(ErrorKind.CAPTURE_FAILED, "No application is in front.")
    tiers = ", ".join(f"tier {a.tier}: {a.error or 'ok'}" for a in result.attempts) or "no tier ran"
    return Error(
        ErrorKind.CAPTURE_FAILED,
        f"Could not read the selection in {result.app} ({tiers}). {SUPPORT_TABLE_HINT}",
    )


def state_for_exception(exc: BaseException, *, selection: str | None = None, action: str | None = None) -> Error:
    """Every exception the app catches, mapped to a Panel state. Order matters: subclasses first."""
    if isinstance(exc, AccessibilityNotGranted):
        who = exc.host_app or "this app"
        return Error(
            ErrorKind.NOT_SET_UP,
            f"Accessibility permission is missing for {who}. Grant it in System Settings › Privacy & Security › Accessibility.",
        )
    if isinstance(exc, CaptureTimeout):
        return Error(ErrorKind.CAPTURE_FAILED, f"The capture did not answer in time. {SUPPORT_TABLE_HINT}")
    if isinstance(exc, CaptureSpikeError):
        return Error(ErrorKind.INTERNAL, f"Capture failed: {exc}")

    if isinstance(exc, SelectionTooLong):
        return Error(
            ErrorKind.TOO_LONG,
            f"The selection is too long ({exc.length:,} characters; the limit is {exc.cap:,}). Select less text.",
            selection=selection,
            action=action,
        )
    if isinstance(exc, NotConfigured):
        return Error(
            ErrorKind.NOT_SET_UP, f"ContextAI is not set up: {exc.message}", selection=selection, action=action
        )
    if isinstance(exc, NoNetwork):
        return Error(ErrorKind.NO_NETWORK, "No network connection.", retryable=True, selection=selection, action=action)
    if isinstance(exc, Timeout):
        return Error(
            ErrorKind.TIMEOUT,
            "The provider did not answer in time.",
            retryable=True,
            selection=selection,
            action=action,
        )
    if isinstance(exc, RateLimit):
        wait = f" Try again in {exc.retry_after:g} s." if exc.retry_after else ""
        return Error(
            ErrorKind.RATE_LIMIT,
            f"The provider is rate-limiting requests.{wait}",
            retryable=True,
            selection=selection,
            action=action,
        )
    if isinstance(exc, APIError):
        return Error(
            ErrorKind.API_ERROR,
            f"The provider returned an error: {exc.message}",
            retryable=True,
            selection=selection,
            action=action,
        )
    if isinstance(exc, ProviderError):
        return Error(ErrorKind.API_ERROR, exc.message, retryable=exc.retryable, selection=selection, action=action)

    if isinstance(exc, MissingParameter):
        return Error(
            ErrorKind.NOT_SET_UP,
            f"ContextAI is not set up: choose a target language ({', '.join(exc.names)}).",
            selection=selection,
            action=action,
        )
    if isinstance(exc, ActionEngineError):
        return Error(ErrorKind.INTERNAL, str(exc), selection=selection, action=action)
    return Error(
        ErrorKind.INTERNAL, f"Unexpected error: {type(exc).__name__}: {exc}", selection=selection, action=action
    )


def state_for_result(result: ActionResult, selection: str, target_language: str | None) -> Result:
    return Result(
        action=result.action,
        text=result.text,
        selection=selection,
        same_language=result.same_language,
        target_language=target_language,
    )
