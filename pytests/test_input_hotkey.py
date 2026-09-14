"""FR-10: hotkey specs parse deterministically and match only the exact modifier set."""

from __future__ import annotations

import pytest

from contextai.input import DEFAULT_HOTKEY, HotkeyError, parse_hotkey
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
