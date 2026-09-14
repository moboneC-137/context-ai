# context-ai

Context AI reads the text a user has just selected in *any* macOS app and offers AI actions on it.
Before any UI exists, this repository holds the de-risking spike from the brief (Risk 2, addendum B5):
a headless probe that measures **hit-rate and latency** of cross-app selected-text capture.

- `CaptureKit` — library: the layered capture chain (Tier 1 AX `kAXSelectedText` → Tier 2 AX press on
  `Edit > Copy` → Tier 3 synthetic ⌘C), the bounds chain, the selection gate and the stats. The app
  will depend on this target directly.
- `capture-spike` — command-line probe. No windows, no menu bar item. Logs one JSON line per selection
  gesture and prints a per-app summary on Ctrl-C.

## Direction (decided 2026-09-14)

The application is being built in **Python** on top of this Swift layer. Swift keeps only what must be
native — the capture chain, the clipboard tiers with the late-copy guard, and the bounds chain — and
exposes them through one process boundary: `capture-spike --once` printing a JSON line. Everything above
that line (hotkey, capture policy, actions, AI provider, panel, evals) lives in the Python package.

- The boundary is specified in [`docs/capture-contract.md`](docs/capture-contract.md) (v1 = the current
  output; any change bumps the version and lands with the Python client in the same commit).
- Planned trimming of this target — moving the gesture gate, the stats and monitor mode to Python — happens
  only after the Python client is running against the unchanged binary and a matrix regression has passed.
- Division of labour and migration order: `docs/swift-python-split-2026-09-14.md` in the workspace root.

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

### Python side

The application layer is the `contextai` package, managed with [`uv`](https://docs.astral.sh/uv/)
(Python ≥ 3.12; the only non-Python runtime dependency is the `capture-spike` binary above).

```sh
cd context-ai
uv sync                                                   # creates .venv with pytest and pyyaml
uv run pytest                                             # Python tests (pytests/ — see note)
uv run python -m evals.run --template all --provider mock # prompt evals against the Mock Provider
uv run python -m evals.run --template all --provider openai --model gpt-4o-mini   # needs an API key
uv run python -m contextai.app --provider mock --target-language Chinese          # the app (v0 demo)
```

Running the app: it lives in the terminal (no Dock icon), waits for **⌃⌥Space** (`--hotkey` to change),
captures the current selection, and shows a floating panel with Summarize / Translate beside it.
Esc or a click outside dismisses it; Copy is the only way it writes the clipboard. `--target-language` is
required on purpose — there is no default. With `--provider openai` the key comes from the Keychain or
`OPENAI_API_KEY`. The terminal you launch from must be granted Accessibility (see below); the app's own
hotkey uses a consuming event tap, so the chord never reaches the app you are working in.

The tests live in `pytests/`, not `tests/`: this filesystem is case-insensitive and `tests/` would
resolve into SwiftPM's `Tests/`.

What is in the package so far (migration steps 1–3 of `docs/swift-python-split-2026-09-14.md`):

| Module | Role |
| --- | --- |
| `contextai/capture/` | The only Swift/Python boundary: spawns `capture-spike --once`, parses the JSON line (contract v1), typed errors for exit 2 / 64 / timeout. |
| `contextai/providers/` | `Provider` protocol, typed errors (`NoNetwork`, `Timeout`, `RateLimit`, `APIError`, `NotConfigured`, `SelectionTooLong`), `OpenAIProvider` (SDK-free, key from Keychain or `OPENAI_API_KEY`), deterministic `MockProvider`. |
| `contextai/actions/` | Action Templates as versioned YAML (`templates/summarize.yaml`, `translate.yaml`), strict loader, `ActionEngine` (size cap before any request, explicit target language, same-language sentinel). |
| `contextai/ui/` | `placement.py` (pure: beside the selection, flip, clamp, mouse fallback), `state.py` (pure: the panel states and every failure → state mapping), `panel.py` (PyObjC non-activating `NSPanel` that never becomes key). |
| `contextai/input/` | `hotkey.py`: binding parser + a Quartz event tap that consumes the chord (key-down *and* key-up). |
| `contextai/app.py` | Main loop: hotkey → capture on a worker thread → panel → action on a worker thread → render; generation counter drops results that arrive after Esc. |
| `evals/` | The Evals Harness: golden sets under `evals/golden/<template>/`, declarative checks (language, must/must-not contain, length, similarity), report + exit status. Not a CI gate. |

The OpenAI key is read from the login Keychain (`security add-generic-password -s ContextAI -a openai -w`)
or, for development, from `OPENAI_API_KEY`. It is never written to logs, `repr` or error messages.

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

| App | Where to select | Measured on 2026-09-13 (macOS 26, warm; p95 = max of 9) |
| --- | --- | --- |
| Safari | Any web page | **Tier 2** every time, ~30 ms; Tier 1 never (`AXWebArea` answers `noValue` for `AXSelectedText`) |
| Notes | A note body | Tier 1, 6–24 ms, `boundsSource: "range"` |
| Preview | A PDF | Tier 1, 1–27 ms; bounds fall to the mouse (PDFKit has no `AXSelectedTextRange`) |
| Chrome | Any web page | Tier 1 warm (6–46 ms) / Tier 2 cold (26–50 ms); bounds fall to the mouse |
| VS Code (or Slack) | Editor | Tier 1, 1–11 ms; Electron. Double-clicks reach Tier 2 (~30 ms) |
| Terminal | Shell output | Tier 1, 1–38 ms, `range` bounds — not the expected-fail control the brief assumed |
| IntelliJ IDEA 2026 | Editor | Tier 1, 3–23 ms, `range` bounds (JetBrains now exposes AX text) |
| IDLE (Tk) | Editor | Tier 2 or 3 only, 180–260 ms — the genuine zero-AX case |

Every warm hit is under the brief's 300 ms bar; hit-rate on real selections was 100% in all eight apps.
The full record, including what went wrong, is in
`_bmad-output/implementation-artifacts/capture-matrix-results.md`.

### Support tiers (measured)

| Tier | Meaning | Apps, as measured on this machine |
| --- | --- | --- |
| 1 | AX-native selected text, ≤ 40 ms warm, real bounds | Notes, Terminal, IntelliJ; VS Code and Chrome (warm) with approximate or no bounds |
| 2 | Clipboard via `Edit > Copy`, 30–60 ms, menu flash, no bounds | Safari, Chrome (cold) |
| 3 | Synthetic ⌘C only, 180–260 ms, clipboard race guarded | Tk apps (IDLE) |
| Unsupported / unknown | — | Qt (not exercisable through AX); the rest of the brief's zero-coverage list is untested |

Also try a password field (Safari login form) and look at `secureInput` in the line. Measured: Safari's
password field refuses both clipboard tiers (nothing leaks), and with Terminal's Secure Keyboard Entry on
the synthetic ⌘C is still delivered — secure input is a signal for the app to honour, not a guard.

Two things to keep in mind while gathering data:

- A double or triple click fires the capture on the **second** click, so the logged text is the word
  selected at that moment. Use a drag for paragraph-sized text.
- Drags that are not text selections (moving a window, dragging a scrollbar) still pass the gesture gate.
  In AppKit text apps (Notes, Terminal) the disabled `Edit > Copy` gate turns them into cheap
  `no-selection` lines. In Chrome, VS Code, IntelliJ, Tk apps and Preview `Edit > Copy` is *always*
  enabled, so they run the clipboard tiers (~200–400 ms). VS Code and IntelliJ then copy the **whole
  current line** when nothing is selected, which the chain reports as a Tier 2 hit — a false positive
  you should expect in the data.

Press **Ctrl-C** when done. If a capture is mid-flight the tool finishes it first (so the clipboard is
restored), then prints the summary to stderr.

Useful variants:

```sh
.build/release/capture-spike --tiers 1               # AX only; never touches the clipboard
.build/release/capture-spike --no-enhanced-ax        # measure Chrome/Electron without the AXEnhancedUserInterface hint
.build/release/capture-spike --tier1-retries 3       # re-enable the 3 × 150 ms Chromium retry loop (measured: net loss)
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
| `bounds`, `boundsSource` | Selection rect in `NSScreen` coordinates (bottom-left origin); `range` → `AXBoundsForRange`, `frame` → `AXFrame` of the focused element, `mouse` → pointer location, zero size. Zero, off-screen (y ≈ −9800) and larger-than-a-display rects are rejected; a `frame` is only used when it covers ≤ 25% of its display and contains the pointer, because WebKit, PDFKit, Chromium and Tk answer with the whole document or window. |
| `attempts` | One entry per tier that ran: its own elapsed ms, `ok`, and an `error` (`empty`, `no-focused-element`, `no-selected-text-attr`, `no-selection`, `no-copy-menu-item`, `timeout`, `no-text`, …). `retries` counts extra Tier 1 reads on Chromium/Electron. Tier 1 failures add `axError` (raw `AXError`, e.g. −25212 `kAXErrorNoValue`) and `role` (the focused element's `AXRole`). |
| `totalMs`, `boundsMs` | Whole chain including bounds; bounds chain alone. |
| `secureInput` | `IsSecureEventInputEnabled()` at capture time. |
| `error` | `no-selection` (Edit > Copy was disabled, clipboard tiers skipped), `exhausted` (every enabled tier failed), or a chain-level failure. |

Tier 2 and Tier 3 snapshot every pasteboard item and type before acting and restore them afterwards,
success or failure. Tier 3 only counts as a hit when `changeCount` advanced within 150 ms. After either
tier the pasteboard is watched for one more second: a Copy that lands late (Preview renders the page
before copying; IntelliJ writes twice) is reverted — but only if no key or mouse button was pressed since
the tier finished, and, when the tier captured text, only if the late write is that same text. Your own
⌘C or Edit > Copy always wins. The summary reports how many late copies were reverted.

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

- Tier 2/3 wait 150 ms for `changeCount` to advance; a Copy that lands later is reported as a `timeout`
  and reverted by the one-second watch described above. A clipboard write with no local input inside
  that second — Universal Clipboard from another device, `pbcopy` from a script — is indistinguishable
  from a late copy and is reverted too.
- The `Edit > Copy` gate only detects "nothing selected" in AppKit text apps. Chrome, VS Code, IntelliJ,
  Tk apps and Preview keep Copy enabled, and VS Code / IntelliJ copy the current line when nothing is
  selected, so a non-selection gesture there yields a false Tier 2 hit of that line.
- Safari never hits Tier 1: WebKit does not answer `AXSelectedText` on the web area. It exposes the
  selection through `AXSelectedTextMarkerRange` / `AXStringForTextMarkerRange` /
  `AXBoundsForTextMarkerRange` instead; that path is not implemented in the spike (Tier 2 captures Safari
  in ~30 ms) and is noted as follow-up work.
- Tier 3 posts virtual key 8, which is ⌘C only on QWERTY-family layouts.
- Keep the clipboard light during a run (no huge images or file promises): every Tier 2/3 attempt
  snapshots and restores all pasteboard data, and that cost lands in the tier's latency.
- Gestures that arrive while a capture is still in flight are skipped; their count is printed with the
  summary.

## Not in this spike

No AI provider, network, Keychain or prompts; no OCR; no `AXSelectedTextChangedNotification` observers
or AX polling; no muting of the system alert sound during Tier 3 (apps that do not handle ⌘C may beep);
no stable code-signing identity (see addendum B4 — the permission currently belongs to the terminal).
