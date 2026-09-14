"""Deterministic provider for tests, demos and mock eval runs (PRD FR-22).

Determinism: the same `Prompt` always yields the same `Completion`. The default responder echoes a
fingerprint of the request; a `responder` callable or a `scripted` queue replaces it when a test or a
golden case needs specific text. `fail_with` forces any typed error, once or permanently, so every
FR-24 state is reachable without a network.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque

from .base import Completion, Prompt, ProviderError

Responder = Callable[[Prompt], str]


def echo_responder(prompt: Prompt) -> str:
    """Default output: recognizable, stable, and short enough to read in a panel."""
    head = prompt.user.strip().splitlines()[0] if prompt.user.strip() else ""
    return f"[mock] {head[:80]}"


@dataclass
class MockProvider:
    responder: Responder = echo_responder
    scripted: Deque[str | ProviderError] = field(default_factory=deque)
    """Consumed first, one per call: a string is returned, an error instance is raised."""
    fail_with: ProviderError | None = None
    """Raised on every call until cleared (`scripted` still takes precedence)."""
    model: str = "mock-1"
    calls: list[Prompt] = field(default_factory=list)
    """Every prompt received, in order — the assertion surface for engine tests."""

    @property
    def name(self) -> str:
        return "mock"

    def complete(self, prompt: Prompt) -> Completion:
        self.calls.append(prompt)
        if self.scripted:
            step = self.scripted.popleft()
            if isinstance(step, ProviderError):
                raise step
            return self._completion(step)
        if self.fail_with is not None:
            raise self.fail_with
        return self._completion(self.responder(prompt))

    def fail_once(self, error: ProviderError) -> None:
        """Queue one failure; the next call raises it and the one after behaves normally (retry tests)."""
        self.scripted.append(error)

    def _completion(self, text: str) -> Completion:
        # Token counts are a vendor concept; the mock reports none rather than a made-up number.
        return Completion(text=text, provider=self.name, model=self.model)
