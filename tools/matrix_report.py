"""Per-app roll-up of capture records (PRD FR-31): hit-rate, p50/p95 and tier histogram from JSONL.

    uv run python -m tools.matrix_report captures.jsonl [more.jsonl ...] [--markdown]

Ported from Swift `Stats` in migration step 4. Reads both the spike's `--out` lines (which carry
`text`) and the app's diagnostics records (which carry `hit` instead) — the support-tier table in the
README is reproducible from either. Percentiles are nearest-rank over hits only, as in the spike.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


@dataclass(frozen=True, slots=True)
class Row:
    app: str
    tier: int | None
    hit: bool
    total_ms: float

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "Row":
        hit = bool(record["hit"]) if "hit" in record else record.get("text") is not None
        tier = record.get("tier")
        return cls(
            app=str(record.get("app", "?")),
            tier=int(tier) if tier is not None else None,
            hit=hit,
            total_ms=float(record.get("totalMs", 0.0)),
        )


@dataclass
class AppStats:
    app: str
    attempts: int = 0
    hits: int = 0
    p50_ms: float | None = None
    p95_ms: float | None = None
    tier_histogram: dict[int | None, int] = field(default_factory=dict)

    @property
    def hit_rate(self) -> float:
        return self.hits / self.attempts if self.attempts else 0.0


def percentile(values: Sequence[float], p: float) -> float | None:
    """Nearest-rank: the smallest value such that at least p% of samples are ≤ it."""
    if not values:
        return None
    ordered = sorted(values)
    rank = math.ceil(p / 100 * len(ordered))
    return ordered[min(max(rank - 1, 0), len(ordered) - 1)]


def summarize(rows: Iterable[Row]) -> list[AppStats]:
    by_app: dict[str, list[Row]] = {}
    for row in rows:
        by_app.setdefault(row.app, []).append(row)
    stats = []
    for app in sorted(by_app):
        group = by_app[app]
        hit_latencies = [r.total_ms for r in group if r.hit]
        histogram: dict[int | None, int] = {}
        for r in group:
            histogram[r.tier] = histogram.get(r.tier, 0) + 1
        stats.append(
            AppStats(
                app,
                len(group),
                len(hit_latencies),
                percentile(hit_latencies, 50),
                percentile(hit_latencies, 95),
                histogram,
            )
        )
    return stats


HEADER = ["app", "attempts", "hits", "hit-rate", "p50 ms", "p95 ms", "tiers (1/2/3/fail)"]


def _cells(s: AppStats) -> list[str]:
    fmt = lambda v: f"{v:.1f}" if v is not None else "-"  # noqa: E731
    tiers = "/".join(str(s.tier_histogram.get(t, 0)) for t in (1, 2, 3)) + "/" + str(s.tier_histogram.get(None, 0))
    return [s.app, str(s.attempts), str(s.hits), f"{s.hit_rate * 100:.0f}%", fmt(s.p50_ms), fmt(s.p95_ms), tiers]


def render_table(stats: Sequence[AppStats]) -> str:
    """The spike's fixed-width terminal table; 'no captures' when empty."""
    if not stats:
        return "no captures"
    rows = [HEADER] + [_cells(s) for s in stats]
    widths = [max(len(r[i]) for r in rows) for i in range(len(HEADER))]
    line = lambda cells: "  ".join(c.ljust(w) for c, w in zip(cells, widths))  # noqa: E731
    return "\n".join([line(HEADER), "  ".join("-" * w for w in widths), *(line(r) for r in rows[1:])])


def render_markdown(stats: Sequence[AppStats]) -> str:
    if not stats:
        return "_no captures_"
    head = "| " + " | ".join(HEADER) + " |\n|" + "|".join(" --- " for _ in HEADER) + "|"
    return head + "\n" + "\n".join("| " + " | ".join(_cells(s)) + " |" for s in stats)


def load_rows(paths: Iterable[Path]) -> list[Row]:
    rows = []
    for path in paths:
        for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                rows.append(Row.from_record(json.loads(line)))
            except (ValueError, KeyError, TypeError) as err:
                raise SystemExit(f"{path}:{number}: not a capture record: {err}") from err
    return rows


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--markdown", action="store_true", help="emit a Markdown table (for the README)")
    args = parser.parse_args(argv)
    stats = summarize(load_rows(args.files))
    print(render_markdown(stats) if args.markdown else render_table(stats))
    return 0


if __name__ == "__main__":
    sys.exit(main())
