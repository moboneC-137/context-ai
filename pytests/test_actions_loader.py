"""FR-19: templates are validated data; the two shipped templates load and are self-consistent."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from contextai.actions import (
    BUILTIN_TEMPLATES_DIR,
    TemplateError,
    load_template,
    load_templates,
)

MINIMAL = """
id: explain
version: 2
name: Explain
system: Explain things in {target_language}.
user: "{selection}"
params: [target_language]
"""


def write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_shipped_templates_load_and_declare_the_v1_actions():
    templates = load_templates()
    assert set(templates) == {"summarize", "translate"}
    assert templates["summarize"].params == ()
    assert templates["translate"].params == ("target_language",)
    assert templates["translate"].sentinels == {"same_language": "<<SAME_LANGUAGE>>"}
    assert all(t.version >= 1 and t.source and t.source.parent == BUILTIN_TEMPLATES_DIR for t in templates.values())


def test_minimal_template_round_trips(tmp_path: Path):
    template = load_template(write(tmp_path, "explain.yaml", MINIMAL))
    assert template.label == "explain@v2"
    assert template.fields() == {"selection", "target_language"}
    assert template.max_output_tokens is None and template.sentinels is None


def test_adding_an_action_is_adding_a_file(tmp_path: Path):
    """The registry is the directory: no code change registers a template."""
    write(tmp_path, "a.yaml", MINIMAL)
    write(tmp_path, "b.yaml", MINIMAL.replace("id: explain", "id: rewrite"))
    assert set(load_templates(tmp_path)) == {"explain", "rewrite"}


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda s: s.replace("version: 2\n", ""), "missing required key 'version'"),
        (lambda s: s.replace("version: 2", "version: '2'"), "'version' must be int"),
        (lambda s: s.replace("version: 2", "version: 0"), "version must be >= 1"),
        (lambda s: s.replace("id: explain", "id: not an id"), "must be an identifier"),
        (lambda s: s.replace("params: [target_language]", "params: []"), "unknown placeholder(s) ['target_language']"),
        (lambda s: s.replace("params: [target_language]", "params: [target_language, tone]"), "never used"),
        (lambda s: s.replace("params: [target_language]", "params: [target_language, selection]"), "implicit"),
        (lambda s: s.replace('user: "{selection}"', 'user: "no placeholder"'), "'user' must contain {selection}"),
        (lambda s: s.replace('user: "{selection}"', 'user: "{selection!r}"'), "format spec"),
        (lambda s: s.replace('user: "{selection}"', 'user: "{selection"'), "malformed placeholder"),
        (lambda s: s + "max_output_tokens: -5\n", "'max_output_tokens' must be a positive integer"),
        (lambda s: s + "sentinels: {same_language: ''}\n", "'sentinels' must map names to non-empty strings"),
    ],
)
def test_invalid_templates_fail_at_load_time(tmp_path: Path, mutation, message):
    path = write(tmp_path, "bad.yaml", mutation(MINIMAL))
    with pytest.raises(TemplateError, match=re.escape(message)):
        load_template(path)


def test_errors_name_the_file(tmp_path: Path):
    path = write(tmp_path, "bad.yaml", "id: x\n")
    with pytest.raises(TemplateError, match="bad.yaml"):
        load_template(path)
    with pytest.raises(TemplateError, match="not found"):
        load_template(tmp_path / "missing.yaml")
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(TemplateError, match="no \\*.yaml templates"):
        load_templates(empty)


def test_duplicate_ids_are_rejected(tmp_path: Path):
    write(tmp_path, "a.yaml", MINIMAL)
    write(tmp_path, "b.yaml", MINIMAL)
    with pytest.raises(TemplateError, match="duplicate template id 'explain'"):
        load_templates(tmp_path)


def test_literal_braces_are_doubled(tmp_path: Path):
    text = MINIMAL.replace(
        "system: Explain things in {target_language}.", "system: 'Reply as {{\"k\": 1}} in {target_language}.'"
    )
    template = load_template(write(tmp_path, "j.yaml", text))
    assert template.fields() == {"selection", "target_language"}
    assert template.system.format_map({"target_language": "English"}) == 'Reply as {"k": 1} in English.'
