"""Post a key chord with explicit modifier flags, e.g. `uv run python -m tools.press_key ctrl+alt+space`.

For driving the app and the matrix from a script. `cliclick kd:…/kp:…` proved unreliable for chords
(modifier flags sometimes missing from the key-down, a stuck ⌘ leaking in); posting a CGEvent with the
flags set on the event itself is deterministic. Uses the same binding syntax as `--hotkey`.
"""

from __future__ import annotations

import sys
import time

from contextai.input import parse_hotkey


def press(spec: str, hold: float = 0.03) -> None:
    from Quartz import (
        CGEventCreateKeyboardEvent,
        CGEventPost,
        CGEventSetFlags,
        kCGHIDEventTap,
    )

    hotkey = parse_hotkey(spec, require_modifier=False)
    for down in (True, False):
        event = CGEventCreateKeyboardEvent(None, hotkey.keycode, down)
        CGEventSetFlags(event, hotkey.modifiers)
        CGEventPost(kCGHIDEventTap, event)
        time.sleep(hold)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    press(sys.argv[1])
