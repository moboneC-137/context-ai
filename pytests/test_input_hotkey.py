"""FR-10: hotkey specs parse deterministically and match only the exact modifier set."""

from __future__ import annotations

import pytest

from contextai.input import DEFAULT_HOTKEY, HotkeyError, HotkeyMonitor, parse_hotkey
from contextai.input.hotkey import FLAG_COMMAND, FLAG_CONTROL, FLAG_OPTION, FLAG_SHIFT


def test_default_parses():
    hotkey = parse_hotkey(DEFAULT_HOTKEY)
    assert hotkey.keycode == 49 and hotkey.modifiers == FLAG_CONTROL | FLAG_OPTION
    assert hotkey.spec == "ctrl+alt+space"


def test_aliases_case_and_whitespace():
    assert parse_hotkey(" Control + Option + Space ") == parse_hotkey("ctrl+opt+space")
    assert parse_hotkey("cmd+shift+e").modifiers == FLAG_COMMAND | FLAG_SHIFT


def test_matches_exact_modifiers_only_and_ignores_device_bits():
    hotkey = parse_hotkey("ctrl+alt+space")
    assert hotkey.matches(49, FLAG_CONTROL | FLAG_OPTION)
    assert hotkey.matches(49, FLAG_CONTROL | FLAG_OPTION | 0x0101)  # device-dependent low bits ignored
    assert not hotkey.matches(49, FLAG_CONTROL)
    assert not hotkey.matches(49, FLAG_CONTROL | FLAG_OPTION | FLAG_SHIFT)
    assert not hotkey.matches(36, FLAG_CONTROL | FLAG_OPTION)


@pytest.mark.parametrize("spec", ["space", "ctrl+", "+space", "hyper+space", "ctrl+nosuchkey", ""])
def test_invalid_specs_are_rejected(spec):
    with pytest.raises(HotkeyError):
        parse_hotkey(spec)


# --- the tap's policy, framework-free (retro D4) ---------------------------------------------------


def test_shift_alone_is_not_a_global_modifier():
    with pytest.raises(HotkeyError, match="shift alone"):
        parse_hotkey("shift+a")
    assert parse_hotkey("shift+a", require_modifier=False).modifiers == FLAG_SHIFT  # tools may post it
    assert parse_hotkey("cmd+shift+e").modifiers == FLAG_COMMAND | FLAG_SHIFT  # shift with a real one is fine


def _monitor(presses: list[int]) -> HotkeyMonitor:
    return HotkeyMonitor(parse_hotkey("ctrl+alt+space"), lambda: presses.append(1))


def test_bound_chord_fires_once_and_is_consumed_with_its_key_up():
    monitor = _monitor([])
    chord = FLAG_CONTROL | FLAG_OPTION
    assert monitor.decide(HotkeyMonitor.KEY_DOWN, 49, chord) == (True, True)
    assert monitor.decide(HotkeyMonitor.KEY_UP, 49, chord) == (True, False)  # paired up consumed
    assert monitor.decide(HotkeyMonitor.KEY_UP, 49, chord) == (False, False)  # a later, unpaired up passes


def test_autorepeat_of_the_chord_is_consumed_but_never_fires_again():
    monitor = _monitor([])
    chord = FLAG_CONTROL | FLAG_OPTION
    assert monitor.decide(HotkeyMonitor.KEY_DOWN, 49, chord) == (True, True)
    for _ in range(20):  # the key held past the repeat delay
        assert monitor.decide(HotkeyMonitor.KEY_DOWN, 49, chord, is_repeat=True) == (True, False)
    assert monitor.decide(HotkeyMonitor.KEY_UP, 49, chord) == (True, False)


def test_other_keys_and_wrong_modifiers_pass_through():
    monitor = _monitor([])
    assert monitor.decide(HotkeyMonitor.KEY_DOWN, 49, FLAG_CONTROL) == (False, False)  # missing alt
    assert monitor.decide(HotkeyMonitor.KEY_DOWN, 36, FLAG_CONTROL | FLAG_OPTION) == (False, False)  # return key
    assert monitor.decide(HotkeyMonitor.KEY_UP, 36, 0) == (False, False)
    # An autorepeat of our chord with no prior consumed down (we started while it was held) is still
    # consumed — passing it through would type ⌥Space's non-breaking space into the Target App — but
    # it never fires, and its eventual key-up is consumed too (no orphaned up).
    assert monitor.decide(HotkeyMonitor.KEY_DOWN, 49, FLAG_CONTROL | FLAG_OPTION, is_repeat=True) == (True, False)
    assert monitor.decide(HotkeyMonitor.KEY_UP, 49, FLAG_CONTROL | FLAG_OPTION) == (True, False)


def test_reset_forgets_a_pending_key_up():
    """The system can disable the tap between a chord's down and up; on re-arm the latch is cleared so
    the next plain Space key-up is not eaten."""
    monitor = _monitor([])
    assert monitor.decide(HotkeyMonitor.KEY_DOWN, 49, FLAG_CONTROL | FLAG_OPTION) == (True, True)
    monitor.reset()
    assert monitor.decide(HotkeyMonitor.KEY_UP, 49, 0) == (False, False)


def test_fire_calls_the_handler():
    presses: list[int] = []
    monitor = _monitor(presses)
    monitor._fire()
    assert presses == [1]


def test_handler_exception_is_reported_through_log_not_raised(capsys):
    def boom() -> None:
        raise RuntimeError("handler blew up")

    lines: list[str] = []
    monitor = HotkeyMonitor(parse_hotkey("ctrl+alt+space"), boom, log=lines.append)
    monitor._fire()  # what AppHelper.callAfter runs on the main loop
    assert len(lines) == 1 and "hotkey handler failed" in lines[0] and "handler blew up" in lines[0]
    assert capsys.readouterr().err == ""  # routed through the log seam, not straight to stderr
