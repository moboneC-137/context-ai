"""Capture diagnostics (PRD FR-9, NFR-4): one JSON line of *metadata* per capture, never the text.

Replaces the spike's `--out` file. The record keeps every Swift-side field except `text` (and the raw
line), adds the Python-side `spawnMs` and the policy verdict, and is what `tools/matrix_report.py` reads.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .capture import CaptureOutcome, CaptureResult

DEFAULT_PATH = Path.home() / "Library" / "Logs" / "ContextAI" / "captures.jsonl"
REDACTED_KEYS = frozenset({"text"})


def record_for(
    result: CaptureResult, *, spawn_ms: float | None = None, extra: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """The Swift line minus user content, plus Python-side fields. Unknown Swift keys pass through."""
    record = {k: v for k, v in result.raw.items() if k not in REDACTED_KEYS}
    record["hit"] = result.is_hit
    if result.error is not None:
        record["error"] = result.error  # the policy may have overwritten the Swift value
        record["tier"] = result.tier
    if spawn_ms is not None:
        record["spawnMs"] = round(spawn_ms, 1)
    if extra:
        record.update(extra)
    return record


@dataclass
class DiagnosticsLog:
    path: Path = DEFAULT_PATH

    def record(
        self,
        outcome: CaptureOutcome | None = None,
        *,
        result: CaptureResult | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if outcome is not None:
            result = result or outcome.result
            spawn_ms = outcome.spawn_ms
        else:
            spawn_ms = None
        if result is None:
            raise ValueError("record() needs an outcome or a result")
        entry = record_for(result, spawn_ms=spawn_ms, extra=extra)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        return entry
