"""tools.matrix_report, ported one-for-one from Swift `StatsTests` (migration step 4)."""

from __future__ import annotations

import json
from pathlib import Path

from tools.matrix_report import (
    Row,
    load_rows,
    main,
    percentile,
    render_markdown,
    render_table,
    summarize,
)


def test_nearest_rank_percentiles():
    values = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    assert percentile(values, 50) == 50
    assert percentile(values, 95) == 100
    assert percentile([7], 50) == 7 and percentile([7], 95) == 7
    assert percentile([], 50) is None
    assert percentile([3, 1, 2], 0) == 1


def rows():
    return [
        Row("com.apple.Safari", 1, True, 5),
        Row("com.apple.Safari", 1, True, 9),
        Row("com.apple.Safari", 3, True, 120),
        Row("com.apple.Safari", None, False, 400),
        Row("com.apple.Terminal", None, False, 2),
    ]


def test_per_app_roll_up():
    stats = summarize(rows())
    assert [s.app for s in stats] == ["com.apple.Safari", "com.apple.Terminal"]
    safari, terminal = stats
    assert (safari.attempts, safari.hits, safari.hit_rate) == (4, 3, 0.75)
    assert (safari.p50_ms, safari.p95_ms) == (9, 120)
    assert safari.tier_histogram == {1: 2, 3: 1, None: 1}
    assert terminal.hits == 0 and terminal.p50_ms is None and terminal.tier_histogram == {None: 1}


def test_empty_renders_no_captures():
    assert render_table([]) == "no captures"


def test_table_columns_match_the_spike():
    table = render_table(
        summarize([Row("com.apple.Notes", 2, True, 33.3), Row("com.apple.Terminal", None, False, 2.5)])
    )
    lines = table.split("\n")
    assert len(lines) == 4 and "hit-rate" in lines[0]
    assert "com.apple.Notes" in lines[2] and "100%" in lines[2] and "33.3" in lines[2] and "0/1/0/0" in lines[2]
    assert "com.apple.Terminal" in lines[3] and "0%" in lines[3] and "  -  " in lines[3] and "0/0/0/1" in lines[3]
    assert render_markdown(summarize([Row("a", 1, True, 1.0)])).startswith("| app |")


def test_reads_both_spike_lines_and_diagnostics_records(tmp_path: Path):
    spike = tmp_path / "matrix.jsonl"
    spike.write_text(
        json.dumps({"app": "com.apple.Notes", "tier": 1, "text": "x", "totalMs": 7.0}) + "\n", encoding="utf-8"
    )
    diag = tmp_path / "captures.jsonl"
    diag.write_text(
        json.dumps({"app": "com.apple.Notes", "tier": None, "hit": False, "totalMs": 300.0, "error": "exhausted"})
        + "\n\n",
        encoding="utf-8",
    )
    loaded = load_rows([spike, diag])
    assert [(r.hit, r.tier) for r in loaded] == [(True, 1), (False, None)]
    assert main([str(spike), str(diag)]) == 0
