"""FR-27: the checks behave as declared and the harness runs the shipped golden sets against the mock."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from contextai.actions import ActionEngine, load_templates
from contextai.providers import MockProvider, Timeout
from evals import run as harness
from evals.checks import (
    CheckSpecError,
    check_language,
    check_max_ratio,
    check_must_contain,
    check_must_not_contain,
    check_similarity,
    dominant_script,
    prose_only,
    run_checks,
)

# --- checks ---


@pytest.mark.parametrize(
    ("text", "script"),
    [
        ("Plain English sentence.", "latin"),
        ("这是中文句子。", "han"),
        ("これは日本語の文です。", "japanese"),
        ("한국어 문장입니다.", "hangul"),
        ("Русское предложение.", "cyrillic"),
        ("1234 !!!", None),
        ("Mostly English with 一个 word", "latin"),
    ],
)
def test_dominant_script(text, script):
    assert dominant_script(text) == script


def test_language_check_ignores_code_and_urls():
    text = "推送前运行 `uv run pytest`。文档：https://example.com/very/long/latin/path"
    assert dominant_script(text) == "latin"  # raw counting is fooled...
    assert dominant_script(prose_only(text)) == "han"  # ...the check is not
    assert check_language("zh", text).passed
    assert not check_language("en", text).passed


def test_language_check_rejects_unknown_codes():
    with pytest.raises(CheckSpecError):
        check_language("xx", "text")


def test_contain_checks_are_case_insensitive_and_list_offenders():
    assert check_must_contain(["Panel", "focus"], "the panel keeps FOCUS").passed
    result = check_must_contain(["panel", "missing"], "the panel")
    assert not result.passed and "missing" in result.detail
    result = check_must_not_contain(["Summary:"], "summary: here")
    assert not result.passed and "Summary:" in result.detail


def test_length_and_similarity_checks():
    assert check_max_ratio(0.5, "short", "a much longer source text").passed
    assert not check_max_ratio(0.1, "not short", "source").passed
    assert check_similarity(
        {"reference": "The panel appears beside the text", "threshold": 0.8}, "the  panel appears beside the text"
    ).passed
    assert not check_similarity({"reference": "completely different", "threshold": 0.8}, "nothing alike").passed
    with pytest.raises(CheckSpecError):
        check_similarity({"reference": "x"}, "y")


def test_run_checks_runs_every_check_and_rejects_unknown_names():
    engine = ActionEngine(MockProvider(), load_templates())
    result = engine.run("summarize", "source text")
    results = run_checks({"must_contain": ["nope"], "max_chars": 5}, "output text", "source text", result)
    assert [r.passed for r in results] == [False, False]  # no short-circuit
    with pytest.raises(CheckSpecError, match="unknown check 'exact'"):
        run_checks({"exact": "x"}, "o", "s", result)
    with pytest.raises(CheckSpecError):
        run_checks({}, "o", "s", result)


# --- harness ---


def test_shipped_golden_sets_pass_against_the_mock():
    reports = harness.run_all(ActionEngine(MockProvider(), load_templates()), harness.load_golden())
    assert {r.template for r in reports} == {"summarize", "translate"}
    for report in reports:
        assert report.all_passed, harness.format_report([report])
        assert report.version == load_templates()[report.template].version


def test_every_shipped_case_has_a_mock_output_and_a_check():
    for template_id, cases in harness.load_golden().items():
        assert cases, template_id
        for case in cases:
            assert case.mock_output is not None, f"{template_id}/{case.id}"
            assert case.checks, f"{template_id}/{case.id}"


def test_failing_case_and_provider_error_are_reported_not_raised(tmp_path: Path):
    golden = tmp_path / "summarize"
    golden.mkdir()
    (golden / "cases.yaml").write_text(
        "cases:\n"
        "  - {id: bad, input: 'The source', checks: {must_contain: [absent]}, mock_output: 'The output'}\n"
        "  - {id: err, input: 'The source', checks: {min_chars: 1}, mock_output: 'unused'}\n",
        encoding="utf-8",
    )
    mock = MockProvider()
    engine = ActionEngine(mock, load_templates())
    sets = harness.load_golden(tmp_path)
    report = harness.run_template(engine, engine.template("summarize"), sets["summarize"][:1])
    assert [c.passed for c in report.cases] == [False]
    assert report.cases[0].checks[0].detail == "missing ['absent']"

    class Failing:  # a non-mock provider, so the harness does not queue a mock_output
        name = "failing"
        model = "none"

        def complete(self, prompt):
            raise Timeout("slow")

    engine = ActionEngine(Failing(), load_templates())
    report = harness.run_template(engine, engine.template("summarize"), sets["summarize"][1:])
    assert report.cases[0].passed is False
    assert report.cases[0].error == "Timeout: slow"
    assert "FAIL  err" in harness.format_report([report])


def test_cli_exit_status_and_json_report(tmp_path: Path, capsys):
    out = tmp_path / "report.json"
    assert harness.main(["--template", "all", "--provider", "mock", "--json", str(out)]) == 0
    assert "total 7/7 passed" in capsys.readouterr().out
    data = json.loads(out.read_text(encoding="utf-8"))
    assert {d["template"] for d in data} == {"summarize", "translate"}

    golden = tmp_path / "translate"
    golden.mkdir()
    (golden / "c.yaml").write_text(
        "cases:\n  - {id: x, input: 'Hi', params: {target_language: Chinese}, checks: {language: zh}, mock_output: 'Hi'}\n",
        encoding="utf-8",
    )
    assert harness.main(["--template", "translate", "--golden-dir", str(tmp_path)]) == 1
    assert "FAIL  x" in capsys.readouterr().out


def test_mock_run_requires_mock_output(tmp_path: Path):
    golden = tmp_path / "summarize"
    golden.mkdir()
    (golden / "c.yaml").write_text("cases:\n  - {id: x, input: 'Hi', checks: {min_chars: 1}}\n", encoding="utf-8")
    with pytest.raises(harness.GoldenSetError, match="mock_output"):
        harness.run_all(ActionEngine(MockProvider(), load_templates()), harness.load_golden(tmp_path), "summarize")
