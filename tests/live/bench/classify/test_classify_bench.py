import os
from collections import Counter
from pathlib import Path

import pytest

from phil.routing import CLASSES, DEPTHS
from tests.live.bench.classify.run import load_cases, run_and_record

EXPECTED_TUNE_COUNTS = {
    "question": 6, "diagnosis": 4, "small_operation": 4, "simple_change": 7, "focused_fix": 4,
    "feature": 3, "refactor": 3, "design": 2, "broad_project": 1, "ambiguous": 6,
}


def test_cases_file_has_the_expected_counts_per_kind():
    cases = load_cases()
    tune = [case for case in cases if case.get("split", "tune") == "tune"]
    check = [case for case in cases if case.get("split") == "check"]
    assert len(tune) == 40 and len(check) == 10
    counts = Counter("ambiguous" if case["ambiguous"] else case["expected_class"] for case in tune)
    assert counts == EXPECTED_TUNE_COUNTS
    assert sum(case["ambiguous"] for case in check) == 7


def test_f01_is_relabelled_with_its_reason():
    [f01] = [case for case in load_cases() if case["id"] == "f-01"]
    assert (f01["expected_class"], f01["expected_depth"]) == ("simple_change", "quick")
    assert "py-multiply" in f01["note"]


def test_the_cta_and_pricing_requests_are_check_cases_that_need_detail():
    by_id = {case["id"]: case for case in load_cases()}
    for case_id in ("n-01", "n-02"):
        assert by_id[case_id]["split"] == "check" and by_id[case_id]["ambiguous"] is True
        assert by_id[case_id]["expected_depth"] == "full"


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
    cases = load_cases()
    summary = run_and_record(backend, cases, Path(config).expanduser())
    if summary is None:
        pytest.skip(f"{backend}: not configured (no TYPESAFE_API_KEY for jev)")
    assert summary["n"] == len(cases) and summary["errors"] < len(cases), summary["errors_by_kind"]
