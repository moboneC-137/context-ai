"""Typed mirror of one `capture-spike` JSON line (docs/capture-contract.md v1).

Field names, presence rules and vocabularies follow the contract exactly. Unknown keys are ignored so
additive Swift-side fields never break the client.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

CONTRACT_VERSION = 1


class BoundsSource(str, Enum):
    """Where `bounds` came from, best first."""

    RANGE = "range"  # AXSelectedTextRange → AXBoundsForRange: accurate
    FRAME = "frame"  # focused element's AXFrame, accepted only if plausible
    MOUSE = "mouse"  # pointer location; zero-size rect


@dataclass(frozen=True, slots=True)
class Bounds:
    """Selection rect in NSScreen coordinates: origin at the bottom-left of the primary display, points."""

    x: float
    y: float
    w: float
    h: float

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "Bounds":
        return cls(x=float(data["x"]), y=float(data["y"]), w=float(data["w"]), h=float(data["h"]))


@dataclass(frozen=True, slots=True)
class TierAttempt:
    """One tier's outcome inside a single capture."""

    tier: int
    ok: bool
    ms: float
    error: str | None = None
    retries: int | None = None
    ax_error: int | None = None
    role: str | None = None

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "TierAttempt":
        return cls(
            tier=int(data["tier"]),
            ok=bool(data["ok"]),
            ms=float(data["ms"]),
            error=data.get("error"),
            retries=data.get("retries"),
            ax_error=data.get("axError"),
            role=data.get("role"),
        )


@dataclass(frozen=True, slots=True)
class CaptureResult:
    """One JSON line from `capture-spike`.

    `total_ms` is the Swift-side chain time only; the spawn cost lives on `CaptureOutcome`, not here.
    """

    ts: datetime
    app: str
    tier: int | None
    secure_input: bool
    attempts: tuple[TierAttempt, ...]
    total_ms: float
    text: str | None = None
    text_length: int | None = None
    bounds: Bounds | None = None
    bounds_source: BoundsSource | None = None
    bounds_ms: float | None = None
    error: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)

    @property
    def is_hit(self) -> bool:
        return self.text is not None

    @property
    def truncated(self) -> bool:
        """Best-effort truncation signal (`--max-text` > 0 appends `…`).

        The client always passes `--max-text 0`, so this should never be true; it is kept as a guard.
        Swift counts grapheme clusters and Python counts code points, so the comparison can miss
        truncation in text dense with combining characters — never claim completeness from it.
        """
        if self.text is None or self.text_length is None:
            return False
        return len(self.text) < self.text_length

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "CaptureResult":
        bounds = data.get("bounds")
        source = data.get("boundsSource")
        return cls(
            ts=_parse_ts(data["ts"]),
            app=str(data["app"]),
            tier=data.get("tier"),
            secure_input=bool(data["secureInput"]),
            attempts=tuple(TierAttempt.from_json(a) for a in data.get("attempts", [])),
            total_ms=float(data["totalMs"]),
            text=data.get("text"),
            text_length=data.get("textLength"),
            bounds=Bounds.from_json(bounds) if bounds is not None else None,
            bounds_source=BoundsSource(source) if source is not None else None,
            bounds_ms=data.get("boundsMs"),
            error=data.get("error"),
            raw=dict(data),
        )


def _parse_ts(value: str) -> datetime:
    # Foundation's .iso8601 strategy emits `2026-09-12T23:40:01Z`; Python 3.11+ accepts the trailing Z.
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
