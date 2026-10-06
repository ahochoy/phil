"""Offline tests for run.py's helpers: `decision_rule`, `_latest_per_case`,
`append_record`/`results_path`, `_error_value`, and the llm backend's model choice (with
`judge_llm` replaced). No network, no models."""

import json
from pathlib import Path

import httpx
import pytest

from phil.config import ConfigError
from phil.key_store import KeyStoreError
from phil.routing.jev import JevError
from tests.live.bench.classify import run

JEV_FIXTURE = json.loads((Path(__file__).parents[3] / "routing" / "fixtures" / "jev_ok.json").read_text())

JEV_PASSING = {"wrong_path": 2, "unsafe": 0, "intake_count": 6, "latency_p95_ms": 50, "cost_per_100": 1.0}
LLM_BASELINE = {"wrong_path": 1, "unsafe": 1, "intake_count": 4, "latency_p95_ms": 300, "cost_per_100": 2.0}


def test_decision_rule_clear_yes():
    result = run.decision_rule(JEV_PASSING, LLM_BASELINE)
    assert result["conditions"] == {
        "wrong_path_within_one_of_llm": True,  # 2 <= 1 + 1
        "unsafe_no_more_than_llm": True,
        "deferrals_within_two_of_llm": True,  # 6 <= 4 + 2
        "p95_latency_at_most_a_third_of_llm": True,
        "cost_per_100_lower_than_llm": True,
    }
    assert result["verdict"] == "yes"


@pytest.mark.parametrize(
    ("change", "condition"),
    [
        ({"wrong_path": 3}, "wrong_path_within_one_of_llm"),
        ({"unsafe": 2}, "unsafe_no_more_than_llm"),
        ({"intake_count": 7}, "deferrals_within_two_of_llm"),
        ({"latency_p95_ms": 101}, "p95_latency_at_most_a_third_of_llm"),
        ({"cost_per_100": 2.0}, "cost_per_100_lower_than_llm"),
    ],
)
def test_decision_rule_each_condition_can_say_no(change, condition):
    result = run.decision_rule({**JEV_PASSING, **change}, LLM_BASELINE)
    assert result["conditions"][condition] is False
    assert [name for name, ok in result["conditions"].items() if ok is not True] == [condition]
    assert result["verdict"] == "no"


def test_decision_rule_unknown_cost_is_undecided_unless_another_condition_fails():
    assert run.decision_rule({**JEV_PASSING, "cost_per_100": None}, LLM_BASELINE)["verdict"] == "undecided (cost unknown)"
    failing = {**JEV_PASSING, "cost_per_100": None, "unsafe": 5}
    assert run.decision_rule(failing, LLM_BASELINE)["verdict"] == "no"


def test_relabel_takes_labels_and_split_from_the_cases_file():
    records = [
        {"case_id": "f-01", "backend": "jev", "expected_class": "feature", "expected_depth": "full", "ambiguous": False},
        {"case_id": "gone", "backend": "jev", "expected_class": "feature", "expected_depth": "full", "ambiguous": False},
    ]
    cases = [{"id": "f-01", "expected_class": "simple_change", "expected_depth": "quick", "ambiguous": False}]
    [relabelled] = run._relabel(records, cases)  # a record whose case is gone is dropped
    assert (relabelled["expected_class"], relabelled["expected_depth"], relabelled["split"]) == ("simple_change", "quick", "tune")


def _rec(case_id, backend, task_class, depth, needs_detail=0.1, cost=0.0, latency=10):
    return {
        "case_id": case_id, "backend": backend, "expected_class": task_class, "expected_depth": depth,
        "ambiguous": False, "task_class": task_class, "probabilities": {task_class: 0.9}, "confidence": 0.9,
        "needs_detail": needs_detail, "latency_ms": latency, "input_tokens": 1, "output_tokens": 0,
        "cost_usd": cost, "error": None,
    }


def test_report_chooses_on_tune_and_shows_check(tmp_path, monkeypatch, capsys):
    cases = [
        {"id": "q-01", "expected_class": "question", "expected_depth": "answer", "ambiguous": False},
        {"id": "n-08", "expected_class": "simple_change", "expected_depth": "quick", "ambiguous": False, "split": "check"},
    ]
    monkeypatch.setattr(run, "load_cases", lambda path=None: cases)
    records = [
        _rec("q-01", "jev", "question", "answer", latency=10), _rec("n-08", "jev", "simple_change", "quick", latency=10),
        _rec("q-01", "llm", "question", "answer", cost=0.001, latency=900),
        _rec("n-08", "llm", "simple_change", "quick", cost=0.001, latency=900),
    ]
    run._report(records)
    out = capsys.readouterr().out
    assert "chosen on tune: confidence 0.5, detail 0.6" in out
    assert "check (1 case" in out
    assert "jev worth recommending: yes" in out


def test_report_compares_only_shared_cases_and_names_the_rest(monkeypatch, capsys):
    cases = [
        {"id": "q-01", "expected_class": "question", "expected_depth": "answer", "ambiguous": False},
        {"id": "n-08", "expected_class": "simple_change", "expected_depth": "quick", "ambiguous": False},
    ]
    monkeypatch.setattr(run, "load_cases", lambda path=None: cases)
    records = [
        _rec("q-01", "jev", "question", "answer"), _rec("n-08", "jev", "simple_change", "quick"),
        _rec("q-01", "llm", "question", "answer", cost=0.001, latency=900),
    ]
    run._report(records)
    out = capsys.readouterr().out
    assert "compared on 1 shared case" in out
    assert "jev-only=['n-08']" in out


def test_report_says_verdict_is_provisional_with_no_check_records(monkeypatch, capsys):
    cases = [{"id": "q-01", "expected_class": "question", "expected_depth": "answer", "ambiguous": False}]
    monkeypatch.setattr(run, "load_cases", lambda path=None: cases)
    records = [
        _rec("q-01", "jev", "question", "answer"),
        _rec("q-01", "llm", "question", "answer", cost=0.001, latency=900),
    ]
    run._report(records)
    out = capsys.readouterr().out
    assert "verdict is provisional" in out


def test_report_prints_the_2d_sweep_grid(monkeypatch, capsys):
    cases = [{"id": "q-01", "expected_class": "question", "expected_depth": "answer", "ambiguous": False}]
    monkeypatch.setattr(run, "load_cases", lambda path=None: cases)
    records = [_rec("q-01", "jev", "question", "answer")]
    run._report(records)
    out = capsys.readouterr().out
    assert "conf 0.5:" in out


def test_report_says_when_a_backend_has_no_tune_results(monkeypatch, capsys):
    cases = [{"id": "n-08", "expected_class": "simple_change", "expected_depth": "quick", "ambiguous": False, "split": "check"}]
    monkeypatch.setattr(run, "load_cases", lambda path=None: cases)
    record = {
        "case_id": "n-08", "backend": "jev", "expected_class": "simple_change", "expected_depth": "quick",
        "ambiguous": False, "task_class": "simple_change", "probabilities": {}, "confidence": 0.9,
        "needs_detail": 0.1, "latency_ms": 10, "input_tokens": 1, "output_tokens": 0, "cost_usd": 0.0, "error": None,
    }
    run._report([record])
    assert "--- jev: no tune results ---" in capsys.readouterr().out


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


def _capture_llm_model(monkeypatch) -> list:
    from phil.routing.types import Judgement

    seen = []

    def fake_judge_llm(ctx, state, *, model=None, call=1):
        seen.append(model)
        return Judgement(task_class="question", probabilities={"question": 1.0}, confidence=0.9,
                         needs_detail=0.1, source="llm", latency_ms=1, usage=None)

    monkeypatch.setattr(run, "judge_llm", fake_judge_llm)
    monkeypatch.setattr(run, "phil_sha", lambda: "test")
    return seen


CASE = {"id": "q-01", "request": "what is calc?", "repo": {"test_cmd": None, "files": [], "file_count": 0},
        "expected_class": "question", "expected_depth": "answer", "ambiguous": False}


def test_the_llm_backend_uses_a_configured_llm_classifier(monkeypatch):
    from phil.config import PhilConfig

    seen = _capture_llm_model(monkeypatch)
    config = PhilConfig(models={"low": "openrouter:l", "classifier": "openrouter:c"})
    [record] = run.run_backend("llm", [CASE], config)
    assert seen == ["openrouter:c"] and record["model"] == "openrouter:c"


def test_the_llm_backend_uses_the_low_model_when_the_classifier_is_jev(monkeypatch):
    from phil.config import PhilConfig

    seen = _capture_llm_model(monkeypatch)
    config = PhilConfig(models={"low": "openrouter:l", "classifier": "typesafe:jev-latest"})
    [record] = run.run_backend("llm", [CASE], config)
    assert seen == ["openrouter:l"] and record["model"] == "openrouter:l"


def _jev_transport(monkeypatch, body: dict) -> None:
    """Route every `httpx.Client` `judge_jev` builds through a `MockTransport` that returns
    `body`, however it's called -- `_run_jev` passes no `transport` of its own."""
    real_client = httpx.Client

    def fake_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(lambda request: httpx.Response(200, json=body))
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", fake_client)


def test_the_jev_backend_records_usage_and_cost_from_the_response(monkeypatch):
    from phil.config import PhilConfig

    _jev_transport(monkeypatch, JEV_FIXTURE)
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-TESTSECRET0123456789abcdefABCDEF")
    monkeypatch.setattr(run, "phil_sha", lambda: "test")
    config = PhilConfig(models={"classifier": "typesafe:jev-latest"})

    [record] = run.run_backend("jev", [CASE], config)

    assert record["input_tokens"] == 412
    assert record["output_tokens"] == 3
    assert record["cost_usd"] == pytest.approx(412 * 0.042 / 1e6)


def test_the_jev_backend_leaves_cost_unknown_when_the_response_carries_no_usage(monkeypatch):
    from phil.config import PhilConfig

    body = {key: value for key, value in JEV_FIXTURE.items() if key != "usage"}
    _jev_transport(monkeypatch, body)
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-TESTSECRET0123456789abcdefABCDEF")
    monkeypatch.setattr(run, "phil_sha", lambda: "test")
    config = PhilConfig(models={"classifier": "typesafe:jev-latest"})

    [record] = run.run_backend("jev", [CASE], config)

    assert record["input_tokens"] == 0
    assert record["output_tokens"] == 0
    assert record["cost_usd"] is None


def test_error_value_keeps_a_configerror_message_since_it_never_carries_a_key_value():
    exc = ConfigError("openrouter needs OPENROUTER_API_KEY (used by classifier).")
    assert run._error_value(exc) == "ConfigError: openrouter needs OPENROUTER_API_KEY (used by classifier)."


def test_error_value_keeps_a_keystoreerror_message_for_the_same_reason():
    exc = KeyStoreError("No keychain is available here; export OPENROUTER_API_KEY instead.")
    assert run._error_value(exc) == "KeyStoreError: No keychain is available here; export OPENROUTER_API_KEY instead."


def test_error_value_only_keeps_the_first_line_of_a_multiline_configerror():
    exc = ConfigError("Invalid phil.toml: line one\nline two")
    assert run._error_value(exc) == "ConfigError: Invalid phil.toml: line one"


def test_error_value_keeps_jeverrors_reason():
    exc = JevError("http 429")
    assert run._error_value(exc) == "JevError: http 429"


def test_error_value_drops_a_generic_exceptions_text_which_could_carry_anything():
    exc = RuntimeError("secret sk-proj-abcdefg")
    assert run._error_value(exc) == "RuntimeError"
