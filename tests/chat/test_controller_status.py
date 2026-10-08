"""The chat's status bar: repo and branch, the model at work, the run's usage and its budget."""

import time

import pytest

from phil.chat.controller import WAKE
from phil.chat.events import ChatEvent
from phil.config import PhilConfig
from phil.git import GitNotFound, current_branch
from phil.store.events import run_events
from phil.store.paths import ProjectPaths
from phil.store.runs import get_run
from phil.ui.toolbar import render_toolbar, toolbar_text
from tests.chat.conftest import critique, goal, plan
from tests.chat.test_controller import run_chat
from tests.chat.test_controller_run import set_state
from tests.helpers import run_git

SCRIPTS = {"intake": [goal()], "architect": [plan()], "critic": [critique()]}
HIGH, LOW = "anthropic:claude-sonnet-5", "openrouter:google/gemini-3.8-flash"


@pytest.fixture
def controller(calc_repo):
    """A chat whose goal was approved and whose run it follows; what it printed so far is cleared."""
    box = {}

    def grab(controller):
        box["controller"] = controller
        return None  # EOF: the chat closes, still following its run

    run_chat(calc_repo, ["add subtract", "y", grab], SCRIPTS)
    controller = box["controller"]
    assert controller._run_id is not None
    controller._follow()  # closing the chat stopped its watcher: follow the run again
    controller.console.export_text()  # clear
    return controller


@pytest.fixture
def controller_with_repo(calc_repo):
    """The `controller` above, plus its repo and the toolbar views seen at chat start and while following."""
    seen = {}

    def at_start(controller):
        seen["start"] = controller.state.view()
        return "add subtract"

    def following(controller):
        seen["controller"] = controller
        seen["following"] = controller.state.view()
        seen["record"] = get_run(controller.conn, controller._run_id)
        return None

    run_chat(calc_repo, [at_start, "y", following], SCRIPTS)
    return seen["controller"], calc_repo, seen


@pytest.fixture
def controller_following_a_scripted_run(calc_repo):
    """A chat with high and low models whose run has finished a task, with the implementer at work."""
    models = {"high": "ollama:big-model", "low": "ollama:acme/small-model"}
    # The chat reloads the repo's config when it plans, so the models go in its phil.toml too.
    toml = "[models]\n" + "".join(f'{tier} = "{model}"\n' for tier, model in models.items())
    (calc_repo / "phil.toml").write_text(toml, encoding="utf-8", newline="\n")
    run_git(calc_repo, "commit", "-am", "high and low models")
    box = {}

    def progress(controller):
        set_state(controller, "running", tasks_done=1, current_node="implement")
        controller.post(ChatEvent("live_step", {
            "task": "CALC-001", "role": "implementer", "summary": "edit calc.py", "started": time.time(),
        }))
        controller._watcher.poll_once()
        return WAKE

    def grab(controller):
        box["controller"], box["view"] = controller, controller.state.view()
        return None

    run_chat(calc_repo, ["add subtract", "y", progress, grab], SCRIPTS, config=PhilConfig(models=models))
    return box["controller"], box["view"]


def test_current_branch(tmp_path, git_repo, monkeypatch):
    """In a repo on branch main: "main". With HEAD detached: a 7+ character hex SHA. In a plain
    non-repo directory: "?". current_branch never raises."""
    assert current_branch(git_repo) == "main"
    run_git(git_repo, "checkout", "--detach")
    sha = current_branch(git_repo)
    assert len(sha) >= 7 and all(c in "0123456789abcdef" for c in sha)
    assert run_git(git_repo, "rev-parse", "HEAD").startswith(sha)
    plain = tmp_path / "plain"
    plain.mkdir()
    assert current_branch(plain) == "?"
    assert current_branch(tmp_path / "missing") == "?"

    def no_git(cwd, *args, **kwargs):  # current_branch passes timeout=
        raise GitNotFound(list(args), 127, "git not found")

    monkeypatch.setattr("phil.git.git", no_git)
    assert current_branch(git_repo) == "?"


def test_place_is_set_at_start_and_after_a_run(controller_with_repo):
    """At chat start, state.view().repo == the repo folder name and .branch == its branch. When a run starts
    following, .branch == the run's record.branch. After run_done, .branch is re-read from the repo."""
    controller, repo, seen = controller_with_repo
    assert seen["start"].repo == repo.name
    assert seen["start"].branch == "main"
    assert seen["following"].repo == repo.name
    assert seen["following"].branch == seen["record"].branch
    assert seen["record"].branch != "main"
    run_git(repo, "checkout", "-b", "feature")  # the repo's branch moved while the run worked
    controller._handle(ChatEvent("run_done", {"state": "completed", "tasks_done": 1, "tasks_total": 1}))
    assert controller.state.view().repo == repo.name
    assert controller.state.view().branch == "feature"


def test_model_follows_the_working_role(controller):
    """With config models high="anthropic:claude-sonnet-5", low="openrouter:google/gemini-3.8-flash":
    - a step "architect" sets model == ("high", "claude-sonnet-5");
    - a live_step with role "implementer" sets ("low", "gemini-3.8-flash");
    - clearing the step with no run sets None."""
    controller.config = PhilConfig(models={"high": HIGH, "low": LOW})
    generation = controller._generation
    controller._handle(ChatEvent("live_step", {"task": "CALC-001", "role": "implementer", "summary": "edit"}))
    assert controller.state.view().model == ("low", "gemini-3.8-flash")
    controller._step("architect", generation)  # while the run is followed, its model stays
    assert controller.state.view().model == ("low", "gemini-3.8-flash")
    controller._step(None, generation)
    assert controller.state.view().model == ("low", "gemini-3.8-flash")
    controller._forget_run()
    controller._step("architect", generation)
    assert controller.state.view().model == ("high", "claude-sonnet-5")
    controller._step(None, generation)
    assert controller.state.view().model is None


RUN_MODELS = {"high": HIGH, "low": LOW, "tester": "ollama:tester-model", "reviewer": "ollama:reviewer-model"}


def progress(node: str) -> ChatEvent:
    return ChatEvent("run_progress", {
        "node": node, "state": "running", "tasks_done": 0, "tasks_total": 1, "keyword": "CALC",
        "started": time.time(), "tokens": 0, "cost_usd": 0.0, "cost_source": "reported",
    })


def live(role: str) -> ChatEvent:
    return ChatEvent("live_step", {"task": "CALC-001", "role": role, "summary": "edit", "started": time.time()})


def test_run_model_comes_from_its_node(controller):
    """No role known yet: None. At node implement with no live step: the implementer's model; a live step
    with role tester shows the tester's; an engine live step is ignored; at commit the last role is kept;
    after run_done the model is None."""
    controller.config = PhilConfig(models=RUN_MODELS)
    implementer, tester = controller._model_for("implementer"), controller._model_for("tester")
    assert implementer == ("low", "gemini-3.8-flash") and tester[1] == "tester-model"
    controller._handle(progress("setup"))
    assert controller.state.view().model is None  # no role seen yet for this run
    controller._handle(progress("implement"))
    assert controller.state.view().model == implementer
    controller._handle(live("tester"))
    assert controller.state.view().model == tester
    controller._handle(ChatEvent("live_step", {}))
    assert controller.state.view().model == implementer
    controller._handle(live("engine"))
    assert controller.state.view().model == implementer
    controller._handle(ChatEvent("live_step", {}))
    controller._handle(progress("review"))
    assert controller.state.view().model == controller._model_for("reviewer")
    controller._handle(progress("commit"))
    assert controller.state.view().model == controller._model_for("reviewer")  # the last role is kept
    controller._handle(ChatEvent("run_done", {"state": "completed", "tasks_done": 1, "tasks_total": 1}))
    assert controller.state.view().model is None


def test_a_goal_step_does_not_hide_the_runs_model(controller):
    """A goal step (the architect's, say) that overlaps the run leaves the run's model shown."""
    controller.config = PhilConfig(models=RUN_MODELS)
    controller._handle(progress("implement"))
    controller._step("architect", controller._generation)
    assert controller.state.view().model == controller._model_for("implementer")
    controller._step(None, controller._generation)
    assert controller.state.view().model == controller._model_for("implementer")


def test_routing_and_answering_steps_show_their_models(controller):
    """With no run: routing shows the classifier's model and answering the answerer's."""
    controller._forget_run()
    controller.config = PhilConfig(models={"high": HIGH, "low": LOW, "classifier": "ollama:tiny"})
    controller._step("routing", controller._generation)
    assert controller.state.view().model == controller._model_for("classifier") == ("classifier", "tiny")
    controller._step("answering", controller._generation)
    assert controller.state.view().model == controller._model_for("answerer") == ("low", "gemini-3.8-flash")


def test_current_branch_times_out(git_repo, monkeypatch):
    """current_branch gives git a 5 second timeout, and a git that times out gives "?"."""
    import subprocess

    seen = []

    def hangs(cmd, **kwargs):
        seen.append(kwargs.get("timeout"))
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))

    monkeypatch.setattr("phil.git.subprocess.run", hangs)
    assert current_branch(git_repo) == "?"
    assert seen == [5]


def test_branch_is_unknown_when_the_run_cant_be_read(controller, monkeypatch):
    """When get_run raises, the branch shows "?" and the repo name stays."""
    def broken(conn, run_id):
        raise RuntimeError("database is locked")

    monkeypatch.setattr("phil.chat.controller.get_run", broken)
    controller._refresh_place()
    assert controller.state.view().branch == "?"
    assert controller.state.view().repo == controller.info.root.name


def test_a_chat_outside_a_repo_starts_with_an_unknown_branch(calc_repo, tmp_path, monkeypatch):
    """A chat whose root isn't a git repo starts normally (it asks for a goal) and shows the branch as "?"."""
    import dataclasses

    import tests.chat.test_controller as harness

    plain = tmp_path / "plain"
    plain.mkdir()
    real = harness.resolve_repo
    monkeypatch.setattr(harness, "resolve_repo", lambda repo: dataclasses.replace(real(repo), root=plain))
    monkeypatch.setattr("phil.chat.controller.repo_overview", lambda root: "")
    seen = {}

    def at_start(controller):
        seen["view"] = controller.state.view()
        return None

    *_, prompts = run_chat(calc_repo, [at_start], SCRIPTS)
    assert prompts  # the chat asked for its first goal
    assert seen["view"].branch == "?"
    assert seen["view"].repo == "plain"


def test_model_for_intake_and_unknown_roles(controller):
    """Intake uses the classifier's model when one is configured, else the orchestrator's; a role
    without a model gives None."""
    controller.config = PhilConfig(models={"high": HIGH, "low": LOW})
    assert controller._model_for(None) == ("low", "gemini-3.8-flash")  # the classifier falls back to low
    controller.config = PhilConfig(models={"high": HIGH, "low": LOW, "classifier": "ollama:tiny"})
    assert controller._model_for(None) == ("classifier", "tiny")
    controller.config = PhilConfig(models={"high": HIGH})
    assert controller._model_for("implementer") is None
    assert controller._model_for("nobody") is None


def test_run_usage_and_budget_come_from_run_progress(controller):
    """A run_progress event with tokens=182000, cost_usd=0.41, cost_source="reported" sets tokens and
    run_cost; when a run starts, budget_usd == config.run.max_cost_usd; after the run ends, tokens and
    run_cost are None."""
    assert controller.state.view().budget_usd == controller.config.run.max_cost_usd > 0
    controller._handle(ChatEvent("run_progress", {
        "node": "implement", "state": "running", "tasks_done": 0, "tasks_total": 1, "keyword": "CALC",
        "started": time.time(), "tokens": 182000, "cost_usd": 0.41, "cost_source": "reported",
    }))
    view = controller.state.view()
    assert view.tokens == 182000
    assert view.run_cost == (0.41, "reported")
    controller._handle(ChatEvent("run_done", {"state": "completed", "tasks_done": 1, "tasks_total": 1}))
    view = controller.state.view()
    assert view.tokens is None
    assert view.run_cost is None


def test_a_new_run_resets_a_raised_budget(controller):
    """A previous run's raised limit doesn't carry over: following a run sets the configured budget."""
    controller._handle(ChatEvent("budget_raised", {"max_cost_usd": 9.0, "max_tokens": 1600}))
    assert controller.state.view().budget_usd == 9.0
    controller._follow()
    assert controller.state.view().budget_usd == controller.config.run.max_cost_usd


def test_a_seeded_budget_raise_wins_over_the_reset(controller):
    """A budget_raised already in the run's log is seeded by the new watcher and applies after the reset."""
    paths = ProjectPaths(controller.info.slug)
    run_events(paths, controller._run_id).append("budget_raised", max_cost_usd=3.5, max_tokens=1600)
    controller._follow()
    assert controller.state.view().budget_usd == controller.config.run.max_cost_usd
    controller._watcher.poll_once()
    controller._drain()
    assert controller.state.view().budget_usd == 3.5


def test_the_chat_uses_the_raised_budget(controller):
    """A budget_raised event with max_cost_usd=2.4 sets controller.state.view().budget_usd == 2.4."""
    controller._handle(ChatEvent("budget_raised", {"max_cost_usd": 2.4, "max_tokens": 1600}))
    assert controller.state.view().budget_usd == 2.4


def test_status_line_end_to_end(controller_following_a_scripted_run):
    """After a scripted run's progress has been followed: toolbar_text(render_toolbar(view, now, 120))
    contains the repo name, "●", the low model's short name, and "/$" (the budget)."""
    controller, view = controller_following_a_scripted_run
    text = toolbar_text(render_toolbar(view, time.time(), 120))
    assert controller.info.root.name in text
    assert "●" in text
    assert "small-model" in text
    assert "/$" in text
