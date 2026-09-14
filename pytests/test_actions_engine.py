"""FR-17/18/20/23: the engine renders templates, enforces the cap before any request, needs an explicit
target language, interprets the same-language sentinel, and passes typed errors through untouched."""

from __future__ import annotations

import pytest

from contextai.actions import (
    ActionEngine,
    EmptySelection,
    MissingParameter,
    UnknownAction,
    load_templates,
)
from contextai.providers import MockProvider, NoNetwork, Prompt, SelectionTooLong


@pytest.fixture
def mock() -> MockProvider:
    return MockProvider()


@pytest.fixture
def engine(mock: MockProvider) -> ActionEngine:
    return ActionEngine(mock, load_templates(), selection_cap=100)


def test_summarize_renders_selection_into_the_user_turn(engine: ActionEngine, mock: MockProvider):
    result = engine.run("summarize", "Some selected text.")
    assert result.label == "summarize@v1"
    assert result.text == "[mock] Some selected text."
    assert result.same_language is False
    assert (result.provider, result.model) == ("mock", "mock-1")
    assert result.elapsed_ms >= 0
    prompt = mock.calls[0]
    assert isinstance(prompt, Prompt)
    assert prompt.user == "Some selected text."
    assert "same language as the source" in prompt.system
    assert prompt.max_output_tokens == 400


def test_translate_renders_target_language_into_the_system_turn(engine: ActionEngine, mock: MockProvider):
    engine.run("translate", "Bonjour", {"target_language": "English"})
    assert "into English" in mock.calls[0].system
    assert mock.calls[0].user == "Bonjour"


def test_translate_without_target_language_is_refused_not_defaulted(engine: ActionEngine, mock: MockProvider):
    """Open Question 1: the app must never silently assume a target language."""
    for params in (None, {}, {"target_language": ""}, {"target_language": "   "}):
        with pytest.raises(MissingParameter) as info:
            engine.run("translate", "Bonjour", params)
        assert info.value.names == ["target_language"]
    assert mock.calls == []


def test_same_language_sentinel_is_interpreted_not_shown(engine: ActionEngine, mock: MockProvider):
    mock.scripted.append("  <<SAME_LANGUAGE>>\n")
    result = engine.run("translate", "Already English.", {"target_language": "English"})
    assert result.same_language is True
    assert result.text == ""
    assert result.completion.text.strip() == "<<SAME_LANGUAGE>>"


def test_sentinel_only_applies_to_templates_that_declare_it(engine: ActionEngine, mock: MockProvider):
    mock.scripted.append("<<SAME_LANGUAGE>>")
    result = engine.run("summarize", "text")
    assert result.same_language is False and result.text == "<<SAME_LANGUAGE>>"


def test_selection_over_cap_is_refused_before_any_request(engine: ActionEngine, mock: MockProvider):
    with pytest.raises(SelectionTooLong) as info:
        engine.run("summarize", "x" * 101)
    assert (info.value.length, info.value.cap) == (101, 100)
    assert info.value.retryable is False
    assert mock.calls == []
    engine.run("summarize", "x" * 100)  # exactly at the cap is allowed
    assert len(mock.calls) == 1


def test_template_cap_can_only_tighten_the_engine_cap(mock: MockProvider, tmp_path):
    (tmp_path / "tight.yaml").write_text(
        'id: tight\nversion: 1\nname: T\nsystem: s\nuser: "{selection}"\nmax_selection_chars: 10\n', encoding="utf-8"
    )
    (tmp_path / "loose.yaml").write_text(
        'id: loose\nversion: 1\nname: L\nsystem: s\nuser: "{selection}"\nmax_selection_chars: 1000\n', encoding="utf-8"
    )
    engine = ActionEngine(mock, load_templates(tmp_path), selection_cap=100)
    assert engine.cap_for(engine.template("tight")) == 10
    assert engine.cap_for(engine.template("loose")) == 100


def test_empty_and_unknown_are_caller_errors(engine: ActionEngine, mock: MockProvider):
    with pytest.raises(EmptySelection):
        engine.run("summarize", "   \n")
    with pytest.raises(UnknownAction, match="'explain'"):
        engine.run("explain", "text")
    assert mock.calls == []


def test_provider_errors_pass_through_and_rerun_is_the_retry(engine: ActionEngine, mock: MockProvider):
    mock.fail_once(NoNetwork("offline"))
    with pytest.raises(NoNetwork) as info:
        engine.run("summarize", "same text")
    assert info.value.retryable
    result = engine.run("summarize", "same text")  # FR-24: retry re-runs on the same captured text
    assert result.text == "[mock] same text"
    assert [p.user for p in mock.calls] == ["same text", "same text"]


def test_one_request_per_action(engine: ActionEngine, mock: MockProvider):
    engine.run("summarize", "a")
    engine.run("translate", "b", {"target_language": "German"})
    assert len(mock.calls) == 2
