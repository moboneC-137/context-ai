"""Capture Policy (PRD FR-7, FR-12): per-application rules, as data, applied on the Python side.

Swift never knows application names; Python chooses the flags it passes and reinterprets the result.
The three rules the B5 matrix produced live here:

1. **Line-copying editors** (VS Code, IntelliJ) copy the whole current line on ⌘C with nothing selected,
   so a Clipboard-Tier hit after a Tier 1 `empty` is a false positive there → treated as no selection.
2. **Exclusion list**: Auto-Appear never fires in these apps; the Hotkey still works (FR-12).
3. **Per-app tier overrides**: e.g. `--tiers 1` for an app that must never see a Clipboard Tier.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Mapping

from .capture import CaptureOptions, CaptureResult

LINE_COPYING_EDITORS: frozenset[str] = frozenset(
    {"com.microsoft.VSCode", "com.jetbrains.intellij", "com.jetbrains.intellij.ce"}
)
POLICY_EMPTY_ERROR = "policy-empty-selection"
"""`error` value the policy writes when it downgrades a Clipboard-Tier hit to a miss (rule 1)."""


@dataclass(frozen=True)
class CapturePolicy:
    line_copying: frozenset[str] = LINE_COPYING_EDITORS
    excluded: frozenset[str] = frozenset()
    tiers_by_app: Mapping[str, tuple[int, ...]] = field(default_factory=dict)
    default_options: CaptureOptions = CaptureOptions()

    def options_for(self, bundle_id: str | None) -> CaptureOptions:
        """Flags for the next spawn. Unknown or missing bundle id → defaults."""
        tiers = self.tiers_by_app.get(bundle_id or "")
        return replace(self.default_options, tiers=tiers) if tiers else self.default_options

    def auto_appear_allowed(self, bundle_id: str | None) -> bool:
        return (bundle_id or "") not in self.excluded

    def interpret(self, result: CaptureResult) -> CaptureResult:
        """Apply result-side rules. Returns the same object when nothing applies."""
        if result.app in self.line_copying and result.is_hit and result.tier in (2, 3) and _tier1_was_empty(result):
            return replace(result, tier=None, text=None, text_length=None, error=POLICY_EMPTY_ERROR)
        return result


def _tier1_was_empty(result: CaptureResult) -> bool:
    return any(a.tier == 1 and not a.ok and a.error == "empty" for a in result.attempts)
