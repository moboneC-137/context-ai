"""FR-25: persisted settings — the TOML file, precedence, validation and the `contextai.settings` CLI.

Every test works on a temp file: `Path.home` is redirected so the real
`~/Library/Application Support/ContextAI/settings.toml` is provably never read or written.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

import pytest

from contextai import settings as mod
from contextai.actions import DEFAULT_SELECTION_CAP
from contextai.diagnostics import DEFAULT_PATH as DEFAULT_DIAGNOSTICS_PATH
from contextai.input import DEFAULT_HOTKEY
from contextai.settings import (
    MISSING_LANGUAGE_MESSAGE,
    Settings,
    SettingsError,
    dumps,
    effective,
    load,
    resolve_path,
    save,
    validate,
)


@pytest.fixture(autouse=True)
def fake_home(tmp_path, monkeypatch):
    """No test may touch the real home directory or inherit a real `CONTEXTAI_SETTINGS`."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("HOME", str(home))  # `Path.expanduser` reads $HOME, not `Path.home`
    monkeypatch.delenv(mod.ENV_VAR, raising=False)
    yield home
    assert not (home / "Library").exists(), "settings must not be written under the (fake) home"


@pytest.fixture
def settings_file(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "settings.toml"
    monkeypatch.setenv(mod.ENV_VAR, str(path))
    return path


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


# --- path resolution --------------------------------------------------------------------------------


def test_resolve_path_precedence(fake_home, monkeypatch, tmp_path):
    assert resolve_path() == fake_home / "Library" / "Application Support" / "ContextAI" / "settings.toml"
    monkeypatch.setenv(mod.ENV_VAR, str(tmp_path / "env.toml"))
    assert resolve_path() == tmp_path / "env.toml"
    assert resolve_path(tmp_path / "cli.toml") == tmp_path / "cli.toml"
    assert resolve_path("~/x.toml") == fake_home / "x.toml"


def test_blank_env_var_falls_back_to_default(fake_home, monkeypatch):
    monkeypatch.setenv(mod.ENV_VAR, "  ")
    assert resolve_path() == fake_home / "Library" / "Application Support" / "ContextAI" / "settings.toml"


# --- load / validate --------------------------------------------------------------------------------


def test_absent_file_is_empty_settings(tmp_path):
    assert load(tmp_path / "missing.toml") == Settings()


def test_load_full_file(tmp_path):
    path = write(
        tmp_path / "s.toml",
        'target_language = "Chinese"\nprovider = "openai"\nmodel = "gpt-4.1-mini"\nhotkey = "ctrl+alt+space"\n'
        'auto_appear = false\nexcluded_apps = ["com.jetbrains.intellij", "com.microsoft.VSCode"]\n'
        "diagnostics = true\nselection_cap = 8000\n",
    )
    assert load(path) == Settings(
        target_language="Chinese",
        provider="openai",
        model="gpt-4.1-mini",
        hotkey="ctrl+alt+space",
        auto_appear=False,
        excluded_apps=("com.jetbrains.intellij", "com.microsoft.VSCode"),
        diagnostics=True,
        selection_cap=8000,
    )


def test_unknown_key_names_key_and_path_never_value(tmp_path):
    path = write(tmp_path / "s.toml", 'api_key = "sk-SECRET-VALUE"\n')
    with pytest.raises(SettingsError) as info:
        load(path)
    message = str(info.value)
    assert "api_key" in message and str(path) in message
    assert "SECRET" not in message and "sk-" not in message
    assert info.value.path == path


def test_bad_values_one_error_three_sorted_lines(tmp_path):
    path = write(tmp_path / "s.toml", 'selection_cap = 0\nhotkey = "space"\nauto_appear = "yes"\n')
    with pytest.raises(SettingsError) as info:
        load(path)
    problems = info.value.problems
    assert [p.split(":")[0] for p in problems] == ["auto_appear", "hotkey", "selection_cap"]
    assert all(": expected " in p for p in problems)
    assert str(info.value).splitlines()[1:] == problems


@pytest.mark.parametrize(
    "text",
    ['target_language = "Chinese\n', 'model = "a"\nmodel = "b"\n'],  # syntax error / duplicate key
)
def test_malformed_toml_is_a_settings_error_with_path(tmp_path, text):
    path = write(tmp_path / "s.toml", text)
    with pytest.raises(SettingsError) as info:
        load(path)
    assert str(path) in str(info.value)
    assert info.value.problems[0].startswith("toml: ")


@pytest.mark.parametrize(
    "values",
    [
        {"provider": "anthropic"},
        {"provider": 1},
        {"target_language": ""},
        {"model": ""},
        {"excluded_apps": "com.apple.Terminal"},
        {"excluded_apps": ["", "com.apple.Terminal"]},
        {"excluded_apps": [1]},
        {"selection_cap": True},
        {"selection_cap": -5},
        {"selection_cap": "8000"},
        {"diagnostics": "on"},
        {"hotkey": 5},
        {"hotkey": "ctrl+nosuchkey"},
    ],
)
def test_validate_rejects_each_bad_value(values):
    with pytest.raises(SettingsError) as info:
        validate(values)
    (problem,) = info.value.problems
    assert problem.startswith(f"{next(iter(values))}: expected ")


def test_load_unreadable_path_is_a_settings_error(tmp_path):
    directory = tmp_path / "settings.toml"
    directory.mkdir()
    with pytest.raises(SettingsError) as info:
        load(directory)
    assert info.value.path == directory
    assert info.value.problems[0].startswith("file: ")


def test_load_accepts_utf8_bom(tmp_path):
    path = tmp_path / "s.toml"
    path.write_bytes(b'\xef\xbb\xbftarget_language = "Chinese"\n')
    assert load(path) == Settings(target_language="Chinese")


def test_hotkey_message_does_not_repeat_itself():
    with pytest.raises(SettingsError) as info:
        validate({"hotkey": "space"})
    assert info.value.problems == [f"hotkey: expected modifier(+modifier)+key, e.g. {DEFAULT_HOTKEY!r}"]


def test_validate_accepts_typed_values():
    assert validate({"excluded_apps": ["a", "b"], "selection_cap": 1}) == Settings(
        excluded_apps=("a", "b"), selection_cap=1
    )


# --- save / dumps -----------------------------------------------------------------------------------


def test_save_load_round_trip_creates_dirs_and_is_valid_toml(tmp_path):
    original = Settings(
        target_language="Chinese",
        provider="openai",
        model="gpt-4.1-mini",
        hotkey="ctrl+alt+space",
        auto_appear=False,
        excluded_apps=("com.jetbrains.intellij", "com.microsoft.VSCode"),
        diagnostics=True,
        selection_cap=8000,
    )
    path = tmp_path / "nested" / "deeper" / "settings.toml"
    save(original, path)
    assert load(path) == original
    assert tomllib.loads(path.read_text()) == original.as_dict()
    assert [p.name for p in path.parent.iterdir()] == ["settings.toml"], "temp file removed after rename"


def test_dumps_matches_design_note_surface():
    text = dumps(
        Settings(
            target_language="Chinese",
            provider="openai",
            model="gpt-4.1-mini",
            hotkey="ctrl+alt+space",
            auto_appear=False,
            excluded_apps=("com.jetbrains.intellij", "com.microsoft.VSCode"),
            diagnostics=True,
            selection_cap=8000,
        )
    )
    assert text == (
        'target_language = "Chinese"\n'
        'provider = "openai"\n'
        'model = "gpt-4.1-mini"\n'
        'hotkey = "ctrl+alt+space"\n'
        "auto_appear = false\n"
        'excluded_apps = ["com.jetbrains.intellij", "com.microsoft.VSCode"]\n'
        "diagnostics = true\n"
        "selection_cap = 8000\n"
    )


def test_save_writes_through_symlink(tmp_path):
    real = tmp_path / "real.toml"
    real.write_text("")
    link = tmp_path / "link.toml"
    link.symlink_to(real)
    save(Settings(target_language="Chinese"), link)
    assert link.is_symlink() and load(real) == Settings(target_language="Chinese")


def test_del_character_round_trips(tmp_path):
    settings = Settings(target_language="a\x7fb")
    path = tmp_path / "s.toml"
    save(settings, path)
    assert load(path) == settings


def test_dumps_only_set_keys_and_escapes_strings(tmp_path):
    settings = Settings(target_language='Zh "quoted" \\ 中文\ttab')
    assert dumps(settings) == 'target_language = "Zh \\"quoted\\" \\\\ 中文\\ttab"\n'
    path = tmp_path / "s.toml"
    save(settings, path)
    assert load(path) == settings


# --- precedence -------------------------------------------------------------------------------------


def test_effective_defaults_when_file_absent():
    config = effective(Settings(), target_language="Chinese")
    assert config.target_language == "Chinese"
    assert config.provider == "mock" and config.model is None
    assert config.hotkey == DEFAULT_HOTKEY and config.auto_appear is False
    assert config.excluded_apps == frozenset() and config.selection_cap == DEFAULT_SELECTION_CAP
    assert config.diagnostics_path == DEFAULT_DIAGNOSTICS_PATH


def test_effective_file_values_apply_without_flags():
    file = Settings(target_language="English", provider="openai", model="gpt-4.1-mini", auto_appear=True)
    config = effective(file)
    assert (config.target_language, config.provider, config.model, config.auto_appear) == (
        "English",
        "openai",
        "gpt-4.1-mini",
        True,
    )


def test_effective_flag_overrides_file():
    config = effective(
        Settings(provider="openai", hotkey="cmd+shift+e", selection_cap=10),
        provider="mock",
        hotkey="ctrl+alt+space",
        selection_cap=20,
    )
    assert (config.provider, config.hotkey, config.selection_cap) == ("mock", "ctrl+alt+space", 20)


def test_effective_boolean_override_both_directions():
    assert effective(Settings(auto_appear=True), auto_appear=False).auto_appear is False
    assert effective(Settings(auto_appear=False), auto_appear=True).auto_appear is True
    assert effective(Settings(auto_appear=True)).auto_appear is True


def test_effective_exclusions_union():
    config = effective(Settings(excluded_apps=("A", "B")), exclude=["C"])
    assert config.excluded_apps == frozenset({"A", "B", "C"})
    assert effective(Settings(), exclude=["C", "C"]).excluded_apps == frozenset({"C"})


def test_effective_diagnostics_rules(tmp_path):
    custom = tmp_path / "d.jsonl"
    assert effective(Settings(diagnostics=False)).diagnostics_path is None
    assert effective(Settings(diagnostics=False), diagnostics=custom).diagnostics_path == custom
    assert effective(Settings(diagnostics=True), no_diagnostics=True).diagnostics_path is None
    assert effective(Settings(), diagnostics=custom, no_diagnostics=True).diagnostics_path is None


def test_effective_no_language_anywhere_is_none():
    assert effective(Settings()).target_language is None
    assert "python -m contextai.settings set target_language" in MISSING_LANGUAGE_MESSAGE


def test_effective_strips_cli_strings_but_not_file_values():
    config = effective(Settings(model=" x "), target_language=" Chinese ", provider=" mock ", hotkey=" cmd+shift+e ")
    assert (config.target_language, config.provider, config.hotkey, config.model) == (
        "Chinese",
        "mock",
        "cmd+shift+e",
        " x ",
    )
    assert effective(Settings(), model=" x ").model == "x"


def test_effective_validates_flags_like_file_values():
    with pytest.raises(SettingsError) as info:
        effective(Settings(), hotkey="space", selection_cap=0)
    assert [p.split(":")[0] for p in info.value.problems] == ["hotkey", "selection_cap"]
    assert info.value.path is None
    assert str(info.value).startswith("command-line settings")


# --- CLI --------------------------------------------------------------------------------------------


def test_cli_path(settings_file, capsys):
    assert mod.main(["path"]) == 0
    assert capsys.readouterr().out.strip() == str(settings_file)


def test_cli_path_uses_settings_flag_over_env(settings_file, tmp_path, capsys):
    assert mod.main(["--settings", str(tmp_path / "cli.toml"), "path"]) == 0
    assert capsys.readouterr().out.strip() == str(tmp_path / "cli.toml")


def test_cli_show_with_file_absent(settings_file, capsys):
    assert mod.main(["show"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines == [
        "target_language = (unset)",
        "provider = mock (default)",
        "model = (provider default)",
        f"hotkey = {DEFAULT_HOTKEY} (default)",
        "auto_appear = false (default)",
        "excluded_apps = (default)",
        "diagnostics = true (default)",
        f"selection_cap = {DEFAULT_SELECTION_CAP} (default)",
        f"path = {settings_file}",
    ]
    assert not settings_file.exists()


def test_cli_show_tags_file_values(settings_file, capsys):
    write(settings_file, 'target_language = "Chinese"\nexcluded_apps = ["a", "b"]\nauto_appear = true\n')
    assert mod.main(["show"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert "target_language = Chinese (file)" in lines
    assert "excluded_apps = a, b (file)" in lines
    assert "auto_appear = true (file)" in lines
    assert "model = (provider default)" in lines
    assert lines[-1] == f"path = {settings_file}"


def test_cli_set_creates_dirs_and_prints_value(tmp_path, monkeypatch, capsys):
    path = tmp_path / "new" / "dir" / "settings.toml"
    monkeypatch.setenv(mod.ENV_VAR, str(path))
    assert mod.main(["set", "target_language", "Chinese"]) == 0
    assert capsys.readouterr().out.strip() == "target_language = Chinese"
    assert load(path) == Settings(target_language="Chinese")


def test_cli_set_parses_bools_ints_and_lists(settings_file, capsys):
    assert mod.main(["set", "auto_appear", "true"]) == 0
    assert mod.main(["set", "diagnostics", "False"]) == 0
    assert mod.main(["set", "selection_cap", "4000"]) == 0
    assert mod.main(["set", "excluded_apps", "com.a.One, com.b.Two"]) == 0
    assert load(settings_file) == Settings(
        auto_appear=True, diagnostics=False, selection_cap=4000, excluded_apps=("com.a.One", "com.b.Two")
    )
    assert mod.main(["set", "excluded_apps", ""]) == 0
    assert load(settings_file).excluded_apps == ()
    assert capsys.readouterr().out.splitlines()[-1] == "excluded_apps = "


def test_cli_set_strips_string_values(settings_file):
    assert mod.main(["set", "target_language", " Chinese "]) == 0
    assert mod.main(["set", "model", " x "]) == 0
    assert load(settings_file) == Settings(target_language="Chinese", model="x")


def test_cli_set_below_regular_file_exits_2(tmp_path, monkeypatch, capsys):
    blocker = tmp_path / "blocker"
    blocker.write_text("")
    monkeypatch.setenv(mod.ENV_VAR, str(blocker / "settings.toml"))
    assert mod.main(["set", "provider", "mock"]) == 2
    assert capsys.readouterr().err.startswith("settings file")
    assert not any(p.name.startswith(".settings.toml.") for p in tmp_path.iterdir())


def test_cli_set_unwritable_dir_exits_2_without_temp_file(tmp_path, monkeypatch, capsys):
    """`load` sees no file, `save` cannot create the temp file → `main`'s OSError path, exit 2."""
    if os.geteuid() == 0:
        pytest.skip("root ignores directory permissions")
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    monkeypatch.setenv(mod.ENV_VAR, str(ro / "settings.toml"))
    try:
        assert mod.main(["set", "provider", "mock"]) == 2
        assert capsys.readouterr().err.startswith("settings file")
        assert list(ro.iterdir()) == []
    finally:
        ro.chmod(0o700)


def test_save_removes_temp_file_when_replace_fails(tmp_path, monkeypatch):
    path = tmp_path / "dir" / "settings.toml"

    def fail(src, dst):
        raise OSError("simulated rename failure")

    monkeypatch.setattr(mod.os, "replace", fail)
    with pytest.raises(OSError):
        save(Settings(provider="mock"), path)
    assert list(path.parent.iterdir()) == []


def test_cli_set_keeps_other_keys(settings_file):
    write(settings_file, 'target_language = "Chinese"\nprovider = "openai"\n')
    assert mod.main(["set", "model", "gpt-4.1-mini"]) == 0
    assert load(settings_file) == Settings(target_language="Chinese", provider="openai", model="gpt-4.1-mini")


@pytest.mark.parametrize(
    ("key", "value"),
    [("auto_appear", "yes"), ("hotkey", "space"), ("selection_cap", "0"), ("selection_cap", "many"), ("provider", "x")],
)
def test_cli_set_invalid_exits_2_and_leaves_file_unchanged(settings_file, capsys, key, value):
    write(settings_file, 'target_language = "Chinese"\n')
    before = settings_file.read_text()
    assert mod.main(["set", key, value]) == 2
    err = capsys.readouterr().err
    assert f"{key}: expected " in err and str(settings_file) in err
    assert settings_file.read_text() == before


def test_cli_set_rejects_unknown_key_without_echoing_value(settings_file, capsys):
    with pytest.raises(SystemExit) as info:
        mod.main(["set", "api_key", "sk-SECRET"])
    assert info.value.code == 2
    assert "SECRET" not in capsys.readouterr().err


def test_cli_set_on_malformed_file_exits_2(settings_file, capsys):
    write(settings_file, 'target_language = "Chinese\n')
    before = settings_file.read_text()
    assert mod.main(["set", "provider", "mock"]) == 2
    assert "toml: " in capsys.readouterr().err
    assert settings_file.read_text() == before


def test_cli_unset_present_and_absent(settings_file, capsys):
    write(settings_file, 'target_language = "Chinese"\nmodel = "gpt-4.1-mini"\n')
    assert mod.main(["unset", "model"]) == 0
    assert capsys.readouterr().out.strip() == "model removed"
    assert load(settings_file) == Settings(target_language="Chinese")
    assert mod.main(["unset", "model"]) == 0
    assert capsys.readouterr().out.strip() == "model not set"
    assert load(settings_file) == Settings(target_language="Chinese")


def test_cli_show_on_bad_file_exits_2(settings_file, capsys):
    write(settings_file, 'api_key = "sk-SECRET"\n')
    assert mod.main(["show"]) == 2
    err = capsys.readouterr().err
    assert "api_key" in err and "SECRET" not in err
