"""Runs the Swift `capture-spike` binary and parses its one JSON line (docs/capture-contract.md v1).

Contract obligations this module carries, in the order the contract lists them:

1. Spawn with `--once --max-text 0` plus policy flags; stdout and stderr are separate pipes.
2. Read the *first stdout line* and return as soon as it is parsed — the Swift process may stay alive
   for up to a second afterwards to revert a late clipboard copy, and it is never waited on for the
   result and never killed once a line has arrived. On a no-line timeout the caller gets
   `CaptureTimeout` at once, but the child is still not killed: the client waits a grace
   (`no_line_kill_after`, 10 s) and kills only a child that has *still* produced no line by then.
   The child runs in its own session, so a Ctrl-C in the caller's terminal never reaches it.
3. Spawn-to-parse wall time is recorded on the outcome next to the Swift-side `total_ms`.
4. Exit 2 raises `AccessibilityNotGranted` carrying the host application named on stderr.
5. Unknown JSON keys are ignored (`CaptureResult.from_json`).
"""

from __future__ import annotations

import json
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from .model import CaptureResult

EXIT_HIT = 0
EXIT_NO_TEXT = 1
EXIT_NOT_TRUSTED = 2
EXIT_USAGE = 64

# The late-copy guard's grace period in Swift (CaptureOptions.clipboardGrace); the process cannot
# outlive the first line by much more than this.
GUARD_GRACE_SECONDS = 1.0
# How long a caller should be prepared to wait for a child past its first line: the default for every
# `wait_settled` here and the bound the application uses on shutdown.
SETTLE_TIMEOUT_SECONDS = GUARD_GRACE_SECONDS + 1.0


class CaptureSpikeError(Exception):
    """Base class for failures of the binary itself (not for a normal miss, which is a result).

    When the child is still running after the failure (a timeout, an unparseable line), `handle`
    (a `_Reaper`) drains it in the background and the caller must hold it like a `CaptureOutcome`:
    the child may still own the pasteboard until `settled`.
    """

    def __init__(self, message: str, *, handle: "_Reaper | None" = None) -> None:
        super().__init__(message)
        self.handle = handle

    @property
    def settled(self) -> bool:
        return self.handle is None or self.handle.settled

    def wait_settled(self, timeout: float | None = SETTLE_TIMEOUT_SECONDS) -> int | None:
        """Join the background drain. Returns the exit code, or None if still running after `timeout`."""
        return None if self.handle is None else self.handle.wait_settled(timeout)


class AccessibilityNotGranted(CaptureSpikeError):
    """Exit 2: the host application that launched us is not trusted for Accessibility."""

    def __init__(self, host_app: str | None, stderr: str) -> None:
        self.host_app = host_app
        self.stderr = stderr
        who = host_app or "the application that launched this process"
        super().__init__(f"Accessibility is not granted to {who}")


class UsageError(CaptureSpikeError):
    """Exit 64: the client built bad arguments — a programming error on this side."""


class CaptureTimeout(CaptureSpikeError):
    """No JSON line arrived within `first_line_timeout`.

    The child is *not* killed here: `handle` keeps draining it in the background and kills only if no
    line has arrived by `no_line_kill_after` — a line seen at any point means a tier ran and the
    clipboard restore must be allowed to finish.
    """


@dataclass(frozen=True, slots=True)
class CaptureOptions:
    """Flags the Capture Policy may vary per invocation. Everything else is fixed by the contract."""

    tiers: tuple[int, ...] = (1, 2, 3)
    enhanced_ax: bool = True
    tier1_retries: int = 0

    def __post_init__(self) -> None:
        if not self.tiers or any(t not in (1, 2, 3) for t in self.tiers):
            raise ValueError(f"tiers must be a non-empty subset of (1, 2, 3), got {self.tiers!r}")
        if self.tier1_retries < 0:
            raise ValueError("tier1_retries must be >= 0")

    def to_args(self) -> list[str]:
        # `--once` and `--max-text 0` are not options: the contract requires them on every call.
        args = ["--once", "--max-text", "0", "--tiers", ",".join(str(t) for t in sorted(set(self.tiers)))]
        if not self.enhanced_ax:
            args.append("--no-enhanced-ax")
        if self.tier1_retries:
            args += ["--tier1-retries", str(self.tier1_retries)]
        return args


class _FirstLineReader:
    """Owns stdout until the first line: a thread that reads one line and records that it was seen."""

    def __init__(self, proc: subprocess.Popen[bytes]) -> None:
        assert proc.stdout is not None
        self.proc = proc
        self.line_seen = threading.Event()  # set only for a real line, never for EOF
        self._done = threading.Event()
        self.line: bytes = b""
        self.thread = threading.Thread(target=self._read, name="capture-spike-first-line", daemon=True)
        self.thread.start()

    def _read(self) -> None:
        line = self.proc.stdout.readline()  # type: ignore[union-attr]
        self.line = line
        if line:
            self.line_seen.set()
        self._done.set()

    def wait(self, timeout: float) -> bytes | None:
        """First stdout line without its newline; `b""` on EOF; `None` on timeout (still reading)."""
        if not self._done.wait(timeout):
            return None
        return self.line.rstrip(b"\r\n")


class _Reaper:
    """Drains and reaps a `capture-spike` process in the background.

    Normal path (first line already read): rest of stdout to EOF, stderr, `wait()`. No-line path
    (`reader` still reading, `kill_after` set): wait up to `kill_after` for the reader; kill only if
    it has *still* seen no line — a late line means a tier ran and the restore must finish.
    """

    def __init__(
        self,
        proc: subprocess.Popen[bytes],
        reader: _FirstLineReader | None = None,
        kill_after: float | None = None,
    ) -> None:
        self.proc = proc
        self.reader = reader
        self.kill_after = kill_after
        self.exit_code: int | None = None
        self.stderr = ""
        self.killed = False
        self.thread = threading.Thread(target=self._drain, name="capture-spike-drain", daemon=True)
        self.thread.start()

    def _drain(self) -> None:
        assert self.proc.stdout is not None and self.proc.stderr is not None
        if self.reader is not None:
            self.reader.thread.join(self.kill_after)
            if self.reader.thread.is_alive():
                self.kill_if_silent()
            self.reader.thread.join()  # the pipe closes with the process; stdout is ours from here
        self.proc.stdout.read()  # nothing more is expected; consuming keeps the pipe from blocking
        stderr = self.proc.stderr.read()
        self.exit_code = self.proc.wait()
        self.stderr = stderr.decode(errors="replace")

    @property
    def line_seen(self) -> bool:
        """Whether a first line ever arrived (always true on the normal path, where it was read first)."""
        return self.reader is None or self.reader.line_seen.is_set()

    def kill_if_silent(self) -> None:
        """The only kill the client ever performs: a child that has *still* produced no line.

        Nothing has reported, so no tier ran to completion and there is no clipboard state to protect.
        Re-checks right before the signal — a line landing between the deadline and here means the
        child must be left to finish its restore.
        """
        if self.settled or self.reader is None:
            return
        if self.reader.line_seen.wait(0.05):
            return
        self.killed = True
        self.proc.kill()

    @property
    def settled(self) -> bool:
        return not self.thread.is_alive()

    def wait_settled(self, timeout: float | None = SETTLE_TIMEOUT_SECONDS) -> int | None:
        """Join the background drain. Returns the exit code, or None if still running after `timeout`."""
        self.thread.join(timeout)
        return self.exit_code if self.settled else None


@dataclass(frozen=True, slots=True)
class CaptureOutcome:
    """A parsed result plus the still-running process behind it.

    `result` is complete and usable immediately. The process is drained and reaped in the background;
    `wait_settled()` blocks until it has exited (normally at once, or after the late-copy guard's
    grace period following a clipboard tier).
    """

    result: CaptureResult
    spawn_ms: float
    """Wall time from `Popen` to the parsed first line — the Python-side cost the contract asks for."""
    _reaper: _Reaper = field(repr=False, compare=False)

    @property
    def settled(self) -> bool:
        return self._reaper.settled

    @property
    def exit_code(self) -> int | None:
        """Exit code once settled, else None."""
        return self._reaper.exit_code if self.settled else None

    @property
    def stderr(self) -> str:
        """Everything the binary wrote to stderr (available once settled; empty before)."""
        return self._reaper.stderr if self.settled else ""

    def wait_settled(self, timeout: float | None = SETTLE_TIMEOUT_SECONDS) -> int | None:
        """Join the background drain. Returns the exit code, or None if still running after `timeout`."""
        return self._reaper.wait_settled(timeout)


class CaptureClient:
    """Spawns `capture-spike --once` and returns the first JSON line without waiting for exit."""

    def __init__(
        self, command: Sequence[str], *, first_line_timeout: float = 3.0, no_line_kill_after: float = 10.0
    ) -> None:
        """`command` is the binary plus any fixed leading arguments (e.g. `[sys.executable, script]`).

        `first_line_timeout` bounds how long `capture()` waits for the line before raising
        `CaptureTimeout`; `no_line_kill_after` is the further grace, measured from that timeout, after
        which a child that has *still* written nothing is killed.
        """
        if not command:
            raise ValueError("command must name the capture-spike binary")
        if not first_line_timeout > 0:
            raise ValueError(f"first_line_timeout must be > 0, got {first_line_timeout!r}")
        if not no_line_kill_after >= 0:
            raise ValueError(f"no_line_kill_after must be >= 0, got {no_line_kill_after!r}")
        self.command = list(command)
        self.first_line_timeout = first_line_timeout
        self.no_line_kill_after = no_line_kill_after

    @classmethod
    def for_binary(cls, path: str | Path, **kwargs: float) -> "CaptureClient":
        binary = Path(path)
        if not binary.is_file():
            raise FileNotFoundError(f"capture-spike binary not found at {binary}")
        return cls([str(binary)], **kwargs)

    def capture(self, options: CaptureOptions = CaptureOptions()) -> CaptureOutcome:
        argv = self.command + options.to_args()
        started = time.perf_counter()
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            # Own session: a Ctrl-C in our terminal (SIGINT to the foreground process group) must never
            # reach a child that is inside its late-copy guard window.
            start_new_session=True,
        )
        assert proc.stdout is not None and proc.stderr is not None

        reader = _FirstLineReader(proc)
        first_line = reader.wait(self.first_line_timeout)
        if first_line is None:
            # Nothing reported yet. The caller learns now; the child keeps running under the reaper,
            # which kills only if there is still no line after `no_line_kill_after` (a tier that is
            # merely slow must be allowed to restore the clipboard). stderr is not available yet.
            raise CaptureTimeout(
                f"capture-spike produced no output within {self.first_line_timeout:.1f}s "
                f"(child still running; killed only if silent for another {self.no_line_kill_after:.0f}s)",
                handle=_Reaper(proc, reader, kill_after=self.no_line_kill_after),
            )
        if first_line == b"":
            # EOF before any line: the binary exited without a result (exit 2 / 64 / crash).
            _, err = proc.communicate(timeout=self.first_line_timeout)
            stderr = err.decode(errors="replace")
            return_code = proc.returncode
            if return_code == EXIT_NOT_TRUSTED and "Accessibility" in stderr:
                # The message is part of the check: a foreign process (a wrong path, a shell wrapper)
                # can also exit 2, and that must not be reported as a permission problem.
                raise AccessibilityNotGranted(_host_app_from_stderr(stderr), stderr)
            if return_code == EXIT_USAGE:
                raise UsageError(f"capture-spike rejected {argv[1:]!r}: {stderr.strip()}")
            raise CaptureSpikeError(f"capture-spike exited {return_code} without a JSON line: {stderr.strip()}")

        try:
            payload = json.loads(first_line)
            result = CaptureResult.from_json(payload)
        except (ValueError, KeyError, TypeError) as exc:
            # A malformed line is a contract violation, not a miss. Still let the process finish —
            # and hand the caller its handle: the child may be inside its guard window.
            raise CaptureSpikeError(
                f"unparseable capture-spike line {first_line!r}: {exc}", handle=_Reaper(proc)
            ) from exc
        spawn_ms = (time.perf_counter() - started) * 1000.0

        return CaptureOutcome(result=result, spawn_ms=spawn_ms, _reaper=_Reaper(proc))


_HOST_LINE = re.compile(r"^\s{4}(\S.*?)\s*$", re.MULTILINE)


def _host_app_from_stderr(stderr: str) -> str | None:
    """The exit-2 message names the host app on its own indented line; return it if present."""
    match = _HOST_LINE.search(stderr)
    return match.group(1) if match else None
