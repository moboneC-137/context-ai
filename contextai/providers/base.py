"""Provider protocol and the typed errors every Action outcome is expressed in (PRD FR-20, FR-24).

The Panel and the Action Engine only ever see `Prompt`, `Completion` and `ProviderError` subclasses;
vendor SDKs, endpoints and status codes stop at the concrete provider module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class Prompt:
    """One fully rendered request: the template's instructions plus the user's Selection."""

    system: str
    user: str
    max_output_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class Completion:
    """What a provider returns on success. `model` is the identifier the provider actually used."""

    text: str
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class ProviderError(Exception):
    """Base of the typed error set. `retryable` drives FR-24's retry affordance on the same text."""

    retryable: bool = False

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NoNetwork(ProviderError):
    """The endpoint could not be reached at all (DNS, connection refused, offline)."""

    retryable = True


class Timeout(ProviderError):
    """The endpoint was reached but did not answer within the deadline."""

    retryable = True


class RateLimit(ProviderError):
    """HTTP 429 or a vendor-specific quota signal. `retry_after` is seconds when the vendor says."""

    retryable = True

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class APIError(ProviderError):
    """Any other vendor-side failure. Retryable per FR-24; the Panel decides how often."""

    retryable = True

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class NotConfigured(ProviderError):
    """No API key (or nothing else the provider needs). Maps to the Panel's "app not set up" state."""

    retryable = False


class SelectionTooLong(ProviderError):
    """The Selection exceeds the size cap (FR-23). Raised by the engine before any request is made."""

    retryable = False

    def __init__(self, length: int, cap: int) -> None:
        super().__init__(f"selection is {length} characters; the cap is {cap}")
        self.length = length
        self.cap = cap


@runtime_checkable
class Provider(Protocol):
    """Everything above this line is vendor-agnostic; everything below it is one module per vendor."""

    @property
    def name(self) -> str: ...

    def complete(self, prompt: Prompt) -> Completion:
        """Runs one request. Raises a `ProviderError` subclass on every failure — never a raw exception."""
        ...
