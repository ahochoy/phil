import os
from collections import Counter
from pathlib import Path

import pytest

from phil.routing import CLASSES, DEPTHS
from tests.live.bench.classify.run import load_cases, run_and_record

EXPECTED_COUNTS = {
    "question": 6, "diagnosis": 4, "small_operation": 4, "simple_change": 6, "focused_fix": 4,
    "feature": 4, "refactor": 3, "design": 2, "broad_project": 1, "ambiguous": 6,
}


def test_cases_file_has_the_expected_counts_per_kind():
    cases = load_cases()
    assert len(cases) == 40
    counts = Counter("ambiguous" if case["ambiguous"] else case["expected_class"] for case in cases)
    assert counts == EXPECTED_COUNTS


def test_every_case_has_a_known_class_and_depth():
    cases = load_cases()
    for case in cases:
        assert case["expected_class"] in CLASSES, case["id"]
        assert case["expected_depth"] in DEPTHS, case["id"]


def test_case_ids_are_unique():
    cases = load_cases()
    ids = [case["id"] for case in cases]
    assert len(ids) == len(set(ids))


@pytest.mark.bench
@pytest.mark.parametrize("backend", ["llm", "jev"])
def test_classify_bench(backend):
    config = os.environ.get("PHIL_BENCH_CONFIG")
    if not config:
        pytest.skip("PHIL_BENCH_CONFIG is unset: point it at a phil.toml with the [models] to benchmark")
    summary = run_and_record(backend, load_cases(), Path(config).expanduser())
    if summary is None:
        pytest.skip(f"{backend}: not configured (no TYPESAFE_API_KEY for jev)")
    assert summary["n"] == 40 and summary["errors"] < 40, summary["errors_by_kind"]
