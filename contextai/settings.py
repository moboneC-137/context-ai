"""Persisted settings (PRD FR-25): a flat TOML file, CLI flags overriding it, and a tiny CLI.

    python -m contextai.settings show
    python -m contextai.settings set target_language Chinese
    python -m contextai.settings unset model
    python -m contextai.settings path

Path resolution (both here and in `contextai.app`): `--settings PATH` > `$CONTEXTAI_SETTINGS` >
`~/Library/Application Support/ContextAI/settings.toml`. Precedence at run time: CLI flag > file > the
code default. `excluded_apps` is the one key where the flag *adds* to the file's list instead of
replacing it.

There is deliberately no default target language (PRD Open Question 1: an explicit choice is required).
The API key never enters this file — it lives in the Keychain (NFR-5); an `api_key` key is rejected
like any other unknown key, and its value is never echoed.

Every validation error is one `SettingsError` listing every offending key (one line per key, sorted),
so a file with three mistakes is fixed in one round trip. This module imports no AppKit / Quartz.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .actions.engine import DEFAULT_SELECTION_CAP
from .diagnostics import DEFAULT_PATH as DEFAULT_DIAGNOSTICS_PATH
from .input.hotkey import DEFAULT_HOTKEY, HotkeyError, parse_hotkey

ENV_VAR = "CONTEXTAI_SETTINGS"
PROVIDERS: tuple[str, ...] = ("mock", "openai")
DEFAULT_PROVIDER = "mock"

STRING_KEYS: tuple[str, ...] = ("target_language", "provider", "model", "hotkey")
"""Stripped of surrounding whitespace on both CLI paths (`set` and app flags); file values stay as read."""

KEYS: tuple[str, ...] = (
    "target_language",
    "provider",
    "model",
    "hotkey",
    "auto_appear",
    "excluded_apps",
    "diagnostics",
    "selection_cap",
)

MISSING_LANGUAGE_MESSAGE = (
    "no target language configured — run: python -m contextai.settings set target_language <Language>"
)


def default_path() -> Path:
    """Evaluated per call (not at import) so tests can redirect `Path.home`."""
    return Path.home() / "Library" / "Application Support" / "ContextAI" / "settings.toml"


def resolve_path(cli: Path | str | None = None, env: Mapping[str, str] | None = None) -> Path:
    """`--settings PATH` > `$CONTEXTAI_SETTINGS` > the default under Application Support."""
    if cli:
        return Path(cli).expanduser()
    env = os.environ if env is None else env
    from_env = env.get(ENV_VAR, "").strip()
    if from_env:
        return Path(from_env).expanduser()
    return default_path()


class SettingsError(Exception):
    """Every problem at once. `problems` are sorted `key: expected …` lines; `path` names the file (or None
    when the offending values came from the command line)."""

    def __init__(self, problems: Sequence[str], path: Path | None = None) -> None:
        self.problems = sorted(problems)
        self.path = path
        super().__init__(self.problems, path)

    def __str__(self) -> str:
        where = f"settings file {self.path}" if self.path is not None else "command-line settings"
        return "\n".join([f"{where}: {len(self.problems)} problem(s)", *self.problems])


@dataclass(frozen=True)
class Settings:
    """What the file says. `None` = key not in the file (so `show` can tag each source)."""

    target_language: str | None = None
    provider: str | None = None
    model: str | None = None
    hotkey: str | None = None
    auto_appear: bool | None = None
    excluded_apps: tuple[str, ...] | None = None
    diagnostics: bool | None = None
    selection_cap: int | None = None

    def as_dict(self) -> dict[str, Any]:
        """Only the keys that are set, in file order; lists as lists."""
        out: dict[str, Any] = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if value is not None:
                out[f.name] = list(value) if isinstance(value, tuple) else value
        return out


@dataclass(frozen=True)
class Effective:
    """Run-time values after precedence: what `App` is built from."""

    target_language: str | None
    provider: str
    model: str | None
    hotkey: str
    auto_appear: bool
    excluded_apps: frozenset[str]
    diagnostics_path: Path | None
    selection_cap: int


# --- validation -----------------------------------------------------------------------------------


def _problems(values: Mapping[str, Any]) -> list[str]:
    """Every offending key, one line each, `key: expected …`. Values are never echoed for unknown keys."""
    problems: list[str] = []
    for key, value in values.items():
        if key not in KEYS:
            problems.append(f"{key}: unknown key (known keys: {', '.join(KEYS)})")
            continue
        if key in ("target_language", "model"):
            if not isinstance(value, str) or not value.strip():
                problems.append(f"{key}: expected a non-empty string")
        elif key == "provider":
            if not isinstance(value, str) or value not in PROVIDERS:
                problems.append(f"{key}: expected one of {', '.join(PROVIDERS)}")
        elif key == "hotkey":
            if not isinstance(value, str):
                problems.append(f"{key}: expected a string like {DEFAULT_HOTKEY!r}")
            else:
                try:
                    parse_hotkey(value)
                except HotkeyError:
                    problems.append(f"{key}: expected modifier(+modifier)+key, e.g. {DEFAULT_HOTKEY!r}")
        elif key in ("auto_appear", "diagnostics"):
            if not isinstance(value, bool):
                problems.append(f"{key}: expected true or false")
        elif key == "excluded_apps":
            if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) and v.strip() for v in value):
                problems.append(f"{key}: expected a list of bundle identifiers, e.g. [\"com.apple.Terminal\"]")
        elif key == "selection_cap":
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                problems.append(f"{key}: expected an integer > 0")
    return problems


def validate(values: Mapping[str, Any], path: Path | None = None) -> Settings:
    """Typed `Settings` from a raw mapping, or one `SettingsError` naming every problem."""
    problems = _problems(values)
    if problems:
        raise SettingsError(problems, path)
    typed = dict(values)
    if "excluded_apps" in typed:
        typed["excluded_apps"] = tuple(typed["excluded_apps"])
    return Settings(**typed)


# --- file I/O -------------------------------------------------------------------------------------


def load(path: Path) -> Settings:
    """Absent file → empty `Settings`. Malformed TOML or bad values → `SettingsError` (never a traceback)."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return Settings()
    except OSError as exc:
        raise SettingsError([f"file: {exc.strerror or exc}"], path) from exc
    try:
        values = tomllib.loads(raw.decode("utf-8-sig"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise SettingsError([f"toml: {exc}"], path) from exc
    return validate(values, path)


def _toml_string(value: str) -> str:
    # json.dumps yields a valid TOML basic string for everything but DEL, which TOML wants escaped.
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")


def dumps(settings: Settings) -> str:
    """Minimal serializer for this flat key set: strings, bools, ints and lists of strings."""
    lines = []
    for key, value in settings.as_dict().items():
        if isinstance(value, bool):
            rendered = "true" if value else "false"
        elif isinstance(value, int):
            rendered = str(value)
        elif isinstance(value, str):
            rendered = _toml_string(value)
        else:
            rendered = "[" + ", ".join(_toml_string(v) for v in value) + "]"
        lines.append(f"{key} = {rendered}")
    return "".join(f"{line}\n" for line in lines)


def save(settings: Settings, path: Path) -> None:
    """Atomic write (temp file + rename); creates the parent directories."""
    path = path.resolve()  # write through a symlink to its target, temp file beside it
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(dumps(settings))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# --- precedence -----------------------------------------------------------------------------------


def effective(
    settings: Settings,
    *,
    target_language: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    hotkey: str | None = None,
    auto_appear: bool | None = None,
    exclude: Iterable[str] = (),
    diagnostics: Path | None = None,
    no_diagnostics: bool = False,
    selection_cap: int | None = None,
) -> Effective:
    """CLI flag > file > code default. `exclude` unions with the file's list. Flag values get the same
    validation as file values (so `App` can no longer see a bad hotkey or cap)."""
    overrides: dict[str, Any] = {
        k: v
        for k, v in {
            "target_language": target_language,
            "provider": provider,
            "model": model,
            "hotkey": hotkey,
            "auto_appear": auto_appear,
            "selection_cap": selection_cap,
        }.items()
        if v is not None
    }
    exclude = list(exclude)
    if exclude:
        overrides["excluded_apps"] = exclude
    for key in STRING_KEYS:
        if isinstance(overrides.get(key), str):
            overrides[key] = overrides[key].strip()
    validate(overrides)  # raises SettingsError(path=None) for bad flags

    def pick(name: str, default: Any) -> Any:
        if name in overrides:
            return overrides[name]
        value = getattr(settings, name)
        return default if value is None else value

    if no_diagnostics:
        diagnostics_path: Path | None = None
    elif diagnostics is not None:
        diagnostics_path = diagnostics
    elif settings.diagnostics is False:
        diagnostics_path = None
    else:
        diagnostics_path = DEFAULT_DIAGNOSTICS_PATH

    return Effective(
        target_language=pick("target_language", None),
        provider=pick("provider", DEFAULT_PROVIDER),
        model=pick("model", None),
        hotkey=pick("hotkey", DEFAULT_HOTKEY),
        auto_appear=pick("auto_appear", False),
        excluded_apps=frozenset(settings.excluded_apps or ()) | frozenset(exclude),
        diagnostics_path=diagnostics_path,
        selection_cap=pick("selection_cap", DEFAULT_SELECTION_CAP),
    )


# --- CLI ------------------------------------------------------------------------------------------

_DEFAULTS: dict[str, Any] = {
    "provider": DEFAULT_PROVIDER,
    "hotkey": DEFAULT_HOTKEY,
    "auto_appear": False,
    "excluded_apps": (),
    "diagnostics": True,
    "selection_cap": DEFAULT_SELECTION_CAP,
}


def _render(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ", ".join(value)
    return str(value)


def show_lines(settings: Settings, path: Path) -> list[str]:
    """`key = value (file|default|unset)` per key; `model` unset → `(provider default)`; path last."""
    lines = []
    for key in KEYS:
        value = getattr(settings, key)
        if value is not None:
            lines.append(f"{key} = {_render(value)} (file)")
        elif key == "model":
            lines.append("model = (provider default)")
        elif key in _DEFAULTS:
            rendered = _render(_DEFAULTS[key])
            lines.append(f"{key} = {rendered} (default)" if rendered else f"{key} = (default)")
        else:
            lines.append(f"{key} = (unset)")
    lines.append(f"path = {path}")
    return lines


def parse_cli_value(key: str, text: str) -> Any:
    """`set K V` text → the type the file would hold. Unparseable text is passed through so `validate`
    reports it with the same wording as a bad file value."""
    if key in ("auto_appear", "diagnostics"):
        lowered = text.strip().lower()
        return {"true": True, "false": False}.get(lowered, text)
    if key == "selection_cap":
        try:
            return int(text.strip())
        except ValueError:
            return text
    if key == "excluded_apps":
        return [item.strip() for item in text.split(",") if item.strip()]
    return text.strip() if key in STRING_KEYS else text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m contextai.settings", description=__doc__.split("\n\n")[0])
    parser.add_argument("--settings", type=Path, default=None, metavar="PATH", help="settings file to use")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("show", help="every key with its value and source")
    p_set = sub.add_parser("set", help="write one key")
    p_set.add_argument("key", choices=KEYS)
    p_set.add_argument("value", help="bools: true/false; excluded_apps: comma-separated ('' = empty)")
    p_unset = sub.add_parser("unset", help="remove one key from the file")
    p_unset.add_argument("key", choices=KEYS)
    sub.add_parser("path", help="print the resolved settings path")
    args = parser.parse_args(argv)

    path = resolve_path(args.settings)
    if args.command == "path":
        print(path)
        return 0
    try:
        current = load(path)
        if args.command == "show":
            print("\n".join(show_lines(current, path)))
            return 0
        values = current.as_dict()
        if args.command == "set":
            values[args.key] = parse_cli_value(args.key, args.value)
            updated = validate(values, path)
            save(updated, path)
            print(f"{args.key} = {_render(getattr(updated, args.key))}")
            return 0
        if args.command == "unset":
            if args.key in values:
                del values[args.key]
                save(validate(values, path), path)
                print(f"{args.key} removed")
            else:
                print(f"{args.key} not set")
            return 0
    except SettingsError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"settings file {path}: {exc.strerror or exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
