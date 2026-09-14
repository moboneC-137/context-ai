"""Runs the Swift `capture-spike` binary and parses its one JSON line (docs/capture-contract.md v1).

Contract obligations this module carries, in the order the contract lists them:

1. Spawn with `--once --max-text 0` plus policy flags; stdout and stderr are separate pipes.
2. Read the *first stdout line* and return as soon as it is parsed — the Swift process may stay alive
   for up to a second afterwards to revert a late clipboard copy, and it is never waited on for the
   result and never killed once a line has arrived. (The only kill is the no-line-at-all timeout,
   where nothing can be lost because no tier reported.)
3. Spawn-to-parse wall time is recorded on the outcome next to the Swift-side `total_ms`.
4. Exit 2 raises `AccessibilityNotGranted` carrying the host application named on stderr.
5. Unknown JSON keys are ignored (`CaptureResult.from_json`).
"""

from __future__ import annotations

import json
import queue
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
# outlive the first line by much more than this. Used only as a default for `wait_settled`.
GUARD_GRACE_SECONDS = 1.0


class CaptureSpikeError(Exception):
    """Base class for failures of the binary itself (not for a normal miss, which is a result)."""


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
    """No JSON line arrived within the deadline; the process was killed (nothing had reported yet)."""


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


class _Reaper:
    """Drains and reaps a `capture-spike` process in the background after its first line was read."""

    def __init__(self, proc: subprocess.Popen[bytes]) -> None:
        self.proc = proc
        self.exit_code: int | None = None
        self.stderr = ""
        self.thread = threading.Thread(target=self._drain, name="capture-spike-drain", daemon=True)
        self.thread.start()

    def _drain(self) -> None:
        assert self.proc.stdout is not None and self.proc.stderr is not None
        self.proc.stdout.read()  # nothing more is expected; consuming keeps the pipe from blocking
        stderr = self.proc.stderr.read()
        self.exit_code = self.proc.wait()
        self.stderr = stderr.decode(errors="replace")

    @property
    def settled(self) -> bool:
        return not self.thread.is_alive()


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

    def wait_settled(self, timeout: float | None = GUARD_GRACE_SECONDS + 1.0) -> int | None:
        """Join the background drain. Returns the exit code, or None if still running after `timeout`."""
        self._reaper.thread.join(timeout)
        return self.exit_code


class CaptureClient:
    """Spawns `capture-spike --once` and returns the first JSON line without waiting for exit."""

    def __init__(self, command: Sequence[str], *, first_line_timeout: float = 3.0) -> None:
        """`command` is the binary plus any fixed leading arguments (e.g. `[sys.executable, script]`)."""
        if not command:
            raise ValueError("command must name the capture-spike binary")
        self.command = list(command)
        self.first_line_timeout = first_line_timeout

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
        )
        assert proc.stdout is not None and proc.stderr is not None

        first_line = _read_first_line(proc, self.first_line_timeout)
        if first_line is None:
            # Nothing reported at all: no tier ran to completion, so there is no clipboard state to
            # protect. This is the one place the client is allowed to kill the process.
            proc.kill()
            _, err = proc.communicate()
            raise CaptureTimeout(
                f"capture-spike produced no output within {self.first_line_timeout:.1f}s: {err.decode(errors='replace').strip()}"
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
            # A malformed line is a contract violation, not a miss. Still let the process finish.
            _Reaper(proc)
            raise CaptureSpikeError(f"unparseable capture-spike line {first_line!r}: {exc}") from exc
        spawn_ms = (time.perf_counter() - started) * 1000.0

        return CaptureOutcome(result=result, spawn_ms=spawn_ms, _reaper=_Reaper(proc))


def _read_first_line(proc: subprocess.Popen[bytes], timeout: float) -> bytes | None:
    """First stdout line without its newline; `b""` on EOF; `None` on timeout."""
    assert proc.stdout is not None
    lines: queue.Queue[bytes] = queue.Queue(maxsize=1)

    def reader() -> None:
        lines.put(proc.stdout.readline())  # type: ignore[union-attr]

    threading.Thread(target=reader, name="capture-spike-first-line", daemon=True).start()
    try:
        return lines.get(timeout=timeout).rstrip(b"\r\n")
    except queue.Empty:
        return None


_HOST_LINE = re.compile(r"^\s{4}(\S.*?)\s*$", re.MULTILINE)


def _host_app_from_stderr(stderr: str) -> str | None:
    """The exit-2 message names the host app on its own indented line; return it if present."""
    match = _HOST_LINE.search(stderr)
    return match.group(1) if match else None
