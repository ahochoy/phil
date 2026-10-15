"""Print the benchmark results: `python -m tests.live.bench.report [--last N]`."""

import argparse
import json
import os
import statistics
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
    "case", "sha", "mem", "tasks/modes", "depth exp/routed", "state", "passed", "min", "calls", "model calls",
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


def _mem(record: dict) -> str:
    """`cold#0` or `warm#2` for a record from a warm-mode pair; "-" for any other."""
    memory = record.get("memory")
    return f"{memory}#{record.get('pair', '?')}" if memory else "-"


def change(cold: float, warm: float) -> str:
    """Warm against cold as a whole percent (`-34%`), or `n/a` when the cold figure is 0."""
    if not cold:
        return "n/a"
    percent = round((warm - cold) / cold * 100)
    return f"{percent:+d}%" if percent else "0%"


def cold_against_warm(records: list[dict], pairs: int) -> list[tuple[str, ...]]:
    """The header, then one row per case that has warm-mode records: the medians of model calls,
    tokens (in plus out) and minutes over the last `pairs` cold and warm records, with the change."""
    header = (
        "case", "cold calls", "warm calls", "change", "cold tokens", "warm tokens", "change",
        "cold min", "warm min", "change",
    )
    measures = (
        (lambda r: r.get("model_calls", 0), "{:g}"),
        (lambda r: r.get("tokens_in", 0) + r.get("tokens_out", 0), "{:,.0f}"),
        (lambda r: r.get("minutes", 0.0), "{:.1f}"),
    )
    rows = [header]
    for name in sorted({r["case"] for r in records if r.get("memory")}):
        medians = {}
        for memory in ("cold", "warm"):
            picked = [r for r in records if r["case"] == name and r.get("memory") == memory][-pairs:]
            medians[memory] = [statistics.median(measure(r) for r in picked) if picked else 0 for measure, _ in measures]
        cells = [name]
        for index, (_, fmt) in enumerate(measures):
            cold, warm = medians["cold"][index], medians["warm"][index]
            cells += [fmt.format(cold), fmt.format(warm), change(cold, warm)]
        rows.append(tuple(cells))
    return rows


def _print_table(rows: list[tuple[str, ...]]) -> None:
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    for r in rows:
        print("  ".join(cell.ljust(width) for cell, width in zip(r, widths, strict=True)).rstrip())


def row(record: dict) -> tuple[str, ...]:
    modes = ",".join(record.get("modes") or []) or "-"
    depth = f"{record.get('expect_depth', '?')}/{record.get('routed_depth', '?')}"
    return (
        record["case"],
        str(record.get("phil_sha", "?")),
        _mem(record),
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
    parser.add_argument("--pairs", type=int, default=3, help="warm-mode pairs to take medians over (default 3)")
    args = parser.parse_args(argv)
    path = results_path()
    all_records = load(path)
    records = last_per_case(all_records, args.last)
    if not records:
        print(f"no benchmark results in {path}")
        return
    _print_table([COLUMNS, *(row(record) for record in records)])
    if any(record.get("memory") for record in all_records):
        print(f"\nCold against warm (medians over the last {args.pairs} pairs)")
        _print_table(cold_against_warm(all_records, args.pairs))


if __name__ == "__main__":
    main()
