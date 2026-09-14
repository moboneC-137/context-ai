"""The floating Panel (PRD FR-13 – FR-16). `placement` and `state` are pure; `panel` needs AppKit."""

from .placement import GAP, Rect, place_panel
from .state import (
    Actions,
    Error,
    ErrorKind,
    Loading,
    PanelState,
    Result,
    state_for_capture_miss,
    state_for_exception,
    state_for_result,
)

__all__ = [
    "GAP",
    "Actions",
    "Error",
    "ErrorKind",
    "Loading",
    "PanelState",
    "Rect",
    "Result",
    "place_panel",
    "state_for_capture_miss",
    "state_for_exception",
    "state_for_result",
]
