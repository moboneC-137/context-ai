"""Actions as versioned data plus the engine that runs them through a Provider (PRD FR-17 – FR-19, FR-23)."""

from .engine import (
    DEFAULT_SELECTION_CAP,
    ActionEngine,
    ActionEngineError,
    ActionResult,
    EmptySelection,
    MissingParameter,
    UnknownAction,
)
from .loader import (
    BUILTIN_TEMPLATES_DIR,
    Template,
    TemplateError,
    load_template,
    load_templates,
)

__all__ = [
    "BUILTIN_TEMPLATES_DIR",
    "DEFAULT_SELECTION_CAP",
    "ActionEngine",
    "ActionEngineError",
    "ActionResult",
    "EmptySelection",
    "MissingParameter",
    "Template",
    "TemplateError",
    "UnknownAction",
    "load_template",
    "load_templates",
]
