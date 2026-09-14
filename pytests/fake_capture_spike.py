"""Stand-in for the Swift `capture-spike` binary, scripted through environment variables.

Reproduces the contract's process behaviour so the client can be tested without macOS permissions:
one flushed JSON line, then an optional linger (the late-copy guard window) before exit; or the
no-line exits (2, 64); or a hang.

  FAKE_MODE      hit | miss | no-selection | exit2 | exit2-foreign | exit64 | hang | garbage   (default: hit)
  FAKE_LINGER    seconds to stay alive after writing the line (default: 0)
  FAKE_ARGS      file to write argv[1:] into, one per line
  FAKE_MARKER    file to create just before a *natural* exit (never created if killed)
"""

import json
import os
import sys
import time

ARGS_FILE = os.environ.get("FAKE_ARGS")
if ARGS_FILE:
    with open(ARGS_FILE, "w", encoding="utf-8") as handle:
        handle.write("\n".join(sys.argv[1:]))

MODE = os.environ.get("FAKE_MODE", "hit")
LINGER = float(os.environ.get("FAKE_LINGER", "0"))
MARKER = os.environ.get("FAKE_MARKER")

HIT = {
    "app": "com.apple.Notes",
    "attempts": [{"ms": 4.1, "ok": True, "tier": 1}],
    "bounds": {"h": 18.0, "w": 280.0, "x": 312.0, "y": 544.5},
    "boundsMs": 1.2,
    "boundsSource": "range",
    "secureInput": False,
    "text": "Select text anywhere…",
    "textLength": 21,
    "tier": 1,
    "totalMs": 6.8,
    "ts": "2026-09-12T23:40:01Z",
    "futureKey": {"ignored": True},
}
MISS = {
    "app": "com.google.Chrome",
    "attempts": [
        {"error": "empty", "ms": 12.3, "ok": False, "role": "AXWebArea", "tier": 1},
        {"error": "timeout", "ms": 151.0, "ok": False, "tier": 2},
        {"error": "timeout", "ms": 150.4, "ok": False, "tier": 3},
    ],
    "error": "exhausted",
    "secureInput": False,
    "tier": None,
    "totalMs": 318.9,
    "ts": "2026-09-13T20:51:07Z",
}
NO_SELECTION = {
    "app": "com.apple.Notes",
    "attempts": [{"error": "no-selection", "ms": 3.9, "ok": False, "tier": 2}],
    "error": "no-selection",
    "secureInput": False,
    "tier": None,
    "totalMs": 4.4,
    "ts": "2026-09-13T20:51:09Z",
}


def finish(code: int) -> None:
    if LINGER:
        time.sleep(LINGER)
    if MARKER:
        with open(MARKER, "w", encoding="utf-8"):
            pass
    sys.exit(code)


if MODE == "exit2":
    sys.stderr.write(
        "capture-spike: Accessibility access is not granted.\n"
        "macOS attributes a command-line tool to the app that launched it, so the app that needs permission is:\n"
        "\n"
        "    Visual Studio Code\n"
        "\n"
        'Open System Settings > Privacy & Security > Accessibility, enable "Visual Studio Code", then run capture-spike again.\n'
    )
    sys.exit(2)
if MODE == "exit2-foreign":
    sys.stderr.write("python: can't open file 'wrong.py': [Errno 2] No such file or directory\n")
    sys.exit(2)
if MODE == "exit64":
    sys.stderr.write("capture-spike: unknown argument: --bogus\nusage: capture-spike ...\n")
    sys.exit(64)
if MODE == "hang":
    time.sleep(30)
    sys.exit(0)
if MODE == "garbage":
    sys.stdout.write("this is not json\n")
    sys.stdout.flush()
    finish(0)

line = {"hit": HIT, "miss": MISS, "no-selection": NO_SELECTION}[MODE]
sys.stdout.write(json.dumps(line, sort_keys=True, ensure_ascii=False) + "\n")
sys.stdout.flush()
sys.stderr.write("capture-spike: status text that must be ignored\n")
sys.stderr.flush()
finish(0 if MODE == "hit" else 1)
