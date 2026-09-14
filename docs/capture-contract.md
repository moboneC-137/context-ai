# `capture-spike` contract — v1

The one boundary between the Swift capture layer and the Python application. Python calls the
binary; Swift answers with one JSON line. Nothing else crosses this line: Python never touches the
Accessibility API or the pasteboard, and Swift never knows application names, templates, or
providers.

**v1 = the output of `capture-spike` at `main` `3a76a9d` (PR #1).** This document describes that
behaviour exactly; it does not add anything. Changing any item below bumps the version, and the
Python client and its tests change in the same commit (PRD NFR-10).

## Invocation

```sh
capture-spike --once --max-text 0 [--tiers 1,2,3] [--no-enhanced-ax] [--tier1-retries N]
```

| Flag | Meaning | Default | Caller guidance |
| --- | --- | --- | --- |
| `--once` | Run the chain once on the frontmost app, print one line, exit. | off (monitor mode) | **Always pass it.** Monitor mode is the spike's own tool and is not part of this contract. |
| `--max-text N` | Truncate `text` to N characters, appending `…`. `0` = unlimited. | **200** | **Always pass `0`.** The default exists for log readability; an application needs the whole selection. If `text.count != textLength`, the text was truncated. |
| `--tiers 1,2,3` | Comma-separated subset of `1,2,3`; always run in ascending order. | `1,2,3` | Capture Policy expresses itself here — e.g. `--tiers 1` when Secure Input is active or the app must never see a Clipboard Tier. |
| `--no-enhanced-ax` | Do not set `AXEnhancedUserInterface` on Chromium/Electron apps; also disables Tier 1 retries. | enhanced AX on | Leave the default unless measuring. |
| `--tier1-retries N` | Extra Tier 1 reads, 150 ms apart, after a failure — Chromium/Electron only. | `0` | Leave `0` (measured as a net loss). |

`--out FILE` and `--help` exist but are not part of the contract (`--out` duplicates stdout to a
file; the application persists its own records).

## Process behaviour

- **Trust check first.** If the process is not trusted for Accessibility, nothing is captured: the
  macOS prompt is requested once, stderr names the host application that needs the grant, exit **2**.
- **One line on stdout, flushed immediately.** In `--once` mode exactly one JSON line is written to
  stdout and `fflush`ed *before* the process waits for the late-copy guard. After a Clipboard Tier
  (2 or 3) the process may stay alive for up to **1 s** after the line to revert a copy that lands
  late; after a Tier 1 hit or a `no-selection` gate it exits at once.
  - Read the line as soon as it arrives and proceed; do not wait on exit for the result.
  - **Never kill the process** after reading the line — that defeats the clipboard restore. Let it
    finish in the background; it always terminates within the guard window.
- **stderr is for humans.** Status text only; nothing on stderr is machine-parsed. Pass it through
  to logs or discard it.
- **Frontmost app is whatever is frontmost at spawn time.** The caller is responsible for not
  stealing focus before spawning (the Panel must be non-activating).
- **Reentrancy.** The binary keeps no state between runs. Callers should not run two instances at
  once: both would touch the same pasteboard.

## Exit codes

| Code | Meaning | Client mapping |
| --- | --- | --- |
| `0` | A tier produced text (`tier` is 1–3, `text` present). | success |
| `1` | Chain ran, no text (`tier` is `null`, `error` present). Not a fault. | capture failed — show the state, name the app |
| `2` | Accessibility not granted, or the global monitor could not be installed. No JSON line. | "app not set up" state; stderr contains the host app name to grant |
| `64` | Bad arguments. No JSON line. | programming error on the caller's side |

## The JSON line

Keys are sorted; `/` is not escaped; `ts` is ISO‑8601 UTC with second precision. The line has no
trailing content after the closing brace.

```json
{"app":"com.apple.Notes","attempts":[{"ms":4.1,"ok":true,"tier":1}],"bounds":{"h":18.0,"w":280.0,"x":312.0,"y":544.5},
 "boundsMs":1.2,"boundsSource":"range","secureInput":false,"text":"Select text anywhere…","textLength":412,
 "tier":1,"totalMs":6.8,"ts":"2026-09-12T23:40:01Z"}
```

Miss:

```json
{"app":"com.google.Chrome","attempts":[{"error":"empty","ms":12.3,"ok":false,"role":"AXWebArea","tier":1},
 {"error":"timeout","ms":151.0,"ok":false,"tier":2},{"error":"timeout","ms":150.4,"ok":false,"tier":3}],
 "error":"exhausted","secureInput":false,"tier":null,"totalMs":318.9,"ts":"2026-09-13T20:51:07Z"}
```

### Top-level fields

| Key | Type | Present | Meaning |
| --- | --- | --- | --- |
| `ts` | string | always | Capture start, ISO‑8601 UTC. |
| `app` | string | always | Bundle identifier of the frontmost app; falls back to its localized name, then `pid:N`. |
| `tier` | int or `null` | **always** | Winning tier (1, 2, 3) or `null` on a miss. Always encoded so misses are greppable. |
| `text` | string | on hit | The selection. Truncated only if `--max-text` > 0. |
| `textLength` | int | on hit | Character count of the untruncated text. |
| `bounds` | object `{x,y,w,h}` | on hit | Selection rect in **`NSScreen` coordinates** (origin bottom-left of the primary display, points). `w = h = 0` when `boundsSource` is `mouse`. |
| `boundsSource` | `"range"` \| `"frame"` \| `"mouse"` | on hit | `range` = `AXBoundsForRange` (accurate); `frame` = focused element's `AXFrame`, accepted only if ≤ 25 % of its display and containing the pointer; `mouse` = pointer location. |
| `boundsMs` | number | on hit | Elapsed ms of the bounds chain alone. |
| `secureInput` | bool | always | `IsSecureEventInputEnabled()` at capture time. A *signal* for policy, not a guard: synthetic ⌘C is still delivered under Secure Keyboard Entry. |
| `attempts` | array | always | One entry per tier that ran, in order. Empty if the chain failed before any tier (`no-frontmost-app`). |
| `totalMs` | number | always | Whole chain including bounds. **Swift-side latency only** — spawn cost is measured by the caller and reported separately. |
| `error` | string | on miss | `no-selection` (Edit > Copy was disabled — Clipboard Tiers skipped, pasteboard untouched), `exhausted` (every enabled tier failed), or `no-frontmost-app`. |

### `attempts[]` entries

| Key | Type | Present | Meaning |
| --- | --- | --- | --- |
| `tier` | int | always | 1, 2 or 3. |
| `ok` | bool | always | |
| `ms` | number | always | Elapsed ms for this tier, including retries and (for the first tier) the first-contact AX cost. |
| `error` | string | on failure | Tier 1: `no-focused-element`, `no-selected-text-attr`, `empty`. Tier 2/3: `no-selection`, `no-copy-menu-item`, `press-failed`, `post-failed`, `timeout`, `no-text`, `empty`. |
| `retries` | int | when > 0 | Extra Tier 1 reads performed (Chromium/Electron only). |
| `axError` | int | Tier 1 failure | Raw `AXError` (e.g. `-25212` `kAXErrorNoValue`, `-25204` `kAXErrorCannotComplete`). |
| `role` | string | Tier 1 failure | `AXRole` of the focused element (e.g. `AXWebArea`, `AXTextArea`). |

Clients must ignore unknown keys, so additive fields can ship without a version bump; removing or
re-typing a key, changing an exit code, or changing when a line is written requires v2.

## Semantics the caller must honour

These are measured behaviours of the chain, not bugs to work around in Swift:

- **Line-copying editors.** VS Code and IntelliJ copy the *whole current line* on ⌘C when nothing is
  selected, so a Tier 2 hit after a Tier 1 `empty` in those apps is a false positive. Capture Policy
  treats Tier 1 `empty` as "no selection" there (or passes `--tiers 1`).
- **Always-enabled Copy.** The `no-selection` gate only fires in AppKit text apps (Notes, Terminal).
  In Chrome, VS Code, IntelliJ, Tk apps and Preview a non-selection invocation runs the Clipboard
  Tiers and takes ~200–400 ms to fail — plan the miss-path UX for that.
- **Safari is Tier 2** until the WebKit text-marker Tier 1 exists; `boundsSource` will be `mouse`.
- **Cold first contact** with an app costs 50–160 ms once; warm captures are ≤ 40 ms on Tier 1 apps.
- **Rich content that is not text** (Preview copying a page as PDF+TIFF) yields `no-text`, not a hit.

## Python client obligations (summary)

1. Spawn with `--once --max-text 0` plus policy flags; capture stdout and stderr separately.
2. Read the first stdout line; parse; map exit code → result type. Do not block on process exit
   for the result; do not kill the process.
3. Record spawn-to-parse wall time alongside `totalMs`; report both.
4. On exit 2, surface the host application named on stderr.
5. Treat any JSON key not listed here as ignorable.

## Change log

- **v1 — 2026-09-14.** Frozen from `main` `3a76a9d`. No code change.
