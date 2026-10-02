"""Print the benchmark results: `python -m tests.live.bench.report [--last N]`."""

import argparse
import json
import os
from pathlib import Path

from phil.store.paths import phil_home


def results_path() -> Path:
    """Where records go: `$PHIL_BENCH_RESULTS`, else `bench/results.jsonl` under Phil's home (~/.phil)."""
    override = os.environ.get("PHIL_BENCH_RESULTS")
    return Path(override) if override else phil_home() / "bench" / "results.jsonl"


def load(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def last_per_case(records: list[dict], n: int) -> list[dict]:
    by_case: dict[str, list[dict]] = {}
    for record in records:
        by_case.setdefault(record["case"], []).append(record)
    return [record for case in sorted(by_case) for record in by_case[case][-n:]]


COLUMNS = (
    "case", "sha", "tasks/modes", "depth exp/routed", "state", "passed", "min", "calls", "model calls",
    "quick target", "tokens in/out", "cost",
)

# M1's targets for a quick case (spec §5.2): under 10 model calls and under 1 minute. Shown, not
# asserted, because live models vary.
QUICK_MAX_MODEL_CALLS = 10
QUICK_MAX_MINUTES = 1.0


def _cost(record: dict) -> str:
    cost = f"${record.get('cost_usd', 0.0):.2f}"
    source = record.get("cost_source")
    return f"~{cost}" if source == "estimated" else f"{cost}?" if source == "unknown" else cost


def _quick_target(record: dict) -> str:
    """Whether a quick case met M1's targets; "-" for a case the benchmark didn't expect to route quick."""
    if record.get("expect_depth") != "quick":
        return "-"
    under_calls = record.get("model_calls", 0) < QUICK_MAX_MODEL_CALLS
    under_minutes = record.get("minutes", 0.0) < QUICK_MAX_MINUTES
    return "ok" if under_calls and under_minutes else "MISS"


def row(record: dict) -> tuple[str, ...]:
    modes = ",".join(record.get("modes") or []) or "-"
    depth = f"{record.get('expect_depth', '?')}/{record.get('routed_depth', '?')}"
    return (
        record["case"],
        str(record.get("phil_sha", "?")),
        f"{record.get('tasks', 0)} {modes}",
        depth,
        str(record.get("state", "?")),
        "yes" if record.get("passed") else "no",
        f"{record.get('minutes', 0.0):.1f}",
        str(record.get("calls", 0)),
        str(record.get("model_calls", 0)),
        _quick_target(record),
        f"{record.get('tokens_in', 0):,}/{record.get('tokens_out', 0):,}",
        _cost(record),
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m tests.live.bench.report")
    parser.add_argument("--last", type=int, default=5, help="records to show per case (default 5)")
    args = parser.parse_args(argv)
    path = results_path()
    records = last_per_case(load(path), args.last)
    if not records:
        print(f"no benchmark results in {path}")
        return
    rows = [COLUMNS, *(row(record) for record in records)]
    widths = [max(len(r[i]) for r in rows) for i in range(len(COLUMNS))]
    for r in rows:
        print("  ".join(cell.ljust(width) for cell, width in zip(r, widths, strict=True)).rstrip())


if __name__ == "__main__":
    main()
