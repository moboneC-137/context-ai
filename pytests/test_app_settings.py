"""FR-25 wiring: `contextai.app.main` resolves flag > file > default before `App` is built.

`App` is subclassed by a recorder whose `run()` is a no-op (so the real, AppKit-free `App.__init__`
still validates every kwarg but no run loop starts) and the capture binary is a placeholder file: only
the argument resolution is under test here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from contextai import app as app_module
from contextai.diagnostics import DEFAULT_PATH as DEFAULT_DIAGNOSTICS_PATH
from contextai.input import DEFAULT_HOTKEY
from contextai.providers import MockProvider, OpenAIProvider
from contextai.settings import ENV_VAR


class RecordingApp(app_module.App):
    """The real `App.__init__` (AppKit-free; the panel is created lazily in `run()`) plus a call record."""

    calls: list[tuple[tuple, dict]] = []

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        RecordingApp.calls.append((args, kwargs))

    def run(self) -> None:
        pass


@pytest.fixture
def harness(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("HOME", str(home))
    binary = tmp_path / "capture-spike"
    binary.write_text("#!/bin/sh\nexit 1\n")
    settings_file = tmp_path / "settings.toml"
    monkeypatch.setenv(ENV_VAR, str(settings_file))
    monkeypatch.setattr(app_module, "App", RecordingApp)
    RecordingApp.calls = []

    def run(*flags: str) -> int:
        return app_module.main(["--binary", str(binary), "--no-diagnostics", *flags])

    yield type("Harness", (), {"run": staticmethod(run), "file": settings_file, "binary": binary})()
    assert not (home / "Library").exists()


def built_app() -> tuple[tuple, dict]:
    assert len(RecordingApp.calls) == 1, RecordingApp.calls
    return RecordingApp.calls[0]


# --- matrix rows ------------------------------------------------------------------------------------


def test_file_absent_flag_supplies_language_and_defaults(harness):
    assert harness.run("--target-language", "Chinese") == 0
    args, kwargs = built_app()
    assert kwargs["target_language"] == "Chinese"
    assert kwargs["hotkey"] == DEFAULT_HOTKEY
    assert kwargs["auto_appear"] is False
    assert kwargs["policy"].excluded == frozenset()
    assert kwargs["diagnostics"] is None
    engine = args[1]
    assert isinstance(engine.provider, MockProvider)


def test_file_supplies_language_no_flag(harness):
    harness.file.write_text('target_language = "English"\n')
    assert harness.run("--provider", "mock") == 0
    assert built_app()[1]["target_language"] == "English"


def test_flag_overrides_file_provider(harness):
    harness.file.write_text('target_language = "Chinese"\nprovider = "openai"\n')
    assert harness.run("--provider", "mock") == 0
    assert isinstance(built_app()[0][1].provider, MockProvider)


def test_file_provider_and_model_build_openai_provider(harness):
    harness.file.write_text('target_language = "Chinese"\nprovider = "openai"\nmodel = "gpt-4.1-mini"\n')
    assert harness.run() == 0
    provider = built_app()[0][1].provider
    assert isinstance(provider, OpenAIProvider) and provider.model == "gpt-4.1-mini"


def test_boolean_override_no_auto_appear(harness):
    harness.file.write_text('target_language = "Chinese"\nauto_appear = true\n')
    assert harness.run("--no-auto-appear") == 0
    assert built_app()[1]["auto_appear"] is False


def test_boolean_file_value_survives_when_flag_omitted(harness):
    harness.file.write_text('target_language = "Chinese"\nauto_appear = true\n')
    assert harness.run() == 0
    assert built_app()[1]["auto_appear"] is True


def test_exclusions_union(harness):
    harness.file.write_text('target_language = "Chinese"\nexcluded_apps = ["A", "B"]\n')
    assert harness.run("--exclude", "C") == 0
    assert built_app()[1]["policy"].excluded == frozenset({"A", "B", "C"})


def test_model_and_cap_flags_override_file(harness):
    harness.file.write_text('target_language = "Chinese"\nprovider = "openai"\nmodel = "a"\nselection_cap = 10\n')
    assert harness.run("--model", "b", "--cap", "20") == 0
    engine = built_app()[0][1]
    assert engine.provider.model == "b"
    assert engine.selection_cap == 20


def test_empty_exclude_flag_exits_2(harness, capsys):
    assert harness.run("--target-language", "Chinese", "--exclude", "") == 2
    assert "excluded_apps: expected " in capsys.readouterr().err
    assert RecordingApp.calls == []


def test_hotkey_and_cap_from_file(harness):
    harness.file.write_text('target_language = "Chinese"\nhotkey = "cmd+shift+e"\nselection_cap = 123\n')
    assert harness.run() == 0
    args, kwargs = built_app()
    assert kwargs["hotkey"] == "cmd+shift+e"
    assert args[1].selection_cap == 123


def test_no_language_anywhere_exits_2_with_fix_it(harness, capsys):
    assert harness.run("--provider", "mock") == 2
    err = capsys.readouterr().err
    assert "no target language configured" in err
    assert "python -m contextai.settings set target_language" in err
    assert RecordingApp.calls == []


def test_unknown_key_exits_2_naming_key_and_path_not_value(harness, capsys):
    harness.file.write_text('target_language = "Chinese"\napi_key = "sk-SECRET"\n')
    assert harness.run() == 2
    err = capsys.readouterr().err
    assert "api_key" in err and str(harness.file) in err and "SECRET" not in err
    assert "Traceback" not in err
    assert RecordingApp.calls == []


def test_bad_values_exit_2_with_sorted_lines(harness, capsys):
    harness.file.write_text('selection_cap = 0\nhotkey = "space"\nauto_appear = "yes"\n')
    assert harness.run("--target-language", "Chinese") == 2
    lines = capsys.readouterr().err.splitlines()
    assert [ln.split(":")[0] for ln in lines[1:]] == ["auto_appear", "hotkey", "selection_cap"]


def test_malformed_toml_exits_2(harness, capsys):
    harness.file.write_text('target_language = "Chinese\n')
    assert harness.run() == 2
    err = capsys.readouterr().err
    assert "toml: " in err and str(harness.file) in err


def test_bad_flag_value_exits_2_not_traceback(harness, capsys):
    assert harness.run("--target-language", "Chinese", "--hotkey", "space") == 2
    err = capsys.readouterr().err
    assert "hotkey: expected " in err and "Traceback" not in err


# --- diagnostics and --settings ---------------------------------------------------------------------


def test_diagnostics_file_false_off_path_flag_on(tmp_path, harness):
    harness.file.write_text('target_language = "Chinese"\ndiagnostics = false\n')
    assert app_module.main(["--binary", str(harness.binary)]) == 0
    assert built_app()[1]["diagnostics"] is None

    RecordingApp.calls = []
    custom = tmp_path / "d.jsonl"
    assert app_module.main(["--binary", str(harness.binary), "--diagnostics", str(custom)]) == 0
    assert built_app()[1]["diagnostics"].path == custom


def test_diagnostics_default_on(harness):
    harness.file.write_text('target_language = "Chinese"\n')
    assert app_module.main(["--binary", str(harness.binary)]) == 0
    assert built_app()[1]["diagnostics"].path == DEFAULT_DIAGNOSTICS_PATH


def test_settings_flag_beats_env(tmp_path, harness):
    other = tmp_path / "other.toml"
    other.write_text('target_language = "Japanese"\n')
    harness.file.write_text('target_language = "Chinese"\n')
    assert harness.run("--settings", str(other)) == 0
    assert built_app()[1]["target_language"] == "Japanese"
