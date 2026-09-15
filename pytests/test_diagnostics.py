"""FR-9 / NFR-4: records are metadata only — never the text — and append as JSONL."""

from __future__ import annotations

import json
from pathlib import Path

from contextai.capture import CaptureResult
from contextai.diagnostics import DiagnosticsLog, record_for

RAW = {
    "app": "com.apple.Notes",
    "tier": 1,
    "text": "SECRET SELECTION",
    "textLength": 16,
    "bounds": {"x": 1, "y": 2, "w": 3, "h": 4},
    "boundsSource": "range",
    "boundsMs": 0.5,
    "secureInput": False,
    "attempts": [{"tier": 1, "ok": True, "ms": 4.0}],
    "totalMs": 5.0,
    "ts": "2026-09-14T10:00:00Z",
    "futureKey": "kept",
}


def result_from(raw: dict) -> CaptureResult:
    return CaptureResult.from_json(raw)


def test_record_drops_text_keeps_everything_else_and_adds_python_fields():
    record = record_for(result_from(RAW), spawn_ms=93.456, extra={"trigger": "hotkey"})
    assert "text" not in record and "SECRET" not in json.dumps(record)
    assert record["textLength"] == 16 and record["boundsSource"] == "range" and record["futureKey"] == "kept"
    assert record["hit"] is True and record["spawnMs"] == 93.5 and record["trigger"] == "hotkey"


def test_policy_overrides_win_over_the_raw_line():
    from dataclasses import replace

    downgraded = replace(result_from(RAW), tier=None, text=None, text_length=None, error="policy-empty-selection")
    record = record_for(downgraded)
    assert record["hit"] is False and record["tier"] is None and record["error"] == "policy-empty-selection"


def test_log_appends_jsonl_and_creates_directories(tmp_path: Path):
    log = DiagnosticsLog(tmp_path / "nested" / "captures.jsonl")
    log.record(result=result_from(RAW))
    log.record(result=result_from(RAW), extra={"n": 2})
    lines = log.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[1])["n"] == 2
    assert all("SECRET" not in line for line in lines)
