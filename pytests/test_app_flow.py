"""`App` flow without AppKit: `capturing` is held until the child has settled, release is tied to the
child alone, and `stop()` is orderly (spec-retro-2-clipboard-safety).

The app is driven through `_begin_capture` / `_captured` / `on_action` / `_finished` / `on_dismiss` /
`stop`; the panel is a fake, the client returns scripted outcomes or raises, and `_call_after` (the
`AppHelper.callAfter` hop to the main thread) runs synchronously and records what it was asked to run.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

from contextai import app as app_module
from contextai.actions import ActionEngine, load_templates
from contextai.app import App
from contextai.capture import CaptureClient, CaptureResult, CaptureSpikeError, CaptureTimeout
from contextai.policy import CapturePolicy
from contextai.providers import MockProvider
from contextai.ui import Actions, Error, ErrorKind, Loading, Rect

FAKE = Path(__file__).with_name("fake_capture_spike.py")

HIT = {
    "app": "com.apple.Safari",
    "attempts": [{"ms": 4.1, "ok": True, "tier": 2}],
    "bounds": {"h": 0.0, "w": 0.0, "x": 312.0, "y": 544.5},
    "boundsMs": 1.2,
    "boundsSource": "mouse",
    "secureInput": False,
    "text": "Select text anywhere…",
    "textLength": 21,
    "tier": 2,
    "totalMs": 6.8,
    "ts": "2026-09-12T23:40:01Z",
}


# --- fakes ------------------------------------------------------------------------------------------


class FakeHandle:
    """Stands in for the client's `_Reaper`: `settled` flips when the test says so."""

    def __init__(self, settled: bool = False, line_seen: bool = True, stderr: str = "") -> None:
        self._exited = threading.Event()
        if settled:
            self._exited.set()
        self.line_seen = line_seen
        self.stderr = stderr
        self.killed = False
        self.exit_code = 0

    @property
    def settled(self) -> bool:
        return self._exited.is_set()

    def wait_settled(self, timeout: float | None = 2.0) -> int | None:
        self._exited.wait(timeout)
        return self.exit_code if self.settled else None

    def settle(self, exit_code: int = 0) -> None:
        self.exit_code = exit_code
        self._exited.set()

    def kill_if_silent(self) -> None:
        if not self.settled and not self.line_seen:
            self.killed = True
            self.settle(-9)


class FakeOutcome:
    """The `CaptureOutcome` surface `App` uses: `result`, `spawn_ms`, `settled`, `wait_settled`."""

    def __init__(self, result: CaptureResult, handle: FakeHandle) -> None:
        self.result = result
        self.spawn_ms = 42.0
        self.handle = handle

    @property
    def settled(self) -> bool:
        return self.handle.settled

    def wait_settled(self, timeout: float | None = 2.0) -> int | None:
        return self.handle.wait_settled(timeout)


class FakeClient:
    first_line_timeout = 0.3

    def __init__(self, *script) -> None:
        self.script = list(script)
        self.calls = 0

    def capture(self, options):
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakePanel:
    def __init__(self) -> None:
        self.visible = False
        self.presented: list = []
        self.updated: list = []
        self.dismissed = 0
        self.fail_present = False

    def present(self, state, frame_for) -> None:
        if self.fail_present:
            raise RuntimeError("panel exploded")
        frame_for((240.0, 120.0))
        self.presented.append(state)
        self.visible = True

    def update(self, state) -> None:
        self.updated.append(state)

    def dismiss(self) -> None:
        self.dismissed += 1
        self.visible = False

    def contains(self, point) -> bool:
        return 0 <= point[0] < 100 and 0 <= point[1] < 100


class FakeMonitor:
    def __init__(self) -> None:
        self.stopped = 0

    def stop(self) -> None:
        self.stopped += 1


class FlowApp(App):
    """`_present` needs `NSScreen`; here the panel gets a fixed frame instead."""

    def _present(self, state) -> None:
        self.panel.present(state, lambda size: Rect(0, 0, *size))


@pytest.fixture
def calls(monkeypatch):
    """`_call_after` runs synchronously and records the callables it was given."""
    record: list = []

    def call_after(fn, *args):
        record.append(fn.__name__)
        fn(*args)

    monkeypatch.setattr(app_module, "_call_after", call_after)
    return record


@pytest.fixture
def make_app(calls):
    def make(*script, panel: FakePanel | None = None):
        client = FakeClient(*script)
        log: list[str] = []
        engine = ActionEngine(MockProvider(), load_templates())
        app = FlowApp(client, engine, target_language="Chinese", log=log.append)
        app.panel = panel or FakePanel()
        app.client_calls = lambda: client.calls
        app.lines = log
        return app

    return make


def hit(settled: bool = False) -> FakeOutcome:
    return FakeOutcome(CaptureResult.from_json(HIT), FakeHandle(settled=settled))


def trigger(app: App, what: str = "hotkey") -> None:
    app._begin_capture(what, "com.apple.Safari", (10.0, 10.0))
    if app._worker is not None:
        app._worker.join(2.0)
        assert not app._worker.is_alive()


def wait_until(predicate, timeout: float = 1.0) -> bool:
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


# --- matrix rows ------------------------------------------------------------------------------------


def test_tier1_hit_settled_on_return_releases_at_once(make_app):
    app = make_app(hit(settled=True))
    trigger(app)
    assert app.capturing is False  # no settle thread was needed: released inside _captured
    assert isinstance(app.panel.presented[-1], Actions)
    assert app.selection == "Select text anywhere…"
    assert app.client_calls() == 1


def test_clipboard_tier_holds_capturing_until_the_child_settles(make_app, calls):
    outcome = hit()
    app = make_app(outcome)
    trigger(app)
    assert isinstance(app.panel.presented[-1], Actions)  # the result is shown at once
    assert app.capturing is True  # ... but the child still owns the pasteboard

    trigger(app, "hotkey")
    assert app.client_calls() == 1, "a second capture-spike was spawned inside the guard window"
    assert app.skipped_gestures == 1
    assert "child still settling" in app.lines[-1]

    outcome.handle.settle()
    assert wait_until(lambda: not app.capturing)
    assert calls[-1] == "_release"  # released through the main-thread hop
    assert app._pending is None

    trigger(app)
    assert app.client_calls() == 2  # free again


def test_skip_log_distinguishes_in_flight_from_settling(make_app):
    started = threading.Event()
    release = threading.Event()

    class BlockingClient(FakeClient):
        def capture(self, options):
            started.set()
            release.wait(2.0)
            return super().capture(options)

    app = make_app(hit(settled=True))
    app.client = BlockingClient(hit(settled=True))
    app._begin_capture("hotkey", "com.apple.Safari", (10.0, 10.0))
    assert started.wait(1.0)
    app._begin_capture("gesture", "com.apple.Safari", (10.0, 10.0))
    assert "capture in flight" in app.lines[-1]
    assert app.skipped_gestures == 1
    release.set()
    app._worker.join(2.0)
    assert app.capturing is False


def test_gesture_during_settling_is_skipped_after_the_exclusion_check(make_app):
    outcome = hit()
    app = make_app(outcome)
    app.policy = CapturePolicy(excluded=frozenset({"com.apple.Terminal"}))
    trigger(app)
    assert app.capturing is True

    app._begin_capture("gesture", "com.apple.Terminal", (10.0, 10.0), require_auto_appear=True)
    assert app.skipped_gestures == 0  # FR-12: excluded → dropped before the capturing check, not counted
    app._begin_capture("gesture", "com.apple.Safari", (10.0, 10.0), require_auto_appear=True)
    assert app.skipped_gestures == 1
    assert "gesture ignored, child still settling" in app.lines[-1]
    assert app.client_calls() == 1

    outcome.handle.settle()
    assert wait_until(lambda: not app.capturing)


def test_esc_does_not_release_the_hold(make_app):
    outcome = hit()
    app = make_app(outcome)
    trigger(app)
    app.on_dismiss()
    assert app.panel.dismissed == 1 and app.panel.visible is False
    assert app.capturing is True

    trigger(app)
    assert app.client_calls() == 1  # still skipped: release is tied to the child, not the panel
    assert app.skipped_gestures == 1

    outcome.handle.settle()
    assert wait_until(lambda: not app.capturing)


def test_presentation_failure_still_releases_once_settled(make_app):
    outcome = hit()
    panel = FakePanel()
    panel.fail_present = True
    app = make_app(outcome, panel=panel)
    trigger(app)  # _captured runs on the worker via the synchronous hop; it must not propagate
    assert any("capture handling failed" in line and "panel exploded" in line for line in app.lines)
    assert app.capturing is True
    outcome.handle.settle()
    assert wait_until(lambda: not app.capturing)


def test_capture_timeout_holds_capturing_until_the_handle_settles(make_app):
    handle = FakeHandle()
    app = make_app(CaptureTimeout("no line", handle=handle))
    trigger(app)
    state = app.panel.presented[-1]
    assert isinstance(state, Error) and state.kind is ErrorKind.CAPTURE_FAILED  # told at once
    assert app.capturing is True  # the child is still running: no second spawn
    assert app._pending is not None

    trigger(app)
    assert app.client_calls() == 1
    assert "child still settling" in app.lines[-1]

    handle.settle()
    assert wait_until(lambda: not app.capturing)


def test_other_capture_exception_without_a_child_releases_immediately(make_app):
    exc = CaptureSpikeError("spike broke")
    assert exc.handle is None  # exit 2 / 64 / crash: the child is already gone
    app = make_app(exc, hit(settled=True))
    trigger(app)
    assert app.capturing is False
    state = app.panel.presented[-1]
    assert isinstance(state, Error) and state.kind is ErrorKind.INTERNAL
    trigger(app)
    assert app.client_calls() == 2  # free at once


def test_garbage_line_error_with_a_child_holds_until_it_settles(make_app):
    handle = FakeHandle(line_seen=True)
    app = make_app(CaptureSpikeError("unparseable line", handle=handle))
    trigger(app)
    state = app.panel.presented[-1]
    assert isinstance(state, Error) and state.kind is ErrorKind.INTERNAL
    assert app.capturing is True  # the child that wrote garbage may still be inside its guard window
    trigger(app)
    assert app.client_calls() == 1
    handle.settle()
    assert wait_until(lambda: not app.capturing)


def test_late_child_resolution_is_logged(make_app):
    handle = FakeHandle(line_seen=False, stderr="capture-spike: Accessibility access is not granted.\nmore")
    app = make_app(CaptureTimeout("no line", handle=handle))
    trigger(app)
    handle.settle(2)
    assert wait_until(lambda: not app.capturing)
    line = next(line for line in app.lines if "late child settled" in line)
    assert "exit=2 killed=False line_seen=False" in line
    assert "stderr='capture-spike: Accessibility access is not granted.'" in line


def test_timeout_without_a_handle_releases_immediately(make_app):
    app = make_app(CaptureTimeout("no line"))
    trigger(app)
    assert app.capturing is False


def test_stale_captured_presents_nothing_but_still_releases(make_app):
    outcome = hit()
    app = make_app(outcome)
    app.capturing = True
    app.generation = 5
    app._captured(4, outcome, None, time.perf_counter())
    assert app.panel.presented == []
    assert app.capturing is True
    outcome.handle.settle()
    assert wait_until(lambda: not app.capturing)


def test_finished_after_dismiss_does_not_touch_the_panel(make_app):
    app = make_app(hit(settled=True))
    trigger(app)
    app.selection = "Select text anywhere…"
    app.panel.visible = True
    generation = app.generation
    app.on_dismiss()  # FR-14: the in-flight action is now stale
    app._finished(generation, "translate", "Select text anywhere…", object(), None)
    assert app.panel.updated == []
    app._finished(app.generation, "translate", "Select text anywhere…", None, RuntimeError("late"))
    assert app.panel.updated == []  # panel not visible either


def test_on_action_marks_loading_and_finishes(make_app):
    app = make_app(hit(settled=True))
    trigger(app)
    app.on_action("translate")
    assert isinstance(app.panel.updated[0], Loading)
    assert wait_until(lambda: len(app.panel.updated) == 2)
    assert type(app.panel.updated[1]).__name__ == "Result"


def test_gesture_over_the_panel_is_ignored_before_the_capturing_check(make_app):
    app = make_app(hit(settled=True))
    app.panel.visible = True
    app.on_selection_gesture((50.0, 50.0))
    assert app.client_calls() == 0
    assert app.skipped_gestures == 0


# --- stop() -----------------------------------------------------------------------------------------


def test_stop_before_run_and_twice_is_a_no_op(make_app):
    app = make_app()
    assert app.panel.visible is False
    started = time.perf_counter()
    app.stop()
    app.stop()
    assert time.perf_counter() - started < 0.1
    assert app.generation == 2
    assert app.panel.dismissed == 0  # nothing was visible


def test_stop_releases_monitors_dismisses_and_waits_for_the_child(make_app):
    outcome = hit()
    app = make_app(outcome)
    app.hotkey_monitor = FakeMonitor()
    app.selection_monitor = FakeMonitor()
    hotkey, selection = app.hotkey_monitor, app.selection_monitor
    trigger(app)
    assert app.panel.visible

    threading.Timer(0.2, outcome.handle.settle).start()
    started = time.perf_counter()
    app.stop()
    elapsed = time.perf_counter() - started
    assert 0.15 < elapsed < 1.0, f"stop() should wait for the child to settle, took {elapsed:.2f}s"
    assert hotkey.stopped == 1 and selection.stopped == 1
    assert app.hotkey_monitor is None and app.selection_monitor is None
    assert app.panel.dismissed == 1
    app.stop()  # idempotent
    assert hotkey.stopped == 1


def test_stop_wait_is_bounded_and_never_kills_a_child_that_spoke(make_app, monkeypatch):
    monkeypatch.setattr(app_module, "STOP_SETTLE_TIMEOUT", 0.2)
    handle = FakeHandle(line_seen=True)  # never settles: a child past its line is left alone
    app = make_app(CaptureTimeout("no line", handle=handle))
    trigger(app)
    started = time.perf_counter()
    app.stop()
    assert time.perf_counter() - started < 0.8
    assert not handle.settled and not handle.killed


def test_stop_kills_a_child_still_silent_after_the_wait(make_app, monkeypatch):
    # The reaper's 10 s kill dies with the process; a silent child in its own session would be orphaned.
    monkeypatch.setattr(app_module, "STOP_SETTLE_TIMEOUT", 0.2)
    handle = FakeHandle(line_seen=False)
    app = make_app(CaptureTimeout("no line", handle=handle))
    trigger(app)
    app.stop()
    assert handle.killed and handle.settled


def test_stop_joins_the_in_flight_worker_then_waits_for_settle(make_app):
    started = threading.Event()
    release = threading.Event()
    outcome = hit()

    class BlockingClient(FakeClient):
        def capture(self, options):
            started.set()
            release.wait(2.0)
            return super().capture(options)

    app = make_app()
    app.client = BlockingClient(outcome)
    app._begin_capture("hotkey", "com.apple.Safari", (10.0, 10.0))
    assert started.wait(1.0)
    threading.Timer(0.2, release.set).start()
    threading.Timer(0.4, outcome.handle.settle).start()
    began = time.perf_counter()
    app.stop()
    elapsed = time.perf_counter() - began
    assert elapsed > 0.35, f"stop() returned after {elapsed:.2f}s: it did not wait for the worker and the settle"
    assert not app._worker.is_alive()
    assert outcome.settled


@pytest.mark.parametrize(
    "pending",
    [hit(), CaptureTimeout("no line", handle=FakeHandle(line_seen=True))],
    ids=["outcome", "timeout-with-handle"],
)
def test_stop_reads_the_worker_result_without_the_main_thread_hop(make_app, monkeypatch, pending):
    # Inside `terminate:` no queued callAfter ever runs; stop() must still find the child to wait on.
    app = make_app(pending)
    monkeypatch.setattr(app_module, "_call_after", lambda fn, *args: None)  # the hop is dead
    app._begin_capture("hotkey", "com.apple.Safari", (10.0, 10.0))
    threading.Timer(0.2, pending.handle.settle).start()
    started = time.perf_counter()
    app.stop()
    assert 0.15 < time.perf_counter() - started < 1.0
    assert pending.settled


# --- acceptance: a real client and a lingering fake child ------------------------------------------


def test_lingering_child_blocks_a_second_spawn_then_frees_within_a_second(monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "hit")
    monkeypatch.setenv("FAKE_LINGER", "0.8")
    monkeypatch.delenv("FAKE_ARGS", raising=False)
    monkeypatch.delenv("FAKE_MARKER", raising=False)
    calls: list[str] = []
    monkeypatch.setattr(app_module, "_call_after", lambda fn, *args: (calls.append(fn.__name__), fn(*args)))

    log: list[str] = []
    app = FlowApp(
        CaptureClient([sys.executable, str(FAKE)]),
        ActionEngine(MockProvider(), load_templates()),
        target_language="Chinese",
        log=log.append,
    )
    app.panel = FakePanel()
    spawns = 0
    original = app.client.capture

    def counting(options):
        nonlocal spawns
        spawns += 1
        return original(options)

    app.client.capture = counting

    trigger(app)
    assert isinstance(app.panel.presented[-1], Actions)
    time.sleep(0.2)
    trigger(app)
    assert spawns == 1
    assert app.skipped_gestures == 1
    assert "child still settling" in log[-1]
    assert wait_until(lambda: not app.capturing, timeout=1.0)
    assert calls[-1] == "_release"
