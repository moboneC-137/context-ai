"""Main loop: hotkey → capture → Panel → Action → Provider → render (docs/swift-python-split §3).

    uv run python -m contextai.settings set target_language Chinese   # once; then:
    uv run python -m contextai.app --provider mock
    uv run python -m contextai.app --provider openai --target-language English --hotkey ctrl+alt+space
    uv run python -m contextai.app --provider mock --auto-appear --exclude com.apple.Terminal

Every flag is optional and overrides the settings file (`contextai.settings`: `--settings PATH` >
`$CONTEXTAI_SETTINGS` > `~/Library/Application Support/ContextAI/settings.toml`); `--exclude` adds to
the file's `excluded_apps`. The target language has no default: it must come from the file or the
flag, else exit 2 (PRD FR-25 / Open Question 1).

Threading: AppKit owns the main thread. The capture spawn (~90 ms) and the provider request run on
worker threads and hand their outcome back with `AppHelper.callAfter`; a generation counter makes a
result that arrives after Esc (or after a newer hotkey press) a no-op (FR-14). Diagnostics printed
here are metadata only — never the selection or the result (NFR-4).

Clipboard safety across the boundary (docs/capture-contract.md, "Process behaviour"): `capturing`
stays true until the child that last owned the pasteboard has *settled* — not merely answered — so a
second `capture-spike` is never spawned while the first may still revert a late copy. That hold is
tied to the child alone: neither Esc nor a newer generation releases it. A `CaptureTimeout` carries
the still-running child's handle and is held the same way. Ctrl-C: the child lives in its own session
(never signalled) and the application delegate runs `stop()`, which waits (bounded) for the child.
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from .actions import DEFAULT_SELECTION_CAP, ActionEngine, load_templates
from .capture import SETTLE_TIMEOUT_SECONDS, CaptureClient, CaptureOptions, CaptureOutcome, CaptureSpikeError
from .diagnostics import DiagnosticsLog
from .input import DEFAULT_HOTKEY, HotkeyMonitor, SelectionMonitor, parse_hotkey
from .policy import CapturePolicy
from .providers import MockProvider, OpenAIProvider, Provider
from .settings import MISSING_LANGUAGE_MESSAGE, PROVIDERS, SettingsError, effective, load, resolve_path
from .ui import (
    Actions,
    Loading,
    Rect,
    place_panel,
    state_for_capture_miss,
    state_for_exception,
    state_for_result,
)

DEFAULT_BINARY = Path(__file__).resolve().parents[1] / ".build" / "release" / "capture-spike"

# `stop()` waits this long, at most, for a child past its first line to leave its guard window.
STOP_SETTLE_TIMEOUT = SETTLE_TIMEOUT_SECONDS


def _call_after(fn: Callable[..., None], *args) -> None:
    """Run `fn(*args)` on the main thread from a worker. Tests patch this to run synchronously."""
    from PyObjCTools import AppHelper

    AppHelper.callAfter(fn, *args)


class App:
    def __init__(
        self,
        client: CaptureClient,
        engine: ActionEngine,
        *,
        target_language: str,
        hotkey: str = DEFAULT_HOTKEY,
        policy: CapturePolicy | None = None,
        diagnostics: DiagnosticsLog | None = None,
        auto_appear: bool = False,
        log: Callable[[str], None] = lambda line: print(line, file=sys.stderr, flush=True),
    ) -> None:
        self.client = client
        self.engine = engine
        self.target_language = target_language
        self.hotkey = parse_hotkey(hotkey)
        self.policy = policy or CapturePolicy()
        self.diagnostics = diagnostics
        self.auto_appear = auto_appear
        self.log = log
        self.skipped_gestures = 0
        self.generation = 0
        self.capturing = False
        self.selection: str | None = None
        self.last_action: str | None = None
        self.anchor = None  # Bounds | None from the last capture
        self.mouse: tuple[float, float] = (0.0, 0.0)
        self.panel = None  # created lazily on the main thread, inside run()
        self.hotkey_monitor: HotkeyMonitor | None = None
        self.selection_monitor: SelectionMonitor | None = None
        self._delegate = None  # NSApplication delegate, retained for the life of the run loop
        self._worker: threading.Thread | None = None  # the in-flight capture worker
        # The child that last owned the pasteboard (a CaptureOutcome, or a CaptureSpikeError carrying
        # a handle), from the worker's return until it has settled; None otherwise.
        self._pending: CaptureOutcome | CaptureSpikeError | None = None
        self._settling = False  # True between the child's answer and its settle (for the skip log)

    # --- lifecycle --------------------------------------------------------------------------------

    def run(self) -> None:
        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory, NSObject, NSTerminateNow
        from PyObjCTools import AppHelper

        from .ui.panel import Panel

        stop = self.stop
        log = self.log

        class ContextAIAppDelegate(NSObject):
            # Ctrl-C ends in `NSApp.terminate_` (PyObjC's Mach interrupt handler), which calls this on
            # the main thread and then exits the process: nothing after `runEventLoop` ever runs, so
            # the orderly stop lives here. Queued `callAfter` callbacks will never fire at this point;
            # `stop()` waits on the child directly.
            def applicationShouldTerminate_(self, sender):
                log("contextai: terminate requested — stopping (a capture in flight finishes first)")
                try:
                    stop()
                except Exception:  # noqa: BLE001 — never let a Python error escape into the ObjC call
                    import traceback

                    log("contextai: stop failed\n" + traceback.format_exc().rstrip())
                return NSTerminateNow

        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)  # no Dock icon, never frontmost
        self._delegate = ContextAIAppDelegate.alloc().init()
        app.setDelegate_(self._delegate)
        self.panel = Panel(on_action=self.on_action, on_retry=self.on_retry, on_dismiss=self.on_dismiss)
        self.hotkey_monitor = HotkeyMonitor(self.hotkey, self.on_hotkey, log=self.log)
        self.hotkey_monitor.start()
        if self.auto_appear:
            self.selection_monitor = SelectionMonitor(self.on_selection_gesture)
            self.selection_monitor.start()
        mode = " · auto-appear on" if self.auto_appear else ""
        self.log(f"contextai: ready — press {self.hotkey.spec} on a selection{mode} (Ctrl-C here to quit)")
        # Route SIGINT through a Mach port so it is handled while the run loop is idle (Python's own
        # handler only runs when Python code does). Installed explicitly: `runEventLoop(installInterrupt=
        # True)` skips it whenever `sharedApplication()` already exists, as it does here. The handler
        # ends in `NSApp.terminate_` → the delegate above → `exit()`. SIGTERM (`kill <pid>`, an IDE's
        # stop button) takes the same road.
        from PyObjCTools import MachSignals

        AppHelper.installMachInterrupt()
        MachSignals.signal(signal.SIGTERM, AppHelper.machInterrupt)
        try:
            AppHelper.runEventLoop()
        finally:
            self.stop()  # the exception path; the Ctrl-C path went through the delegate

    def stop(self) -> None:
        """Orderly shutdown: no new captures, monitors off, panel gone, the child's restore finished.

        Framework-free and idempotent. Joins the in-flight worker (`capture()` can take up to about
        2 × `first_line_timeout` on its EOF path), then waits up to `STOP_SETTLE_TIMEOUT` for the child
        that last owned the pasteboard: ~2 s in the common case, a few seconds at worst. A child that
        has produced a line is never killed. A child still silent after that wait is killed — the one
        kill the contract allows — because the reaper that would do it later dies with this process
        and the child, in its own session, would otherwise be orphaned.
        """
        self.generation += 1  # a later _captured presents nothing (release still runs); _finished is a no-op
        if self.hotkey_monitor is not None:
            self.hotkey_monitor.stop()
            self.hotkey_monitor = None
        if self.selection_monitor is not None:
            self.selection_monitor.stop()
            self.selection_monitor = None
        if self.panel is not None and self.panel.visible:
            self.panel.dismiss()
        worker = self._worker
        if worker is not None and worker.is_alive():
            worker.join(2 * self.client.first_line_timeout + 1.0)
        pending = self._pending
        if pending is not None and not pending.settled:
            pending.wait_settled(STOP_SETTLE_TIMEOUT)
            handle = getattr(pending, "handle", None)
            if handle is not None and not handle.settled and not handle.line_seen:
                handle.kill_if_silent()

    # --- hotkey → capture -------------------------------------------------------------------------

    def on_hotkey(self) -> None:
        """FR-10: the universal path — works in excluded apps too."""
        self._start_capture("hotkey", require_auto_appear=False)

    def on_selection_gesture(self, point: tuple[float, float]) -> None:
        """FR-11: a qualifying gesture from the SelectionMonitor. Ignored over our own panel (FR-11)."""
        if self.panel is not None and self.panel.visible and self.panel.contains(point):
            return
        self._start_capture("gesture", require_auto_appear=True)

    def _start_capture(self, trigger: str, *, require_auto_appear: bool) -> None:
        from AppKit import NSEvent, NSWorkspace

        front = NSWorkspace.sharedWorkspace().frontmostApplication()
        bundle_id = front.bundleIdentifier() if front else None
        point = NSEvent.mouseLocation()
        self._begin_capture(
            trigger, bundle_id, (float(point.x), float(point.y)), require_auto_appear=require_auto_appear
        )

    def _begin_capture(
        self,
        trigger: str,
        bundle_id: str | None,
        point: tuple[float, float],
        *,
        require_auto_appear: bool = False,
    ) -> None:
        """FR-11/FR-12 gate plus the spawn; framework-free so the whole flow is testable without AppKit."""
        if require_auto_appear and not self.policy.auto_appear_allowed(bundle_id):
            return  # FR-12: excluded app, and this was not the hotkey
        if self.capturing:
            # FR-11: gestures during a capture are skipped and counted, never queued (this is why the
            # miss path must fit the latency budget). "Settling" = the child has answered but may still
            # own the pasteboard (late-copy guard); spawning now would let it revert the next copy.
            self.skipped_gestures += 1
            why = "child still settling" if self._settling else "capture in flight"
            self.log(f"contextai: {trigger} ignored, {why} (skipped so far: {self.skipped_gestures})")
            return
        self.generation += 1
        generation = self.generation
        self.capturing = True
        self._settling = False
        self._pending = None
        self.mouse = point
        options = self.policy.options_for(bundle_id)
        self.log(f"contextai: {trigger} frontmost={bundle_id} tiers={','.join(map(str, options.tiers))}")
        self._worker = threading.Thread(target=self._capture_worker, args=(generation, options), daemon=True)
        self._worker.start()

    def _capture_worker(self, generation: int, options: CaptureOptions) -> None:
        started = time.perf_counter()
        try:
            outcome = self.client.capture(options)
        except Exception as exc:  # noqa: BLE001 — every failure becomes a panel state
            # A timeout or a garbage line carries the still-running child; `stop()` reads it here
            # directly because a `callAfter` queued during termination never runs.
            self._pending = exc if getattr(exc, "handle", None) is not None else None
            _call_after(self._captured, generation, None, exc, started)
        else:
            self._pending = outcome
            _call_after(self._captured, generation, outcome, None, started)

    def _captured(
        self, generation: int, outcome: CaptureOutcome | None, exc: Exception | None, started: float
    ) -> None:
        # Release first, whatever happens below: `capturing` is tied to the child, never to the panel
        # or to the generation, and must not stay stuck if presenting raises.
        if outcome is not None:
            self._schedule_release(outcome)
        elif isinstance(exc, CaptureSpikeError) and exc.handle is not None:
            self._schedule_release(exc)
        else:
            self._release()
        try:
            self._show_capture(generation, outcome, exc, started)
        except Exception:  # noqa: BLE001 — a presentation failure must not take the app down
            import traceback

            self.log("contextai: capture handling failed\n" + traceback.format_exc().rstrip())

    def _show_capture(
        self, generation: int, outcome: CaptureOutcome | None, exc: Exception | None, started: float
    ) -> None:
        if generation != self.generation:
            return
        if exc is not None or outcome is None:
            self.log(f"contextai: capture error {type(exc).__name__}")
            self.anchor = None
            self._present(state_for_exception(exc))
            return

        result = self.policy.interpret(outcome.result)
        self.anchor = result.bounds
        if self.diagnostics is not None:
            try:
                self.diagnostics.record(outcome, result=result)
            except OSError as err:
                self.log(f"contextai: diagnostics not written: {err}")
        self.log(
            f"contextai: capture app={result.app} tier={result.tier} hit={result.is_hit} "
            f"swift={result.total_ms:.0f}ms spawn={outcome.spawn_ms:.0f}ms "
            f"total={(time.perf_counter() - started) * 1000:.0f}ms bounds={result.bounds_source.value if result.bounds_source else None}"
        )
        if not result.is_hit:
            self._present(state_for_capture_miss(result))
            return
        self.selection = result.text
        self._present(Actions(selection=result.text or "", app=result.app, tier=result.tier))

    def _schedule_release(self, pending: CaptureOutcome | CaptureSpikeError) -> None:
        """Release `capturing` on the main thread once the child behind `pending` has exited."""
        self._pending = pending
        if pending.settled:
            self._release()  # Tier 1 / no-selection: the child is already gone
            return
        self._settling = True

        def settle() -> None:
            code = pending.wait_settled(timeout=None)
            if isinstance(pending, CaptureSpikeError):
                # The failed path's resolution is otherwise invisible: say how the late child ended
                # (metadata only — never its line).
                handle = pending.handle
                line = f"contextai: late child settled exit={code} killed={handle.killed} line_seen={handle.line_seen}"
                if not handle.line_seen and handle.stderr.strip():
                    line += f" stderr={handle.stderr.strip().splitlines()[0]!r}"
                self.log(line)
            _call_after(self._release)

        threading.Thread(target=settle, name="contextai-settle", daemon=True).start()

    def _release(self) -> None:
        """The child that owned the pasteboard is gone: a new capture may spawn."""
        self.capturing = False
        self._settling = False
        self._pending = None

    # --- action → provider ------------------------------------------------------------------------

    def on_action(self, action: str) -> None:
        if self.selection is None:
            return
        self.generation += 1
        generation = self.generation
        self.last_action = action
        selection = self.selection
        self.panel.update(Loading(action=action, selection=selection))
        threading.Thread(target=self._action_worker, args=(generation, action, selection), daemon=True).start()

    def _action_worker(self, generation: int, action: str, selection: str) -> None:
        try:
            result = self.engine.run(action, selection, {"target_language": self.target_language})
        except Exception as exc:  # noqa: BLE001
            _call_after(self._finished, generation, action, selection, None, exc)
        else:
            _call_after(self._finished, generation, action, selection, result, None)

    def _finished(self, generation: int, action: str, selection: str, result, exc) -> None:
        if generation != self.generation or self.panel is None or not self.panel.visible:
            return  # dismissed or superseded while the request was in flight (FR-14)
        if exc is not None:
            self.log(f"contextai: action={action} error={type(exc).__name__}")
            self.panel.update(state_for_exception(exc, selection=selection, action=action))
            return
        self.log(
            f"contextai: action={action} provider={result.provider} model={result.model} {result.elapsed_ms:.0f}ms"
        )
        self.panel.update(state_for_result(result, selection, self.target_language))

    def on_retry(self) -> None:
        if self.last_action:
            self.on_action(self.last_action)

    def on_dismiss(self) -> None:
        self.generation += 1  # any in-flight result is now stale
        self.panel.dismiss()
        self.log("contextai: panel dismissed")

    # --- placement --------------------------------------------------------------------------------

    def _present(self, state) -> None:
        from AppKit import NSScreen

        screens = [
            Rect(f.origin.x, f.origin.y, f.size.width, f.size.height)
            for f in (screen.visibleFrame() for screen in NSScreen.screens())
        ]
        anchor, mouse = self.anchor, self.mouse

        def frame_for(size: tuple[float, float]) -> Rect:
            frame = place_panel(anchor, mouse, size, screens)
            self.log(
                f"contextai: panel {type(state).__name__} at ({frame.x:.0f}, {frame.y:.0f}) {frame.w:.0f}x{frame.h:.0f}"
            )
            return frame

        self.panel.present(state, frame_for)


def build_provider(name: str, model: str | None) -> Provider:
    if name == "mock":
        return MockProvider()
    if name == "openai":
        return OpenAIProvider(model=model) if model else OpenAIProvider()
    raise SystemExit(f"unknown provider {name!r}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--binary", type=Path, default=DEFAULT_BINARY, help="path to capture-spike")
    parser.add_argument("--settings", type=Path, default=None, metavar="PATH", help="settings file to use")
    # Every setting flag defaults to None: omitted ≠ given, so the file value survives (settings.effective).
    parser.add_argument("--provider", choices=list(PROVIDERS), default=None, help="default: mock")
    parser.add_argument("--model", default=None, help="provider model identifier (openai only)")
    parser.add_argument("--target-language", default=None, help="Translate target, e.g. Chinese (no default: PRD Q1)")
    parser.add_argument("--hotkey", default=None, help=f"default: {DEFAULT_HOTKEY}")
    parser.add_argument(
        "--cap", type=int, default=None, help=f"selection size cap in characters (default: {DEFAULT_SELECTION_CAP})"
    )
    parser.add_argument(
        "--auto-appear",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="also capture on selection gestures (drag > 3 pt, double/triple-click); off by default",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=None,
        metavar="BUNDLE_ID",
        help="app where auto-appear never fires (repeatable, adds to the file's list); the hotkey still works there",
    )
    parser.add_argument("--diagnostics", type=Path, default=None, help="JSONL of per-capture metadata (never text)")
    parser.add_argument("--no-diagnostics", action="store_true")
    args = parser.parse_args(argv)

    try:
        config = effective(
            load(resolve_path(args.settings)),
            target_language=args.target_language,
            provider=args.provider,
            model=args.model,
            hotkey=args.hotkey,
            auto_appear=args.auto_appear,
            exclude=args.exclude or (),
            diagnostics=args.diagnostics,
            no_diagnostics=args.no_diagnostics,
            selection_cap=args.cap,
        )
    except SettingsError as exc:
        print(exc, file=sys.stderr)
        return 2
    if config.target_language is None:
        print(MISSING_LANGUAGE_MESSAGE, file=sys.stderr)
        return 2

    if not args.binary.exists():
        parser.error(f"capture-spike not found at {args.binary}; run `swift build -c release` first")
    engine = ActionEngine(
        build_provider(config.provider, config.model), load_templates(), selection_cap=config.selection_cap
    )
    App(
        CaptureClient.for_binary(args.binary),
        engine,
        target_language=config.target_language,
        hotkey=config.hotkey,
        policy=CapturePolicy(excluded=config.excluded_apps),
        diagnostics=None if config.diagnostics_path is None else DiagnosticsLog(config.diagnostics_path),
        auto_appear=config.auto_appear,
    ).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
