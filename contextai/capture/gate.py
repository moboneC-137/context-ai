"""SelectionGate: which mouse-ups count as "the user just selected something" (PRD FR-11).

Ported from Swift `SelectionGate` in migration step 4 — pure logic, so it belongs on this side of the
boundary. Semantics are unchanged: a mouse-up qualifies when the pointer travelled *more than*
`drag_threshold` points since mouse-down or the click count is ≥ 2, and the previous accepted gesture
is at least `debounce` seconds old. Time is injected (`now`) so tests never sleep.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

DRAG_THRESHOLD = 3.0
"""Points; the B5 matrix used 3 pt."""
DEBOUNCE_SECONDS = 0.2


@dataclass
class SelectionGate:
    drag_threshold: float = DRAG_THRESHOLD
    debounce: float = DEBOUNCE_SECONDS
    _down: tuple[float, float] | None = field(default=None, init=False, repr=False)
    _last_accepted: float | None = field(default=None, init=False, repr=False)

    def mouse_down(self, point: tuple[float, float]) -> None:
        self._down = point

    def mouse_up(self, point: tuple[float, float], click_count: int, now: float | None = None) -> bool:
        """True when this mouse-up should trigger a capture. A mouse-up without a mouse-down is zero travel."""
        now = time.monotonic() if now is None else now
        down, self._down = self._down, None
        distance = math.hypot(point[0] - down[0], point[1] - down[1]) if down is not None else 0.0
        if not (distance > self.drag_threshold or click_count >= 2):
            return False
        if self._last_accepted is not None and now - self._last_accepted < self.debounce:
            return False
        self._last_accepted = now
        return True
