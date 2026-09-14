"""Contract obligations of `contextai.capture.client`, exercised against `fake_capture_spike.py`."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from contextai.capture import (
    AccessibilityNotGranted,
    BoundsSource,
    CaptureClient,
    CaptureOptions,
    CaptureSpikeError,
    CaptureTimeout,
    UsageError,
)

FAKE = Path(__file__).with_name("fake_capture_spike.py")


@pytest.fixture
def fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Returns a factory: fake(mode, linger=0) -> (client, args_file, marker_file)."""

    def make(mode: str = "hit", linger: float = 0.0, *, first_line_timeout: float = 3.0):
        args_file = tmp_path / f"{mode}.args"
        marker = tmp_path / f"{mode}.marker"
        monkeypatch.setenv("FAKE_MODE", mode)
        monkeypatch.setenv("FAKE_LINGER", str(linger))
        monkeypatch.setenv("FAKE_ARGS", str(args_file))
        monkeypatch.setenv("FAKE_MARKER", str(marker))
        client = CaptureClient([sys.executable, str(FAKE)], first_line_timeout=first_line_timeout)
        return client, args_file, marker

    return make


# --- Obligation 1: always `--once --max-text 0` plus policy flags ------------------------------------


def test_default_options_pass_once_and_max_text_zero(fake):
    client, args_file, _ = fake("hit")
    outcome = client.capture()
    outcome.wait_settled()
    argv = args_file.read_text().split("\n")
    assert argv == ["--once", "--max-text", "0", "--tiers", "1,2,3"]


def test_policy_flags_are_forwarded(fake):
    client, args_file, _ = fake("hit")
    client.capture(CaptureOptions(tiers=(3, 1), enhanced_ax=False, tier1_retries=2)).wait_settled()
    argv = args_file.read_text().split("\n")
    assert argv[:3] == ["--once", "--max-text", "0"]
    assert argv[3:5] == ["--tiers", "1,3"]  # sorted, deduplicated — the binary runs ascending anyway
    assert "--no-enhanced-ax" in argv
    assert argv[argv.index("--tier1-retries") + 1] == "2"


def test_max_text_cannot_be_overridden():
    # There is no knob for it: the contract fixes `--max-text 0`, so CaptureOptions has no such field.
    with pytest.raises(TypeError):
        CaptureOptions(max_text=200)  # type: ignore[call-arg]


@pytest.mark.parametrize("bad", [(), (0,), (4,), (1, 2, 5)])
def test_invalid_tiers_rejected(bad):
    with pytest.raises(ValueError):
        CaptureOptions(tiers=bad)


# --- Obligation 2: first line without waiting for exit; never kill after a line --------------------


def test_returns_after_first_line_while_process_lingers(fake):
    client, _, marker = fake("hit", linger=0.8)
    started = time.perf_counter()
    outcome = client.capture()
    elapsed = time.perf_counter() - started

    assert outcome.result.is_hit
    assert elapsed < 0.6, f"capture() waited for exit ({elapsed:.2f}s) instead of returning on the first line"
    assert not outcome.settled
    assert outcome.exit_code is None
    assert not marker.exists(), "process should still be inside its linger window"


def test_lingering_process_is_not_killed_and_exits_naturally(fake):
    client, _, marker = fake("hit", linger=0.5)
    outcome = client.capture()
    code = outcome.wait_settled(timeout=3.0)
    assert code == 0
    assert outcome.settled
    assert marker.exists(), "marker is written only on a natural exit — the process was killed"
    assert "status text" in outcome.stderr  # stderr is captured, not parsed


def test_miss_is_a_result_not_an_error(fake):
    client, _, _ = fake("miss")
    outcome = client.capture()
    result = outcome.result
    assert not result.is_hit
    assert result.tier is None
    assert result.error == "exhausted"
    assert [a.tier for a in result.attempts] == [1, 2, 3]
    assert result.attempts[0].role == "AXWebArea"
    assert result.attempts[0].error == "empty"
    assert outcome.wait_settled() == 1


def test_no_selection_gate_line(fake):
    client, _, _ = fake("no-selection")
    outcome = client.capture()
    assert outcome.result.error == "no-selection"
    assert outcome.result.attempts[0].error == "no-selection"
    assert outcome.wait_settled() == 1


# --- Obligation 3: spawn cost measured separately from Swift-side totalMs -------------------------


def test_spawn_ms_is_recorded_and_distinct_from_total_ms(fake):
    client, _, _ = fake("hit")
    outcome = client.capture()
    assert outcome.spawn_ms > 0
    assert outcome.result.total_ms == 6.8  # comes from the line, untouched
    outcome.wait_settled()


# --- Obligation 4: exit 2 surfaces the host app; other no-line exits are typed ---------------------


def test_exit_2_raises_with_host_app(fake):
    client, _, _ = fake("exit2")
    with pytest.raises(AccessibilityNotGranted) as excinfo:
        client.capture()
    assert excinfo.value.host_app == "Visual Studio Code"
    assert "Privacy & Security" in excinfo.value.stderr


def test_exit_2_without_the_accessibility_message_is_not_a_permission_error(fake):
    client, _, _ = fake("exit2-foreign")
    with pytest.raises(CaptureSpikeError) as excinfo:
        client.capture()
    assert not isinstance(excinfo.value, AccessibilityNotGranted)
    assert "exited 2" in str(excinfo.value)


def test_exit_64_raises_usage_error(fake):
    client, _, _ = fake("exit64")
    with pytest.raises(UsageError):
        client.capture()


def test_no_line_timeout_kills_and_raises(fake):
    client, _, marker = fake("hang", first_line_timeout=0.3)
    started = time.perf_counter()
    with pytest.raises(CaptureTimeout):
        client.capture()
    assert time.perf_counter() - started < 2.0
    assert not marker.exists()  # killed: the only case where killing is allowed


def test_garbage_line_is_a_contract_violation(fake):
    client, _, _ = fake("garbage")
    with pytest.raises(CaptureSpikeError):
        client.capture()


# --- Obligation 5: unknown keys ignored; model round-trips the contract's fields -------------------


def test_hit_model_fields(fake):
    client, _, _ = fake("hit")
    result = client.capture().result
    assert result.app == "com.apple.Notes"
    assert result.tier == 1
    assert result.text == "Select text anywhere…"
    assert result.text_length == 21
    assert not result.truncated
    assert result.bounds is not None and (result.bounds.x, result.bounds.h) == (312.0, 18.0)
    assert result.bounds_source is BoundsSource.RANGE
    assert result.bounds_ms == 1.2
    assert result.secure_input is False
    assert result.ts.isoformat() == "2026-09-12T23:40:01+00:00"
    assert result.raw["futureKey"] == {"ignored": True}  # kept, not rejected


def test_for_binary_requires_existing_file(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        CaptureClient.for_binary(tmp_path / "missing")
