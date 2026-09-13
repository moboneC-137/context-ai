# context-ai

Context AI reads the text a user has just selected in *any* macOS app and offers AI actions on it.
Before any UI exists, this repository holds the de-risking spike from the brief (Risk 2, addendum B5):
a headless probe that measures **hit-rate and latency** of cross-app selected-text capture.

- `CaptureKit` — library: the layered capture chain (Tier 1 AX `kAXSelectedText` → Tier 2 AX press on
  `Edit > Copy` → Tier 3 synthetic ⌘C), the bounds chain, the selection gate and the stats. The app
  will depend on this target directly.
- `capture-spike` — command-line probe. No windows, no menu bar item. Logs one JSON line per selection
  gesture and prints a per-app summary on Ctrl-C.

## Requirements

- macOS 14 or later, Apple silicon or Intel.
- A Swift 6 toolchain. The Command Line Tools are enough; Xcode is not required.

## Build and test

```sh
cd context-ai
swift build -c release            # → .build/release/capture-spike
scripts/test.sh                   # runs the tests and fails unless tests actually ran
```

Use `scripts/test.sh` rather than bare `swift test`. On a machine that has **only the Command Line
Tools**, SwiftPM does not discover Swift Testing by itself, so bare `swift test` builds, runs zero tests
and exits 0. The script adds `-Xswiftc -F/Library/Developer/CommandLineTools/Library/Developer/Frameworks`
when `xcode-select -p` points at the CLT and checks that the output reports a non-zero test count.

## Grant Accessibility to the terminal

Reading another app's selection needs the Accessibility permission. macOS attributes a command-line
tool to the **app that launched it**, so the grant goes to your terminal (Terminal, iTerm2, Warp, the
VS Code integrated terminal, …), not to `capture-spike`.

Under tmux or screen the grant goes to the multiplexer binary, not the terminal window that hosts it.

1. Run `.build/release/capture-spike --once` once. macOS shows the "would like to control this computer"
   prompt, and the tool prints which app needs the permission and exits with code 2.
2. Open **System Settings → Privacy & Security → Accessibility**, enable that app (add it with **+** if it
   is not listed).
3. Quit and reopen the terminal app if the next run still exits with code 2.

Rebuilding the tool does not require re-granting, because the grant belongs to the terminal. Exit code 2
also covers the case where the global mouse monitor could not be installed.

## Run the B5 matrix

```sh
.build/release/capture-spike --out matrix.jsonl
```

The tool prints `listening …` on stderr and then waits. Work through the matrix: in each app, select text
by dragging (more than 3 pt) or by double/triple-clicking a word or paragraph. Every qualifying mouseUp
produces one JSON line on stdout (and in `matrix.jsonl`). Plain clicks and typing produce nothing and
never touch the clipboard. Do a handful of selections per app so the percentiles mean something.

| App | Where to select | Expected |
| --- | --- | --- |
| Safari | Any web page | Tier 1, `boundsSource: "range"` |
| Notes | A note body | Tier 1 |
| Preview | A PDF | Tier 1 |
| Chrome | Any web page | Tier 1 after `AXEnhancedUserInterface`, possibly with retries |
| Slack or VS Code | Message / editor | Tier 1 or 2, slower — Electron |
| Terminal | Shell output | Tier 3 via ⌘C, or `error: "no-selection"` — expected-fail control |
| IntelliJ | Editor | Tier 3 or `error` — expected-fail control |

Also try a password field (Safari login form, or `sudo` in Terminal) and look at `secureInput` in the
line: that answers whether secure input swallows the synthetic events (addendum B1).

Two things to keep in mind while gathering data:

- A double or triple click fires the capture on the **second** click, so the logged text is the word
  selected at that moment. Use a drag for paragraph-sized text.
- Drags that are not text selections (moving a window, dragging a scrollbar) still pass the gesture gate.
  In native apps the disabled `Edit > Copy` gate turns them into cheap `no-selection` lines; in Electron
  apps `Edit > Copy` is always enabled, so they count as full attempts and burn the Tier 2/3 timeouts.

Press **Ctrl-C** when done. If a capture is mid-flight the tool finishes it first (so the clipboard is
restored), then prints the summary to stderr.

Useful variants:

```sh
.build/release/capture-spike --tiers 1               # AX only; never touches the clipboard
.build/release/capture-spike --no-enhanced-ax        # measure Chrome/Electron without the AX hint + retries
.build/release/capture-spike --max-text 0            # log the full selected text
.build/release/capture-spike --once                  # one capture of the focused app, then exit (0 = got text, 1 = not)
```

`--once` runs the chain immediately on whatever app is frontmost, which is only useful when the tool is
driven from a script or another app already has focus and a selection.

## Read the output

One line per gesture, keys sorted:

```json
{"app":"com.apple.Safari","attempts":[{"ms":4.1,"ok":true,"tier":1}],"bounds":{"h":18.0,"w":280.0,"x":312.0,"y":544.5},
 "boundsMs":1.2,"boundsSource":"range","secureInput":false,"text":"Select text anywhere…","textLength":412,
 "tier":1,"totalMs":6.8,"ts":"2026-09-12T23:40:01Z"}
```

| Key | Meaning |
| --- | --- |
| `app` | Bundle identifier of the frontmost app. |
| `tier` | Winning tier (1, 2, 3) or `null` when nothing produced text. |
| `text`, `textLength` | Captured text (truncated to `--max-text`, default 200) and its full length. |
| `bounds`, `boundsSource` | Selection rect in `NSScreen` coordinates (bottom-left origin); `range` → `AXBoundsForRange`, `frame` → `AXFrame` of the focused element, `mouse` → pointer location, zero size. Zero and off-screen (y ≈ −9800) AX rects are rejected, never logged. |
| `attempts` | One entry per tier that ran: its own elapsed ms, `ok`, and an `error` (`empty`, `no-focused-element`, `no-selection`, `no-copy-menu-item`, `timeout`, `no-text`, …). `retries` counts extra Tier 1 reads on Chromium/Electron. |
| `totalMs`, `boundsMs` | Whole chain including bounds; bounds chain alone. |
| `secureInput` | `IsSecureEventInputEnabled()` at capture time. |
| `error` | `no-selection` (Edit > Copy was disabled, clipboard tiers skipped), `exhausted` (every enabled tier failed), or a chain-level failure. |

Tier 2 and Tier 3 snapshot every pasteboard item and type before acting and restore them afterwards,
success or failure. Tier 3 only counts as a hit when `changeCount` advanced within 150 ms.

The Ctrl-C summary, per app:

```
app                   attempts  hits  hit-rate  p50 ms  p95 ms  tiers (1/2/3/fail)
--------------------  --------  ----  --------  ------  ------  ------------------
com.apple.Safari      12        12    100%      5.9     9.4     12/0/0/0
com.google.Chrome     10        9     90%       48.0    310.2   7/2/0/1
com.apple.Terminal    6         4     67%       120.5   140.1   0/0/4/2
```

`p50`/`p95` are over hits only (`totalMs`); `attempts` shows how many gestures produced nothing. The
brief's bar: if p95 exceeds ~300 ms the interaction is dead regardless of the rest of the app. An empty
session prints `no captures`.

## Known limitations

- Tier 2/3 wait 150 ms for `changeCount` to advance. A Copy that lands after that window is reported as
  a `timeout`, and the late write can leave the selection on the clipboard (the restore has already run
  or was skipped because nothing had changed yet).
- Tier 3 posts virtual key 8, which is ⌘C only on QWERTY-family layouts.
- Keep the clipboard light during a run (no huge images or file promises): every Tier 2/3 attempt
  snapshots and restores all pasteboard data, and that cost lands in the tier's latency.
- Gestures that arrive while a capture is still in flight are skipped; their count is printed with the
  summary.

## Not in this spike

No AI provider, network, Keychain or prompts; no OCR; no `AXSelectedTextChangedNotification` observers
or AX polling; no muting of the system alert sound during Tier 3 (apps that do not handle ⌘C may beep);
no stable code-signing identity (see addendum B4 — the permission currently belongs to the terminal).
