"""Run Action Templates against their Golden Sets and report pass/fail per case (PRD FR-27).

    uv run python -m evals.run --template all --provider mock
    uv run python -m evals.run --template translate --provider openai --model gpt-4o-mini

`--provider mock` needs no key or network: each golden case supplies a `mock_output`, so a mock run
exercises the loader, the engine, the checks and the report — not the prompt. Only a real provider
run says anything about prompt quality. Exit status is 1 when any case fails; not a CI gate.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

if __package__ in (None, ""):  # `uv run evals/run.py` puts evals/ on sys.path; make the project root importable
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from contextai.actions import (  # noqa: E402
    ActionEngine,
    ActionResult,
    Template,
    load_templates,
)
from contextai.providers import (  # noqa: E402
    MockProvider,
    OpenAIProvider,
    Provider,
    ProviderError,
)
from evals.checks import CheckResult, run_checks  # noqa: E402

GOLDEN_DIR = Path(__file__).with_name("golden")


class GoldenSetError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Case:
    id: str
    input: str
    checks: Mapping[str, Any]
    params: Mapping[str, str] = field(default_factory=dict)
    mock_output: str | None = None
    source: str = ""


@dataclass(slots=True)
class CaseReport:
    case: str
    passed: bool
    checks: list[CheckResult]
    output: str = ""
    error: str | None = None
    elapsed_ms: float = 0.0


@dataclass(slots=True)
class TemplateReport:
    template: str
    version: int
    provider: str
    model: str
    cases: list[CaseReport]

    @property
    def passed(self) -> int:
        return sum(1 for c in self.cases if c.passed)

    @property
    def all_passed(self) -> bool:
        return all(c.passed for c in self.cases)


def load_golden(directory: Path = GOLDEN_DIR) -> dict[str, list[Case]]:
    """`golden/<template-id>/*.yaml`, each file holding `cases: [...]`. Ids must be unique per template."""
    sets: dict[str, list[Case]] = {}
    for template_dir in sorted(p for p in directory.iterdir() if p.is_dir()):
        cases: list[Case] = []
        for path in sorted(template_dir.glob("*.yaml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            raw_cases = data.get("cases")
            if not isinstance(raw_cases, list):
                raise GoldenSetError(f"{path}: expected a top-level 'cases' list")
            for raw in raw_cases:
                cases.append(_case_from_mapping(raw, path))
        ids = [c.id for c in cases]
        if len(ids) != len(set(ids)):
            raise GoldenSetError(f"{template_dir}: duplicate case ids")
        sets[template_dir.name] = cases
    return sets


def _case_from_mapping(raw: Any, path: Path) -> Case:
    if not isinstance(raw, Mapping):
        raise GoldenSetError(f"{path}: each case must be a mapping")
    try:
        case_id, text, checks = str(raw["id"]), str(raw["input"]), raw["checks"]
    except KeyError as err:
        raise GoldenSetError(f"{path}: case is missing {err}") from None
    if not isinstance(checks, Mapping) or not checks:
        raise GoldenSetError(f"{path}: case {case_id!r} must declare a non-empty 'checks' mapping")
    params = raw.get("params") or {}
    if not isinstance(params, Mapping):
        raise GoldenSetError(f"{path}: case {case_id!r}: 'params' must be a mapping")
    mock_output = raw.get("mock_output")
    return Case(
        id=case_id,
        input=text,
        checks=dict(checks),
        params={str(k): str(v) for k, v in params.items()},
        mock_output=None if mock_output is None else str(mock_output),
        source=str(path),
    )


def run_template(engine: ActionEngine, template: Template, cases: Sequence[Case]) -> TemplateReport:
    provider = engine.provider
    mock = provider if isinstance(provider, MockProvider) else None
    reports: list[CaseReport] = []
    for case in cases:
        if mock is not None:
            if case.mock_output is None:
                raise GoldenSetError(f"{case.source}: case {case.id!r} has no 'mock_output' for a mock run")
            mock.scripted.append(case.mock_output)
        try:
            result: ActionResult = engine.run(template.id, case.input, case.params)
        except ProviderError as err:
            reports.append(CaseReport(case.id, False, [], error=f"{type(err).__name__}: {err.message}"))
            continue
        checks = run_checks(case.checks, result.text, case.input, result)
        reports.append(
            CaseReport(case.id, all(c.passed for c in checks), checks, output=result.text, elapsed_ms=result.elapsed_ms)
        )
    model = getattr(provider, "model", "")
    return TemplateReport(template.id, template.version, provider.name, str(model), reports)


def run_all(engine: ActionEngine, golden: Mapping[str, Sequence[Case]], only: str = "all") -> list[TemplateReport]:
    wanted = sorted(engine.templates) if only == "all" else [only]
    reports = []
    for template_id in wanted:
        template = engine.template(template_id)
        cases = golden.get(template_id)
        if not cases:
            raise GoldenSetError(f"no golden set for template {template_id!r} under {GOLDEN_DIR}")
        reports.append(run_template(engine, template, cases))
    return reports


def format_report(reports: Sequence[TemplateReport]) -> str:
    lines = []
    for report in reports:
        lines.append(
            f"{report.template}@v{report.version}  provider={report.provider} model={report.model}  "
            f"{report.passed}/{len(report.cases)} passed"
        )
        for case in report.cases:
            mark = "PASS" if case.passed else "FAIL"
            lines.append(f"  {mark}  {case.case}  ({case.elapsed_ms:.0f} ms)")
            if case.error:
                lines.append(f"        error: {case.error}")
            for check in case.checks:
                if not check.passed:
                    lines.append(f"        {check.name}: {check.detail}")
    total = sum(len(r.cases) for r in reports)
    passed = sum(r.passed for r in reports)
    lines.append(f"total {passed}/{total} passed")
    return "\n".join(lines)


def build_provider(name: str, model: str | None) -> Provider:
    if name == "mock":
        return MockProvider()
    if name == "openai":
        return OpenAIProvider(model=model) if model else OpenAIProvider()
    raise SystemExit(f"unknown provider {name!r}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--template", default="all", help="template id or 'all'")
    parser.add_argument("--provider", choices=["mock", "openai"], default="mock")
    parser.add_argument("--model", help="provider model identifier (openai only)")
    parser.add_argument("--templates-dir", type=Path, help="override the built-in templates directory")
    parser.add_argument("--golden-dir", type=Path, default=GOLDEN_DIR)
    parser.add_argument("--json", type=Path, help="also write the full report as JSON to this path")
    args = parser.parse_args(argv)

    templates = load_templates(args.templates_dir) if args.templates_dir else load_templates()
    engine = ActionEngine(build_provider(args.provider, args.model), templates)
    reports = run_all(engine, load_golden(args.golden_dir), args.template)

    print(format_report(reports))
    if args.json:
        args.json.write_text(json.dumps([asdict(r) for r in reports], ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if all(r.all_passed for r in reports) else 1


if __name__ == "__main__":
    sys.exit(main())
