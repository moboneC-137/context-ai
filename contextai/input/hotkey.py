"""Global hotkey (PRD FR-10): a parsed binding plus an AppKit global key monitor.

`parse_hotkey` is pure (tested); `HotkeyMonitor` needs Quartz and a running main run loop. The
modifier bit layout is shared by `NSEvent.modifierFlags` and `CGEventFlags`, so one matcher serves both.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

# NSEventModifierFlags == CGEventFlags for these bits (stable ABI values; kept local so the parser has no framework import).
FLAG_SHIFT = 1 << 17
FLAG_CONTROL = 1 << 18
FLAG_OPTION = 1 << 19
FLAG_COMMAND = 1 << 20
DEVICE_INDEPENDENT_MASK = 0xFFFF0000

MODIFIERS: dict[str, int] = {
    "shift": FLAG_SHIFT,
    "ctrl": FLAG_CONTROL,
    "control": FLAG_CONTROL,
    "alt": FLAG_OPTION,
    "opt": FLAG_OPTION,
    "option": FLAG_OPTION,
    "cmd": FLAG_COMMAND,
    "command": FLAG_COMMAND,
}

# Virtual key codes for the keys a hotkey is likely to use (Carbon `kVK_*`, US layout for letters).
KEYCODES: dict[str, int] = {
    "space": 49,
    "return": 36,
    "enter": 36,
    "tab": 48,
    "escape": 53,
    "esc": 53,
    "a": 0,
    "s": 1,
    "d": 2,
    "f": 3,
    "h": 4,
    "g": 5,
    "z": 6,
    "x": 7,
    "c": 8,
    "v": 9,
    "b": 11,
    "q": 12,
    "w": 13,
    "e": 14,
    "r": 15,
    "y": 16,
    "t": 17,
    "1": 18,
    "2": 19,
    "3": 20,
    "4": 21,
    "6": 22,
    "5": 23,
    "9": 25,
    "7": 26,
    "8": 28,
    "0": 29,
    "o": 31,
    "u": 32,
    "i": 34,
    "p": 35,
    "l": 37,
    "j": 38,
    "k": 40,
    "n": 45,
    "m": 46,
    "f1": 122,
    "f2": 120,
    "f3": 99,
    "f4": 118,
    "f5": 96,
    "f6": 97,
    "f7": 98,
    "f8": 100,
    "f9": 101,
    "f10": 109,
    "f11": 103,
    "f12": 111,
}

DEFAULT_HOTKEY = "ctrl+alt+space"
"""Working default until UX settles PRD Open Question 2; unlikely to collide with app shortcuts."""


class HotkeyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Hotkey:
    keycode: int
    modifiers: int
    spec: str

    def matches(self, keycode: int, modifier_flags: int) -> bool:
        return keycode == self.keycode and (modifier_flags & DEVICE_INDEPENDENT_MASK) & _ALL_MODIFIERS == self.modifiers


_ALL_MODIFIERS = FLAG_SHIFT | FLAG_CONTROL | FLAG_OPTION | FLAG_COMMAND


def parse_hotkey(spec: str) -> Hotkey:
    """`"ctrl+alt+space"` → Hotkey. At least one modifier is required so a bare key never becomes global."""
    parts = [p.strip().lower() for p in spec.split("+")]
    if len(parts) < 2 or not all(parts):
        raise HotkeyError(f"hotkey {spec!r} must be modifier(+modifier)+key, e.g. {DEFAULT_HOTKEY!r}")
    *mods, key = parts
    modifiers = 0
    for mod in mods:
        if mod not in MODIFIERS:
            raise HotkeyError(f"unknown modifier {mod!r} in {spec!r}; use {sorted(set(MODIFIERS))}")
        modifiers |= MODIFIERS[mod]
    if key not in KEYCODES:
        raise HotkeyError(f"unknown key {key!r} in {spec!r}")
    canonical = [
        name
        for name, flag in (("ctrl", FLAG_CONTROL), ("alt", FLAG_OPTION), ("shift", FLAG_SHIFT), ("cmd", FLAG_COMMAND))
        if modifiers & flag
    ]
    return Hotkey(keycode=KEYCODES[key], modifiers=modifiers, spec="+".join([*canonical, key]))


class HotkeyMonitor:
    """Calls `on_press()` on the main thread when the binding is pressed in any application.

    Implemented as a Quartz event tap that *consumes* the matching key-down. A passive `NSEvent` global
    monitor cannot do that, and the difference is not cosmetic: with a passive monitor the chord still
    reaches the Target App, and a binding like ⌃⌥Space types a non-breaking space over the Selection
    before the capture runs (found live on 2026-09-14). The tap needs Accessibility trust — which the
    app already requires — and lives on the main run loop, so `on_press` runs on the main thread.
    """

    def __init__(self, hotkey: Hotkey, on_press: Callable[[], None]) -> None:
        self.hotkey = hotkey
        self.on_press = on_press
        self._tap = None
        self._source = None

    def start(self) -> None:
        from Quartz import (  # imported here: the parser must stay framework-free
            CFMachPortCreateRunLoopSource,
            CFRunLoopAddSource,
            CFRunLoopGetMain,
            CGEventGetFlags,
            CGEventGetIntegerValueField,
            CGEventMaskBit,
            CGEventTapCreate,
            CGEventTapEnable,
            kCFRunLoopCommonModes,
            kCGEventKeyDown,
            kCGEventKeyUp,
            kCGEventTapDisabledByTimeout,
            kCGEventTapDisabledByUserInput,
            kCGEventTapOptionDefault,
            kCGHeadInsertEventTap,
            kCGKeyboardEventKeycode,
            kCGSessionEventTap,
        )

        swallowed_down = False

        def callback(proxy, event_type, event, refcon):
            nonlocal swallowed_down
            if event_type in (kCGEventTapDisabledByTimeout, kCGEventTapDisabledByUserInput):
                CGEventTapEnable(self._tap, True)  # the system disables a slow tap; re-arm it
                return event
            keycode = CGEventGetIntegerValueField(event, kCGKeyboardEventKeycode)
            if event_type == kCGEventKeyUp:
                if swallowed_down and keycode == self.hotkey.keycode:
                    swallowed_down = False
                    return None  # consume the up as well: an orphaned key-up is delivered to the Target App
                return event
            if self.hotkey.matches(keycode, int(CGEventGetFlags(event))):
                swallowed_down = True
                self.on_press()
                return None  # consumed: the Target App never sees the chord
            return event

        self._tap = CGEventTapCreate(
            kCGSessionEventTap,
            kCGHeadInsertEventTap,
            kCGEventTapOptionDefault,
            CGEventMaskBit(kCGEventKeyDown) | CGEventMaskBit(kCGEventKeyUp),
            callback,
            None,
        )
        if self._tap is None:
            raise HotkeyError("could not create the key event tap: is Accessibility granted to this app?")
        self._source = CFMachPortCreateRunLoopSource(None, self._tap, 0)
        CFRunLoopAddSource(CFRunLoopGetMain(), self._source, kCFRunLoopCommonModes)
        CGEventTapEnable(self._tap, True)

    def stop(self) -> None:
        from Quartz import (
            CFRunLoopGetMain,
            CFRunLoopRemoveSource,
            CGEventTapEnable,
            kCFRunLoopCommonModes,
        )

        if self._tap is not None:
            CGEventTapEnable(self._tap, False)
        if self._source is not None:
            CFRunLoopRemoveSource(CFRunLoopGetMain(), self._source, kCFRunLoopCommonModes)
        self._tap = self._source = None
