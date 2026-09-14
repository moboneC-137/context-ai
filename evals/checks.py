"""Declarative checks a Golden Set case can assert on an Action's output (PRD FR-27).

Exact string match is deliberately absent: Summarize and Translate have many correct outputs. Each
check is a pure function of (spec, output, input, result) and reports a `CheckResult` with a
human-readable detail so a failing eval run explains itself.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Callable, Mapping

from contextai.actions import ActionResult


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    passed: bool
    detail: str


class CheckSpecError(ValueError):
    """A golden case declares a check the harness does not know or with a malformed value."""


# --- language (script-level) -------------------------------------------------------------------
#
# Without a language-ID dependency the harness checks the dominant *writing system* of the output.
# That is enough to catch the failure modes the templates guard against (a summary answering in
# English to Chinese input, a translation echoing the source) but cannot tell English from French.
# Codes map to scripts; extend the table rather than the logic when a golden set needs more.

SCRIPT_OF_LANGUAGE: Mapping[str, str] = {
    "en": "latin",
    "fr": "latin",
    "de": "latin",
    "es": "latin",
    "it": "latin",
    "pt": "latin",
    "nl": "latin",
    "zh": "han",
    "ja": "japanese",
    "ko": "hangul",
    "ru": "cyrillic",
    "uk": "cyrillic",
    "el": "greek",
    "ar": "arabic",
    "he": "hebrew",
    "th": "thai",
    "hi": "devanagari",
}

_SCRIPT_RANGES: tuple[tuple[str, range], ...] = (
    ("han", range(0x4E00, 0xA000)),
    ("han", range(0x3400, 0x4DC0)),
    ("kana", range(0x3040, 0x3100)),
    ("hangul", range(0xAC00, 0xD7B0)),
    ("hangul", range(0x1100, 0x1200)),
    ("cyrillic", range(0x0400, 0x0530)),
    ("greek", range(0x0370, 0x0400)),
    ("arabic", range(0x0600, 0x0700)),
    ("hebrew", range(0x0590, 0x0600)),
    ("thai", range(0x0E00, 0x0E80)),
    ("devanagari", range(0x0900, 0x0980)),
)


def script_counts(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for char in text:
        if not char.isalpha():
            continue
        code = ord(char)
        for script, span in _SCRIPT_RANGES:
            if code in span:
                counts[script] = counts.get(script, 0) + 1
                break
        else:
            if unicodedata.name(char, "").startswith("LATIN"):
                counts["latin"] = counts.get("latin", 0) + 1
            else:
                counts["other"] = counts.get("other", 0) + 1
    return counts


def dominant_script(text: str) -> str | None:
    """Most frequent script among letters; Han text with any kana is classified as `japanese`."""
    counts = script_counts(text)
    if not counts:
        return None
    if counts.get("kana", 0) and counts.get("kana", 0) + counts.get("han", 0) >= max(counts.values()):
        return "japanese"
    return max(counts, key=counts.__getitem__)


_NON_PROSE = re.compile(r"`[^`\n]*`|https?://\S+|\S+@\S+")


def prose_only(text: str) -> str:
    """Drops code spans, URLs and e-mail addresses: they are copied verbatim in any language."""
    return _NON_PROSE.sub(" ", text)


def check_language(expected: str, output: str) -> CheckResult:
    want = SCRIPT_OF_LANGUAGE.get(expected.lower())
    if want is None:
        raise CheckSpecError(f"language {expected!r} is not in SCRIPT_OF_LANGUAGE")
    got = dominant_script(prose_only(output))
    return CheckResult("language", got == want, f"expected {expected} ({want}), output script is {got}")


# --- content ------------------------------------------------------------------------------------


def check_must_contain(terms: list[str], output: str) -> CheckResult:
    missing = [t for t in terms if t.lower() not in output.lower()]
    return CheckResult("must_contain", not missing, f"missing {missing}" if missing else f"all {len(terms)} present")


def check_must_not_contain(terms: list[str], output: str) -> CheckResult:
    found = [t for t in terms if t.lower() in output.lower()]
    return CheckResult("must_not_contain", not found, f"found {found}" if found else f"none of {len(terms)} present")


# --- length -------------------------------------------------------------------------------------


def check_max_chars(limit: int, output: str) -> CheckResult:
    return CheckResult("max_chars", len(output) <= limit, f"{len(output)} chars, limit {limit}")


def check_min_chars(limit: int, output: str) -> CheckResult:
    return CheckResult("min_chars", len(output) >= limit, f"{len(output)} chars, minimum {limit}")


def check_max_ratio(ratio: float, output: str, source: str) -> CheckResult:
    """Output length relative to the input — the natural bound for a summary."""
    actual = len(output) / max(len(source), 1)
    return CheckResult("max_ratio", actual <= ratio, f"output/input = {actual:.2f}, limit {ratio}")


# --- similarity ---------------------------------------------------------------------------------


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip().lower()


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


def check_similarity(spec: Mapping[str, Any], output: str) -> CheckResult:
    try:
        reference, threshold = str(spec["reference"]), float(spec["threshold"])
    except (KeyError, TypeError, ValueError) as err:
        raise CheckSpecError("similarity needs {reference, threshold}") from err
    score = similarity(output, reference)
    return CheckResult("similarity", score >= threshold, f"{score:.2f} vs threshold {threshold}")


# --- engine outcome -----------------------------------------------------------------------------


def check_same_language(expected: bool, result: ActionResult) -> CheckResult:
    return CheckResult(
        "same_language", result.same_language == expected, f"engine reported same_language={result.same_language}"
    )


# --- dispatch -----------------------------------------------------------------------------------

Check = Callable[[Any, str, str, ActionResult], CheckResult]

CHECKS: Mapping[str, Check] = {
    "language": lambda spec, out, src, res: check_language(str(spec), out),
    "must_contain": lambda spec, out, src, res: check_must_contain(_terms(spec), out),
    "must_not_contain": lambda spec, out, src, res: check_must_not_contain(_terms(spec), out),
    "max_chars": lambda spec, out, src, res: check_max_chars(int(spec), out),
    "min_chars": lambda spec, out, src, res: check_min_chars(int(spec), out),
    "max_ratio": lambda spec, out, src, res: check_max_ratio(float(spec), out, src),
    "similarity": lambda spec, out, src, res: check_similarity(spec, out),
    "same_language": lambda spec, out, src, res: check_same_language(bool(spec), res),
}


def _terms(spec: Any) -> list[str]:
    if isinstance(spec, str):
        return [spec]
    if isinstance(spec, list) and all(isinstance(t, str) for t in spec):
        return spec
    raise CheckSpecError("must_contain / must_not_contain take a string or a list of strings")


def run_checks(specs: Mapping[str, Any], output: str, source: str, result: ActionResult) -> list[CheckResult]:
    """Every declared check runs (no short-circuit) so a report shows all failures at once."""
    if not specs:
        raise CheckSpecError("a golden case must declare at least one check")
    results = []
    for name, spec in specs.items():
        try:
            check = CHECKS[name]
        except KeyError:
            raise CheckSpecError(f"unknown check {name!r}; known: {sorted(CHECKS)}") from None
        results.append(check(spec, output, source, result))
    return results
