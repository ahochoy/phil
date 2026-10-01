"""Offline tests for run.py's pure helpers: `decision_rule`, `_latest_per_case`, and
`append_record`/`results_path`. No network, no models."""

import json

from tests.live.bench.classify import run

# A summary with every condition comfortably passing, to flip one at a time below.
JEV_PASSING = {"depth_accuracy": 0.85, "answer_as_change": 0, "latency_p95_ms": 50, "cost_per_100": 1.0}
LLM_BASELINE = {"depth_accuracy": 0.85, "answer_as_change": 1, "latency_p95_ms": 300, "cost_per_100": 2.0}


def test_decision_rule_clear_yes():
    result = run.decision_rule(JEV_PASSING, LLM_BASELINE)
    assert result["conditions"] == {
        "depth_accuracy_within_2_points": True,
        "answer_as_change_no_more_than_llm": True,
        "p95_latency_at_most_a_third_of_llm": True,
        "cost_per_100_lower_than_llm": True,
    }
    assert result["verdict"] == "yes"


def test_decision_rule_no_when_depth_accuracy_is_more_than_2_points_lower():
    jev = {**JEV_PASSING, "depth_accuracy": 0.75}  # llm 0.85 - 0.10: more than 2 points lower
    result = run.decision_rule(jev, LLM_BASELINE)
    assert result["conditions"]["depth_accuracy_within_2_points"] is False
    assert result["conditions"]["answer_as_change_no_more_than_llm"] is True
    assert result["conditions"]["p95_latency_at_most_a_third_of_llm"] is True
    assert result["conditions"]["cost_per_100_lower_than_llm"] is True
    assert result["verdict"] == "no"


def test_decision_rule_no_when_jev_makes_more_answer_as_change_errors():
    jev = {**JEV_PASSING, "answer_as_change": 2}  # llm makes only 1
    result = run.decision_rule(jev, LLM_BASELINE)
    assert result["conditions"]["depth_accuracy_within_2_points"] is True
    assert result["conditions"]["answer_as_change_no_more_than_llm"] is False
    assert result["conditions"]["p95_latency_at_most_a_third_of_llm"] is True
    assert result["conditions"]["cost_per_100_lower_than_llm"] is True
    assert result["verdict"] == "no"


def test_decision_rule_no_when_jev_p95_is_not_at_least_3x_faster():
    jev = {**JEV_PASSING, "latency_p95_ms": 150}  # llm 300 / 3 == 100; 150 > 100
    result = run.decision_rule(jev, LLM_BASELINE)
    assert result["conditions"]["depth_accuracy_within_2_points"] is True
    assert result["conditions"]["answer_as_change_no_more_than_llm"] is True
    assert result["conditions"]["p95_latency_at_most_a_third_of_llm"] is False
    assert result["conditions"]["cost_per_100_lower_than_llm"] is True
    assert result["verdict"] == "no"


def test_decision_rule_no_when_jevs_cost_is_not_lower():
    jev = {**JEV_PASSING, "cost_per_100": 2.0}
    llm = {**LLM_BASELINE, "cost_per_100": 1.0}  # jev is now the more expensive one
    result = run.decision_rule(jev, llm)
    assert result["conditions"]["depth_accuracy_within_2_points"] is True
    assert result["conditions"]["answer_as_change_no_more_than_llm"] is True
    assert result["conditions"]["p95_latency_at_most_a_third_of_llm"] is True
    assert result["conditions"]["cost_per_100_lower_than_llm"] is False
    assert result["verdict"] == "no"


def test_decision_rule_undecided_when_jevs_cost_is_unknown():
    jev = {**JEV_PASSING, "cost_per_100": None}
    result = run.decision_rule(jev, LLM_BASELINE)
    assert result["conditions"]["cost_per_100_lower_than_llm"] == "unknown"
    assert result["verdict"] == "undecided (cost unknown)"


def test_decision_rule_undecided_when_llms_cost_is_unknown():
    llm = {**LLM_BASELINE, "cost_per_100": None}
    result = run.decision_rule(JEV_PASSING, llm)
    assert result["conditions"]["cost_per_100_lower_than_llm"] == "unknown"
    assert result["verdict"] == "undecided (cost unknown)"


def test_decision_rule_exactly_2_points_lower_passes():
    jev = {**JEV_PASSING, "depth_accuracy": 0.83}  # llm 0.85 - 0.02 == 0.83, exactly at the slack
    result = run.decision_rule(jev, LLM_BASELINE)
    assert result["conditions"]["depth_accuracy_within_2_points"] is True
    assert result["verdict"] == "yes"


def test_decision_rule_exactly_a_third_of_llm_p95_passes():
    jev = {**JEV_PASSING, "latency_p95_ms": 100}  # llm 300 / 3 == 100, exactly at the boundary
    result = run.decision_rule(jev, LLM_BASELINE)
    assert result["conditions"]["p95_latency_at_most_a_third_of_llm"] is True
    assert result["verdict"] == "yes"


def test_latest_per_case_keeps_the_newest_record_per_case_and_backend():
    records = [
        {"case_id": "q-01", "backend": "jev", "x": 1},
        {"case_id": "q-01", "backend": "llm", "x": 2},
        {"case_id": "q-01", "backend": "jev", "x": 3},  # a rerun: supersedes the first jev record
        {"case_id": "s-01", "backend": "llm", "x": 4},
    ]
    latest = run._latest_per_case(records)
    assert {(r["case_id"], r["backend"], r["x"]) for r in latest} == {
        ("q-01", "jev", 3), ("q-01", "llm", 2), ("s-01", "llm", 4),
    }


def test_append_record_writes_to_the_env_override(tmp_path, monkeypatch):
    target = tmp_path / "nested" / "classify.jsonl"
    monkeypatch.setenv("PHIL_BENCH_CLASSIFY_RESULTS", str(target))
    assert run.results_path() == target

    run.append_record({"case_id": "q-01", "backend": "llm"})
    run.append_record({"case_id": "d-01", "backend": "jev"})

    lines = target.read_text().splitlines()
    assert [json.loads(line) for line in lines] == [
        {"case_id": "q-01", "backend": "llm"},
        {"case_id": "d-01", "backend": "jev"},
    ]
