"""FR-13: below the selection, flip above when needed, always fully on the selection's screen."""

from __future__ import annotations

from contextai.capture import Bounds
from contextai.ui import GAP, Rect, place_panel

MAIN = Rect(0, 0, 1440, 875)  # visible frame: y=0 above the Dock? no — Dock excluded below; menu bar above
SECOND = Rect(1440, 100, 1920, 1055)
PANEL = (380.0, 120.0)
MOUSE = (700.0, 400.0)


def test_below_the_selection_left_aligned():
    frame = place_panel(Bounds(300, 500, 200, 18), MOUSE, PANEL, [MAIN])
    assert frame == Rect(300, 500 - GAP - 120, 380, 120)


def test_flips_above_when_no_room_below():
    frame = place_panel(Bounds(300, 60, 200, 18), MOUSE, PANEL, [MAIN])
    assert frame.y == 60 + 18 + GAP
    assert frame.x == 300


def test_clamps_to_the_right_and_bottom_edges():
    frame = place_panel(Bounds(1300, 500, 200, 18), MOUSE, PANEL, [MAIN])
    assert frame.max_x == MAIN.max_x
    tall = (380.0, 900.0)  # taller than the screen: neither side fits → clamped inside
    frame = place_panel(Bounds(300, 500, 200, 18), MOUSE, tall, [MAIN])
    assert frame.y == MAIN.y and frame.h == 900.0


def test_uses_the_screen_containing_the_selection():
    frame = place_panel(Bounds(1500, 900, 100, 18), MOUSE, PANEL, [MAIN, SECOND])
    assert SECOND.contains(frame.x, frame.y) and frame.max_x <= SECOND.max_x
    assert frame.y == 900 - GAP - 120


def test_falls_back_to_the_mouse_without_bounds():
    frame = place_panel(None, (1600.0, 300.0), PANEL, [MAIN, SECOND])
    assert frame.x == 1600.0
    assert frame.y == 300.0 - GAP - 120
    assert SECOND.contains(frame.x, frame.y)


def test_zero_size_mouse_bounds_behave_like_a_point():
    """Swift's `boundsSource: mouse` is a zero-size rect at the pointer; same result as no bounds."""
    assert place_panel(Bounds(700, 400, 0, 0), MOUSE, PANEL, [MAIN]) == place_panel(None, MOUSE, PANEL, [MAIN])
