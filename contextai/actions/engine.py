"""Action Engine: Template + Selection (+ params) → Prompt → Provider → ActionResult (PRD FR-17 – FR-23).

Sits between capture and the provider. It owns three rules the UI must never re-implement:
the selection size cap (checked before any request, so a refused Selection costs nothing — NFR-9),
the required-parameter check (no silent default target language — Open Question 1), and the
sentinel interpretation for "already in the target language" (FR-18).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Mapping

from ..providers.base import Completion, Prompt, Provider, SelectionTooLong
from .loader import SELECTION_FIELD, Template, load_templates

DEFAULT_SELECTION_CAP = 8_000
"""Characters. Open Question 5 leaves the number to architecture; this is the working default."""


class ActionEngineError(ValueError):
    """Caller-side misuse: unknown action, missing parameter, empty selection. Not a provider error."""


class UnknownAction(ActionEngineError):
    pass


class MissingParameter(ActionEngineError):
    def __init__(self, action: str, names: list[str]) -> None:
        super().__init__(f"action {action!r} needs parameter(s) {names}")
        self.action = action
        self.names = names


class EmptySelection(ActionEngineError):
    pass


@dataclass(frozen=True, slots=True)
class ActionResult:
    action: str
    version: int
    text: str
    same_language: bool
    """True when the template's `same_language` sentinel came back: nothing to translate (FR-18)."""
    provider: str
    model: str
    elapsed_ms: float
    completion: Completion

    @property
    def label(self) -> str:
        return f"{self.action}@v{self.version}"


class ActionEngine:
    def __init__(
        self,
        provider: Provider,
        templates: Mapping[str, Template] | None = None,
        *,
        selection_cap: int = DEFAULT_SELECTION_CAP,
    ) -> None:
        if selection_cap < 1:
            raise ValueError("selection_cap must be >= 1")
        self.provider = provider
        self.templates = dict(templates) if templates is not None else load_templates()
        self.selection_cap = selection_cap

    def template(self, action: str) -> Template:
        try:
            return self.templates[action]
        except KeyError:
            raise UnknownAction(f"no action template {action!r}; have {sorted(self.templates)}") from None

    def cap_for(self, template: Template) -> int:
        return min(self.selection_cap, template.max_selection_chars or self.selection_cap)

    def render(self, template: Template, selection: str, params: Mapping[str, str] | None = None) -> Prompt:
        """Pure: validates inputs and fills the template. Used by `run` and by the Evals Harness."""
        params = dict(params or {})
        if not selection.strip():
            raise EmptySelection("selection is empty")
        cap = self.cap_for(template)
        if len(selection) > cap:
            raise SelectionTooLong(len(selection), cap)
        missing = [name for name in template.params if not str(params.get(name, "")).strip()]
        if missing:
            raise MissingParameter(template.id, missing)
        values = {name: params[name] for name in template.params}
        values[SELECTION_FIELD] = selection
        return Prompt(
            system=template.system.format_map(values),
            user=template.user.format_map(values),
            max_output_tokens=template.max_output_tokens,
        )

    def run(self, action: str, selection: str, params: Mapping[str, str] | None = None) -> ActionResult:
        """One provider request per call (NFR-9). Re-running on the same text is the retry path."""
        template = self.template(action)
        prompt = self.render(template, selection, params)
        started = time.perf_counter()
        completion = self.provider.complete(prompt)
        elapsed_ms = (time.perf_counter() - started) * 1000
        return self.interpret(template, completion, elapsed_ms)

    @staticmethod
    def interpret(template: Template, completion: Completion, elapsed_ms: float = 0.0) -> ActionResult:
        text = completion.text.strip()
        sentinel = (template.sentinels or {}).get("same_language")
        same_language = bool(sentinel) and text == sentinel
        return ActionResult(
            action=template.id,
            version=template.version,
            text="" if same_language else text,
            same_language=same_language,
            provider=completion.provider,
            model=completion.model,
            elapsed_ms=elapsed_ms,
            completion=completion,
        )
