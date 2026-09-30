"""The benchmark harness, driven offline with scripted agents (runs in the default suite)."""

import json
import shlex
import shutil
import sys
import tomllib
from pathlib import Path

import pytest

from phil.agents.fake import ScriptedAgentFactory, Turn
from phil.chat.approval import launch_problems
from phil.config import load_config
from phil.contracts import Plan, Task
from tests.chat.conftest import critique
from tests.helpers import MODELS_TOML
from tests.live.bench import report
from tests.live.bench.cases import CASES, FIXTURES, Case
from tests.live.bench.harness import _init_repo, case_config, deep_merge, phil_sha, run_case
from tests.run.conftest import review, task_result, tester_report

TEST_CMD = f"{shlex.quote(sys.executable)} -m pytest -q -p no:cacheprovider"

RECORD_FIELDS = {
    "case", "ts", "phil_sha", "models", "tasks", "modes", "expect_modes", "state", "passed", "minutes",
    "calls", "tokens_in", "tokens_out", "cost_usd", "cost_source", "model_calls", "retries",
    "run_id", "chat_id", "error",
}


def case(name: str) -> Case:
    return next(c for c in CASES if c.name == name)


def multiply_plan() -> Plan:
    task = Task(
        id="CALC-001", description="Add multiply(a, b)", acceptance_criteria=["multiply(2, 3) == 6"],
        files_hint=["calc/__init__.py"],
    )
    return Plan(keyword="CALC", description="Add multiply", tasks=[task], test_cmd=TEST_CMD)


def write_red(turn: Turn):
    (turn.workdir / "tests" / "test_multiply.py").write_text(
        "from calc import multiply\n\n\ndef test_multiply():\n    assert multiply(2, 3) == 6\n"
    )
    return task_result("red", ["tests/test_multiply.py"], ["tests/test_multiply.py"])


def write_green(turn: Turn):
    module = turn.workdir / "calc" / "__init__.py"
    module.write_text(module.read_text() + "\n\ndef multiply(a, b):\n    return a * b\n")
    return task_result("green", ["calc/__init__.py"])


def scripted() -> ScriptedAgentFactory:
    return ScriptedAgentFactory({
        "architect": [multiply_plan()],
        "critic": [critique()],
        "implementer": [write_red, write_green],
        "tester": [tester_report()],
        "reviewer": [review()],
    })


@pytest.fixture
def results(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "results.jsonl"
    monkeypatch.setenv("PHIL_BENCH_RESULTS", str(path))
    return path


@pytest.fixture
def config(tmp_path: Path) -> Path:
    path = tmp_path / "bench.toml"
    # The plan's test command is the project's own, so the chat's start gate lets the run start.
    path.write_text(MODELS_TOML + f"\n[project]\ntest_cmd = {json.dumps(TEST_CMD)}\n")
    return path


def test_run_case_appends_one_complete_record(tmp_path, results, config):
    record = run_case(case("py-multiply"), config, tmp_path / "work", factory=scripted())
    lines = results.read_text().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == record
    assert set(record) >= RECORD_FIELDS
    assert record["case"] == "py-multiply"
    assert record["state"] == "completed"
    assert record["passed"] is True
    assert record["error"] is None
    assert (record["tasks"], record["modes"], record["expect_modes"]) == (1, ["tdd"], ["tdd"])
    assert record["models"]["architect"] == "test:model"
    # architect + critic in the chat, then red, green, tester and reviewer in the run
    assert record["calls"] == 6
    assert (record["tokens_in"], record["tokens_out"]) == (600, 120)
    assert (record["model_calls"], record["retries"]) == (0, 0)  # scripted agents make no model calls
    assert record["cost_source"] in ("reported", "estimated", "unknown")
    assert record["chat_id"].startswith("bench-py-multiply-")


def test_architect_reads_a_snapshot_of_the_base_commit(tmp_path, results, config):
    seen = []

    def architect(turn: Turn):
        seen.append(turn.workdir)
        return multiply_plan()

    factory = scripted()
    factory.scripts["architect"] = [architect]
    record = run_case(case("py-multiply"), config, tmp_path / "work", factory=factory)
    assert record["passed"] is True
    [tree] = seen
    assert tree != (tmp_path / "work" / "py-multiply").resolve()
    assert record["chat_id"] in str(tree)
    assert (tree / "calc" / "__init__.py").is_file()


def test_a_plan_the_chat_would_not_start_is_refused(tmp_path, results, config):
    no_test_cmd = multiply_plan().model_copy(update={"test_cmd": None})
    config.write_text(MODELS_TOML)  # no [project] test_cmd either
    factory = ScriptedAgentFactory({"architect": [no_test_cmd], "critic": [critique()]})
    record = run_case(case("py-multiply"), config, tmp_path / "work", factory=factory)
    assert (record["state"], record["passed"], record["run_id"]) == ("launch_refused", False, None)
    assert "no test command" in record["error"]
    assert record["calls"] == 2  # architect and critic only: no run started
    assert len(results.read_text().splitlines()) == 1


def test_run_case_writes_the_merged_config(tmp_path, results, config):
    run_case(case("py-multiply"), config, tmp_path / "work", factory=scripted())
    written = (tmp_path / "work" / "py-multiply" / "phil.toml").read_text()
    assert "max_tokens = 5000000" in written
    assert 'architect = "test:model"' in written


def test_a_run_that_does_not_finish_fails_the_case(tmp_path, results, config):
    factory = ScriptedAgentFactory({
        "architect": [multiply_plan()],
        "critic": [critique()],
        "implementer": [write_red, RuntimeError("model went away")],
    })
    record = run_case(case("py-multiply"), config, tmp_path / "work", factory=factory)
    assert record["state"] == "failed"
    assert record["passed"] is False
    assert "model went away" in record["error"]
    assert len(results.read_text().splitlines()) == 1


def test_deep_merge_keeps_the_baseline_under_the_config():
    base = {"run": {"max_tokens": 5, "max_cost_usd": 10.0}}
    merged = deep_merge(base, {"run": {"max_tokens": 7}, "models": {"critic": "x"}})
    assert merged == {"run": {"max_tokens": 7, "max_cost_usd": 10.0}, "models": {"critic": "x"}}
    assert base == {"run": {"max_tokens": 5, "max_cost_usd": 10.0}}


@pytest.mark.parametrize("name", ["site-meta-tag", "site-typo"])
def test_site_cases_configure_the_build_as_the_test_command(tmp_path, name):
    site_case = case(name)
    user = tomllib.loads(MODELS_TOML + '[run]\nmax_cost_usd = 3.0\n[project]\ntest_cmd = "npm test"\n')
    root = _init_repo(site_case, tmp_path / "work", case_config(site_case, user))
    config = load_config(root)
    assert config.project.test_cmd == "node build.mjs"  # the case's override wins
    assert (config.run.max_tokens, config.run.max_cost_usd) == (5_000_000, 3.0)
    # A plan without a test command (the site has no test script) passes the chat's start gate.
    assert launch_problems(multiply_plan().model_copy(update={"test_cmd": None}), config) == []


def test_phil_sha_is_unknown_outside_git(tmp_path):
    assert phil_sha(tmp_path) == "unknown"
    assert phil_sha(Path(__file__).parent) not in ("", "unknown")


def test_report_prints_the_last_records_per_case(tmp_path, results, config, capsys):
    run_case(case("py-multiply"), config, tmp_path / "work", factory=scripted())
    capsys.readouterr()
    report.main(["--last", "3"])
    out = capsys.readouterr().out
    assert "py-multiply" in out and "completed" in out and "tdd" in out


def test_report_says_when_there_are_no_results(results, capsys):
    report.main([])
    assert "no benchmark results" in capsys.readouterr().out


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_site_checks_fail_on_the_fixture_and_pass_when_fixed(tmp_path):
    site = tmp_path / "site"
    shutil.copytree(FIXTURES / "static-site", site)
    assert not case("site-typo").passed(site)
    assert not case("site-meta-tag").passed(site)
    index = site / "src" / "index.html"
    index.write_text(index.read_text().replace("Welcom ", "Welcome "))
    layout = site / "src" / "layout.html"
    layout.write_text(layout.read_text().replace("</head>", '<meta name="easter-egg" content="hello world"></head>'))
    assert case("site-typo").passed(site)
    assert case("site-meta-tag").passed(site)


def test_py_check_fails_until_multiply_exists(tmp_path):
    calc = tmp_path / "calc"
    shutil.copytree(FIXTURES / "py-calc", calc)
    assert not case("py-multiply").passed(calc)
    module = calc / "calc" / "__init__.py"
    module.write_text(module.read_text() + "\n\ndef multiply(a, b):\n    return a * b\n")
    assert case("py-multiply").passed(calc)
