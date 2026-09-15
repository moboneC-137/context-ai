"""Client for the Swift `capture-spike` binary — the single Swift/Python boundary (docs/capture-contract.md v1)."""

from .client import (
    AccessibilityNotGranted,
    CaptureClient,
    CaptureOptions,
    CaptureOutcome,
    CaptureSpikeError,
    CaptureTimeout,
    UsageError,
)
from .gate import SelectionGate
from .model import Bounds, BoundsSource, CaptureResult, TierAttempt

__all__ = [
    "AccessibilityNotGranted",
    "Bounds",
    "BoundsSource",
    "CaptureClient",
    "CaptureOptions",
    "CaptureOutcome",
    "CaptureResult",
    "CaptureSpikeError",
    "CaptureTimeout",
    "SelectionGate",
    "TierAttempt",
    "UsageError",
]
