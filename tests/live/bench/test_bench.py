"""The live benchmark (opt-in): `PHIL_BENCH_CONFIG=~/bench.toml uv run pytest -m bench`.

Each case plans and runs a small goal with real models and appends a record to
~/.phil/bench/results.jsonl (or $PHIL_BENCH_RESULTS); `python -m tests.live.bench.report` shows them.

For comparable `minutes`, run the cases one at a time: `... uv run pytest -m bench -n 0`. The default
`-n auto` runs them in parallel, and contention inflates each case's wall-clock time.

Warm mode compares a run with no repo memory against one that starts with what the cold run learned:
`PHIL_BENCH_WARM=1 PHIL_BENCH_PAIRS=3 PHIL_BENCH_CONFIG=~/bench.toml uv run pytest -m bench -n 0`
runs each case as that many cold-then-warm pairs (default 3), and the report shows the medians.
"""

import json
import os
import shutil
from pathlib import Path

import pytest

from tests.live.bench.cases import CASES, Case
from tests.live.bench.harness import run_case


@pytest.mark.bench
@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
def test_bench(case: Case, tmp_path: Path) -> None:
    config = os.environ.get("PHIL_BENCH_CONFIG")
    if not config:
        pytest.skip("PHIL_BENCH_CONFIG is unset: point it at a phil.toml with the [models] to benchmark")
    if case.fixture == "static-site" and shutil.which("node") is None:
        pytest.skip("node is not installed; the static-site cases build with `node build.mjs`")
    config_path = Path(config).expanduser()
    if os.environ.get("PHIL_BENCH_WARM") != "1":
        _check(case, run_case(case, config_path, tmp_path / "work"))
        return
    for pair in range(int(os.environ.get("PHIL_BENCH_PAIRS", "3"))):
        work = tmp_path / f"pair{pair}"
        _check(case, run_case(case, config_path, work, memory="cold", pair=pair))
        _check(case, run_case(case, config_path, work, memory="warm", pair=pair, reuse_repo=True))


def _check(case: Case, record: dict) -> None:
    print(json.dumps(record, indent=2))
    assert record["routed_depth"] == case.expect_depth, (
        f"{case.name}: routed {record['routed_depth']!r}, expected {case.expect_depth!r}"
    )
    assert record["passed"], f"{case.name}: {record['state']}: {record['error']}"
