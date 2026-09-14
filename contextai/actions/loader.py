"""Loads and validates Action Templates from versioned YAML files (PRD FR-19).

A template is data, not code: adding an Action means adding a file here (or pointing the loader at
another directory). Validation is strict at load time so a broken template fails at startup, not
when the user presses the button.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from string import Formatter
from typing import Any, Mapping

import yaml

BUILTIN_TEMPLATES_DIR = Path(__file__).with_name("templates")
SELECTION_FIELD = "selection"


class TemplateError(ValueError):
    """A template file is missing, malformed or self-inconsistent. Carries the path for the message."""


@dataclass(frozen=True, slots=True)
class Template:
    id: str
    version: int
    name: str
    description: str
    system: str
    user: str
    params: tuple[str, ...] = ()
    """Parameter names the caller must supply besides the selection (e.g. `target_language`)."""
    max_output_tokens: int | None = None
    max_selection_chars: int | None = None
    """Optional per-action cap; the engine applies the stricter of this and its own (FR-23)."""
    sentinels: Mapping[str, str] | None = None
    """Exact outputs with a meaning the engine interprets, e.g. `same_language` (FR-18)."""
    source: Path | None = None

    @property
    def label(self) -> str:
        return f"{self.id}@v{self.version}"

    def fields(self) -> set[str]:
        return _format_fields(self.system) | _format_fields(self.user)


def load_template(path: Path | str) -> Template:
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as err:
        raise TemplateError(f"{path}: not found") from err
    except yaml.YAMLError as err:
        raise TemplateError(f"{path}: invalid YAML: {err}") from err
    if not isinstance(data, Mapping):
        raise TemplateError(f"{path}: top level must be a mapping")
    return template_from_mapping(data, source=path)


def load_templates(directory: Path | str = BUILTIN_TEMPLATES_DIR) -> dict[str, Template]:
    """All `*.yaml` in a directory keyed by id; duplicate ids are an error, not a silent override."""
    directory = Path(directory)
    registry: dict[str, Template] = {}
    for path in sorted(directory.glob("*.yaml")):
        template = load_template(path)
        if template.id in registry:
            raise TemplateError(
                f"{path}: duplicate template id {template.id!r} (also in {registry[template.id].source})"
            )
        registry[template.id] = template
    if not registry:
        raise TemplateError(f"{directory}: no *.yaml templates found")
    return registry


def template_from_mapping(data: Mapping[str, Any], *, source: Path | None = None) -> Template:
    where = str(source) if source else "<template>"

    def require(key: str, kind: type) -> Any:
        if key not in data:
            raise TemplateError(f"{where}: missing required key {key!r}")
        value = data[key]
        if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
            raise TemplateError(f"{where}: {key!r} must be {kind.__name__}, got {type(value).__name__}")
        return value

    template_id = require("id", str)
    if not template_id.isidentifier():
        raise TemplateError(f"{where}: id {template_id!r} must be an identifier")
    version = require("version", int)
    if version < 1:
        raise TemplateError(f"{where}: version must be >= 1")

    params = data.get("params", [])
    if not isinstance(params, list) or not all(isinstance(p, str) and p.isidentifier() for p in params):
        raise TemplateError(f"{where}: 'params' must be a list of identifiers")
    if SELECTION_FIELD in params:
        raise TemplateError(f"{where}: {SELECTION_FIELD!r} is implicit and must not be listed in 'params'")

    sentinels = data.get("sentinels")
    if sentinels is not None and not (
        isinstance(sentinels, Mapping)
        and all(isinstance(k, str) and isinstance(v, str) and v for k, v in sentinels.items())
    ):
        raise TemplateError(f"{where}: 'sentinels' must map names to non-empty strings")

    template = Template(
        id=template_id,
        version=version,
        name=require("name", str),
        description=str(data.get("description", "")),
        system=require("system", str).strip(),
        user=require("user", str).strip(),
        params=tuple(params),
        max_output_tokens=_optional_positive_int(data, "max_output_tokens", where),
        max_selection_chars=_optional_positive_int(data, "max_selection_chars", where),
        sentinels=dict(sentinels) if sentinels else None,
        source=source,
    )

    allowed = {SELECTION_FIELD, *template.params}
    unknown = template.fields() - allowed
    if unknown:
        raise TemplateError(f"{where}: unknown placeholder(s) {sorted(unknown)}; declare them in 'params'")
    if SELECTION_FIELD not in _format_fields(template.user):
        raise TemplateError(f"{where}: 'user' must contain {{{SELECTION_FIELD}}}")
    unused = set(template.params) - template.fields()
    if unused:
        raise TemplateError(f"{where}: declared param(s) {sorted(unused)} are never used")
    return template


def _optional_positive_int(data: Mapping[str, Any], key: str, where: str) -> int | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise TemplateError(f"{where}: {key!r} must be a positive integer")
    return value


def _format_fields(text: str) -> set[str]:
    """Names of `{field}` placeholders; rejects conversions/format specs so templates stay plain."""
    try:
        parsed = list(Formatter().parse(text))
    except ValueError as err:  # unbalanced braces
        raise TemplateError(f"malformed placeholder syntax: {err}") from err
    fields: set[str] = set()
    for _, name, spec, conversion in parsed:
        if name is None:
            continue
        if spec or conversion or not name.isidentifier():
            raise TemplateError(f"placeholder {{{name}}} may not carry a format spec, conversion or attribute access")
        fields.add(name)
    return fields
