"""Where the Panel goes (PRD FR-13): beside the Selection, flipped to stay on screen, or at the mouse.

Pure geometry so it is unit-tested without AppKit. All rects are in NSScreen coordinates — origin at the
bottom-left of the primary display, y grows upward — exactly what `capture-spike` reports for `bounds`
and what `NSWindow.setFrame` takes, so no flipping happens here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..capture.model import Bounds

GAP = 8.0
"""Points between the Selection and the Panel."""


@dataclass(frozen=True, slots=True)
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def max_x(self) -> float:
        return self.x + self.w

    @property
    def max_y(self) -> float:
        return self.y + self.h

    def contains(self, x: float, y: float) -> bool:
        return self.x <= x < self.max_x and self.y <= y < self.max_y

    @classmethod
    def from_bounds(cls, bounds: Bounds) -> "Rect":
        return cls(bounds.x, bounds.y, bounds.w, bounds.h)


def anchor_rect(bounds: Bounds | None, mouse: tuple[float, float]) -> Rect:
    """The Selection rect, or a zero-size rect at the pointer when Swift reported no bounds."""
    if bounds is None:
        return Rect(mouse[0], mouse[1], 0.0, 0.0)
    return Rect.from_bounds(bounds)


def screen_for(anchor: Rect, screens: Sequence[Rect], mouse: tuple[float, float]) -> Rect:
    """The screen holding the anchor's centre, else the one under the mouse, else the first."""
    if not screens:
        raise ValueError("at least one screen is required")
    cx, cy = anchor.x + anchor.w / 2, anchor.y + anchor.h / 2
    for screen in screens:
        if screen.contains(cx, cy):
            return screen
    for screen in screens:
        if screen.contains(*mouse):
            return screen
    return screens[0]


def place_panel(
    bounds: Bounds | None,
    mouse: tuple[float, float],
    panel_size: tuple[float, float],
    screens: Sequence[Rect],
    gap: float = GAP,
) -> Rect:
    """Panel frame: below the anchor, left-aligned; above it when below does not fit; always on screen.

    `screens` should be the *visible* frames (menu bar and Dock excluded). The result is fully inside the
    chosen screen even when the panel is taller than the room on either side of the anchor.
    """
    width, height = panel_size
    anchor = anchor_rect(bounds, mouse)
    screen = screen_for(anchor, screens, mouse)

    below_y = anchor.y - gap - height
    above_y = anchor.max_y + gap
    if below_y >= screen.y:
        y = below_y
    elif above_y + height <= screen.max_y:
        y = above_y
    else:
        y = below_y  # neither fits; clamping below keeps the panel nearest the reading position

    x = anchor.x
    x = min(x, screen.max_x - width)
    x = max(x, screen.x)
    y = min(y, screen.max_y - height)
    y = max(y, screen.y)
    return Rect(x, y, width, height)
