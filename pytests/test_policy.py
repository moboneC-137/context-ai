"""FR-7 / FR-12: the three matrix-derived rules, applied on the Python side as data."""

from __future__ import annotations

from datetime import datetime, timezone

from contextai.capture import CaptureOptions, CaptureResult, TierAttempt
from contextai.policy import POLICY_EMPTY_ERROR, CapturePolicy

TS = datetime(2026, 9, 14, tzinfo=timezone.utc)


def hit(app: str, tier: int, attempts: tuple[TierAttempt, ...]) -> CaptureResult:
    return CaptureResult(
        ts=TS,
        app=app,
        tier=tier,
        secure_input=False,
        attempts=attempts,
        total_ms=30.0,
        text="the whole line",
        text_length=14,
        raw={"app": app, "tier": tier, "text": "the whole line"},
    )


EMPTY_THEN_COPY = (TierAttempt(1, False, 2.0, error="empty"), TierAttempt(2, True, 28.0))
NOVALUE_THEN_COPY = (TierAttempt(1, False, 2.0, error="no-value"), TierAttempt(2, True, 28.0))


def test_line_copying_editor_tier2_after_empty_is_a_miss():
    policy = CapturePolicy()
    result = policy.interpret(hit("com.microsoft.VSCode", 2, EMPTY_THEN_COPY))
    assert not result.is_hit and result.tier is None and result.error == POLICY_EMPTY_ERROR
    assert result.attempts == EMPTY_THEN_COPY  # evidence kept for diagnostics
    assert result.raw["text"] == "the whole line"  # raw is untouched; diagnostics redacts it


def test_rule_does_not_fire_elsewhere():
    policy = CapturePolicy()
    same = hit("com.apple.Safari", 2, EMPTY_THEN_COPY)
    assert policy.interpret(same) is same
    vscode_tier1 = hit("com.microsoft.VSCode", 1, (TierAttempt(1, True, 2.0),))
    assert policy.interpret(vscode_tier1) is vscode_tier1
    vscode_no_value = hit("com.microsoft.VSCode", 2, NOVALUE_THEN_COPY)
    assert policy.interpret(vscode_no_value) is vscode_no_value


def test_exclusion_list_only_affects_auto_appear():
    policy = CapturePolicy(excluded=frozenset({"com.apple.Terminal"}))
    assert policy.auto_appear_allowed("com.apple.Terminal") is False
    assert policy.auto_appear_allowed("com.apple.Notes") is True
    assert policy.auto_appear_allowed(None) is True


def test_per_app_tier_override_and_defaults():
    policy = CapturePolicy(tiers_by_app={"com.example.Vault": (1,)})
    assert policy.options_for("com.example.Vault") == CaptureOptions(tiers=(1,))
    assert policy.options_for("com.apple.Notes") == CaptureOptions()
    assert policy.options_for(None) == CaptureOptions()
