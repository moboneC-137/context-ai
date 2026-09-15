"""Auto-Appear (PRD FR-11, opt-in): a global mouse monitor feeding the SelectionGate.

Ported from the spike's monitor mode in `main.swift`. Passive `NSEvent` global monitors are the right
tool here — nothing is swallowed, mouse events must reach the Target App untouched. Only *triggers* come
out of this module; the App decides whether to capture (policy, in-flight capture, own panel).
"""

from __future__ import annotations

from typing import Callable

from ..capture.gate import SelectionGate


class SelectionMonitor:
    """Calls `on_select(point)` on the main thread for every qualifying selection gesture."""

    def __init__(self, on_select: Callable[[tuple[float, float]], None], gate: SelectionGate | None = None) -> None:
        self.on_select = on_select
        self.gate = gate or SelectionGate()
        self._monitors: list = []

    def start(self) -> None:
        from AppKit import NSEvent, NSEventMaskLeftMouseDown, NSEventMaskLeftMouseUp

        def down(event):
            point = NSEvent.mouseLocation()
            self.gate.mouse_down((float(point.x), float(point.y)))
            return event

        def up(event):
            point = NSEvent.mouseLocation()
            xy = (float(point.x), float(point.y))
            if self.gate.mouse_up(xy, int(event.clickCount())):
                self.on_select(xy)
            return event

        self._monitors = [
            NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(NSEventMaskLeftMouseDown, down),
            NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(NSEventMaskLeftMouseUp, up),
        ]
        if any(m is None for m in self._monitors):
            raise RuntimeError("could not install the global mouse monitor: is Accessibility granted?")

    def stop(self) -> None:
        from AppKit import NSEvent

        for monitor in self._monitors:
            if monitor is not None:
                NSEvent.removeMonitor_(monitor)
        self._monitors = []
