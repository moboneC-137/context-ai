"""Main loop: hotkey → capture → Panel → Action → Provider → render (docs/swift-python-split §3).

    uv run python -m contextai.app --provider mock --target-language Chinese
    uv run python -m contextai.app --provider openai --target-language English --hotkey ctrl+alt+space
    uv run python -m contextai.app --provider mock --target-language Chinese --auto-appear --exclude com.apple.Terminal

Threading: AppKit owns the main thread. The capture spawn (~90 ms) and the provider request run on
worker threads and hand their outcome back with `AppHelper.callAfter`; a generation counter makes a
result that arrives after Esc (or after a newer hotkey press) a no-op (FR-14). Diagnostics printed
here are metadata only — never the selection or the result (NFR-4).
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from .actions import DEFAULT_SELECTION_CAP, ActionEngine, load_templates
from .capture import CaptureClient, CaptureOptions, CaptureOutcome
from .diagnostics import DEFAULT_PATH as DEFAULT_DIAGNOSTICS_PATH
from .diagnostics import DiagnosticsLog
from .input import DEFAULT_HOTKEY, HotkeyMonitor, SelectionMonitor, parse_hotkey
from .policy import CapturePolicy
from .providers import MockProvider, OpenAIProvider, Provider
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

    # --- lifecycle --------------------------------------------------------------------------------

    def run(self) -> None:
        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
        from PyObjCTools import AppHelper

        from .ui.panel import Panel

        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)  # no Dock icon, never frontmost
        self.panel = Panel(on_action=self.on_action, on_retry=self.on_retry, on_dismiss=self.on_dismiss)
        HotkeyMonitor(self.hotkey, self.on_hotkey).start()
        if self.auto_appear:
            SelectionMonitor(self.on_selection_gesture).start()
        mode = " · auto-appear on" if self.auto_appear else ""
        self.log(f"contextai: ready — press {self.hotkey.spec} on a selection{mode} (Ctrl-C here to quit)")
        AppHelper.runEventLoop()

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
        if require_auto_appear and not self.policy.auto_appear_allowed(bundle_id):
            return  # FR-12: excluded app, and this was not the hotkey
        if self.capturing:
            # FR-11: gestures during a capture are skipped and counted, never queued (this is why the
            # miss path must fit the latency budget).
            self.skipped_gestures += 1
            self.log(f"contextai: {trigger} ignored, capture in flight (skipped so far: {self.skipped_gestures})")
            return
        self.generation += 1
        generation = self.generation
        self.capturing = True
        point = NSEvent.mouseLocation()
        self.mouse = (float(point.x), float(point.y))
        options = self.policy.options_for(bundle_id)
        self.log(f"contextai: {trigger} frontmost={bundle_id} tiers={','.join(map(str, options.tiers))}")
        threading.Thread(target=self._capture_worker, args=(generation, options), daemon=True).start()

    def _capture_worker(self, generation: int, options: CaptureOptions) -> None:
        from PyObjCTools import AppHelper

        started = time.perf_counter()
        try:
            outcome = self.client.capture(options)
        except Exception as exc:  # noqa: BLE001 — every failure becomes a panel state
            AppHelper.callAfter(self._captured, generation, None, exc, started)
        else:
            AppHelper.callAfter(self._captured, generation, outcome, None, started)

    def _captured(self, generation: int, outcome: CaptureOutcome | None, exc: Exception | None, started: float) -> None:
        self.capturing = False
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
        from PyObjCTools import AppHelper

        try:
            result = self.engine.run(action, selection, {"target_language": self.target_language})
        except Exception as exc:  # noqa: BLE001
            AppHelper.callAfter(self._finished, generation, action, selection, None, exc)
        else:
            AppHelper.callAfter(self._finished, generation, action, selection, result, None)

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
    parser.add_argument("--provider", choices=["mock", "openai"], default="mock")
    parser.add_argument("--model", help="provider model identifier (openai only)")
    parser.add_argument("--target-language", required=True, help="Translate target, e.g. Chinese (no default: PRD Q1)")
    parser.add_argument("--hotkey", default=DEFAULT_HOTKEY)
    parser.add_argument("--cap", type=int, default=DEFAULT_SELECTION_CAP, help="selection size cap in characters")
    parser.add_argument(
        "--auto-appear",
        action="store_true",
        help="also capture on selection gestures (drag > 3 pt, double/triple-click); off by default",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="BUNDLE_ID",
        help="app where auto-appear never fires (repeatable); the hotkey still works there",
    )
    parser.add_argument(
        "--diagnostics", type=Path, default=DEFAULT_DIAGNOSTICS_PATH, help="JSONL of per-capture metadata (never text)"
    )
    parser.add_argument("--no-diagnostics", action="store_true")
    args = parser.parse_args(argv)

    if not args.binary.exists():
        parser.error(f"capture-spike not found at {args.binary}; run `swift build -c release` first")
    engine = ActionEngine(build_provider(args.provider, args.model), load_templates(), selection_cap=args.cap)
    App(
        CaptureClient.for_binary(args.binary),
        engine,
        target_language=args.target_language,
        hotkey=args.hotkey,
        policy=CapturePolicy(excluded=frozenset(args.exclude)),
        diagnostics=None if args.no_diagnostics else DiagnosticsLog(args.diagnostics),
        auto_appear=args.auto_appear,
    ).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
