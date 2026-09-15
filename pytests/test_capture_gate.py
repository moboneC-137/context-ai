"""SelectionGate, ported one-for-one from Swift `SelectionGateTests` (migration step 4)."""

from __future__ import annotations

from contextai.capture import SelectionGate


def test_plain_click_is_ignored():
    gate = SelectionGate()
    gate.mouse_down((10, 10))
    assert gate.mouse_up((11, 12), click_count=1, now=0.0) is False


def test_drag_beyond_3pt_counts():
    gate = SelectionGate()
    gate.mouse_down((10, 10))
    assert gate.mouse_up((14, 10), click_count=1, now=0.0) is True


def test_drag_of_exactly_3pt_does_not_count():
    gate = SelectionGate()
    gate.mouse_down((0, 0))
    assert gate.mouse_up((3, 0), click_count=1, now=0.0) is False


def test_double_and_triple_click_count_without_movement():
    gate = SelectionGate()
    gate.mouse_down((5, 5))
    assert gate.mouse_up((5, 5), click_count=2, now=0.0) is True
    gate.mouse_down((5, 5))
    assert gate.mouse_up((5, 5), click_count=3, now=0.5) is True


def test_second_gesture_inside_debounce_is_dropped_later_one_accepted():
    gate = SelectionGate()
    gate.mouse_down((0, 0))
    assert gate.mouse_up((20, 0), click_count=1, now=0.0) is True
    gate.mouse_down((0, 0))
    assert gate.mouse_up((20, 0), click_count=1, now=0.1) is False
    gate.mouse_down((0, 0))
    assert gate.mouse_up((20, 0), click_count=1, now=0.25) is True


def test_mouse_up_without_mouse_down_is_zero_travel():
    gate = SelectionGate()
    assert gate.mouse_up((100, 100), click_count=1, now=0.0) is False
    assert gate.mouse_up((100, 100), click_count=2, now=0.0) is True


def test_wall_clock_default_works():
    gate = SelectionGate()
    gate.mouse_down((0, 0))
    assert gate.mouse_up((10, 0), click_count=1) is True
