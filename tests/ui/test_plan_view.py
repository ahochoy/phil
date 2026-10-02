from phil.chat.planning import PlanDraft
from phil.contracts import Goal, Task
from phil.ui.plan_view import render_goal, render_plan
from phil.ui.theme import make_console
from tests.chat.conftest import critique, plan


def text_of(fn, *args, **kw):
    console = make_console(record=True, width=120)
    fn(console, *args, **kw)
    return console.export_text()


def test_render_goal():
    text = text_of(render_goal, Goal(objective="Add a [bold]map[/bold]", constraints=["keep leaflet"], non_goals=["no auth"]))
    assert "Goal: Add a [bold]map[/bold]" in text
    assert "keep leaflet" in text and "no auth" in text


def test_render_plan_is_bounded_and_escaped():
    p = plan(n=2)
    p.tasks[0].acceptance_criteria.extend(["b", "c", "d", "e"])
    p = p.model_copy(update={"critic_notes": [f"note {i}" for i in range(7)] + ["[red]x[/red]"]})
    draft = PlanDraft(p, critique("ok"), 3)
    text = text_of(render_plan, draft, test_cmd="uv run pytest -q", test_cmd_note=None, git_note="signing on")
    assert "Plan CALC v3 · 2 tasks" in text
    assert "CALC-001" in text and "CALC-002" in text
    assert "(+2 more)" in text  # 5 criteria, 3 shown
    assert "Critic (ok):" in text
    assert "(+3 more)" in text  # 8 notes, 5 shown
    assert "Tests: uv run pytest -q" in text
    assert "signing on" in text


def test_render_plan_without_a_test_command():
    draft = PlanDraft(plan(test_cmd=None), critique("ok"), 1)
    text = text_of(render_plan, draft, test_cmd=None, test_cmd_note="no test command", git_note=None)
    assert "Tests: none" in text
    assert "no test command" in text


def test_render_plan_shows_the_full_test_cmd_and_clips_its_note():
    draft = PlanDraft(plan(test_cmd=None), critique("ok"), 1)
    long_cmd = "x" * 200
    long_note = "y" * 200
    console = make_console(record=True, width=1000)
    render_plan(console, draft, test_cmd=long_cmd, test_cmd_note=long_note, git_note=None)
    text = console.export_text()
    assert long_cmd in text
    assert long_note not in text
    assert "…" in text


def test_render_goal_is_bounded():
    long = "z" * 150 + " " + "z" * 149
    g = Goal(
        objective=long,
        constraints=[long],
        non_goals=[long],
        open_questions=[f"question {i} {long}" for i in range(5)],
    )
    console = make_console(record=True, width=1000)
    render_goal(console, g)
    text = console.export_text()
    assert long not in text
    assert "question 0" in text and "question 2" in text
    assert "question 3" not in text
    assert "(+2 more)" in text


def test_render_plan_marks_check_tasks():
    base = plan()
    check = Task(
        id="CALC-002", description="Edit copy", acceptance_criteria=["c"], verify="check", check_cmd="grep -q [b] x"
    )
    draft = PlanDraft(base.model_copy(update={"tasks": [*base.tasks, check]}), critique("ok"), 1)
    text = text_of(render_plan, draft, test_cmd="uv run pytest -q", test_cmd_note=None, git_note=None)
    assert "CALC-002  Edit copy  (check: grep -q [b] x)" in text
    assert "Step 1  (check" not in text


def test_render_plan_labels_where_the_test_command_came_from():
    draft = PlanDraft(plan(test_cmd=None), critique("ok"), 1)
    for source, label in (("plan", "(from the plan)"), ("config", "(from your config)"), ("detected", "(detected)")):
        text = text_of(
            render_plan, draft, test_cmd="go test ./...", test_cmd_source=source, test_cmd_note=None, git_note=None
        )
        assert f"Tests: go test ./... {label}" in text


def test_render_plan_names_the_file_a_configured_test_command_came_from():
    draft = PlanDraft(plan(test_cmd=None), critique("ok"), 1)
    for origin in ("phil.toml", "/home/me/.phil/config.toml"):
        text = text_of(
            render_plan, draft, test_cmd="make check", test_cmd_source="config", test_cmd_origin=origin,
            test_cmd_note=None, git_note=None,
        )
        assert f"Tests: make check (from {origin})" in text


def test_render_plan_without_a_test_command_points_at_your_config():
    draft = PlanDraft(plan(test_cmd=None), critique("ok"), 1)
    text = text_of(render_plan, draft, test_cmd=None, test_cmd_source="none", test_cmd_note=None, git_note=None)
    assert "Tests: none — set test_cmd in the plan or your config" in text


def test_render_plan_shows_the_setup_command_above_the_test_line():
    draft = PlanDraft(plan(test_cmd="pytest"), critique("ok"), 1)
    text = text_of(
        render_plan, draft, test_cmd="pytest", test_cmd_note=None, git_note=None,
        setup_cmd="npm ci", setup_cmd_source="detected",
    )
    assert "setup: npm ci (detected)" in text
    assert text.index("setup: npm ci (detected)") < text.index("Tests: pytest")


def test_render_plan_shows_a_configured_setup_command_with_no_suffix():
    draft = PlanDraft(plan(test_cmd="pytest"), critique("ok"), 1)
    text = text_of(
        render_plan, draft, test_cmd="pytest", test_cmd_note=None, git_note=None,
        setup_cmd="npm ci", setup_cmd_source="config",
    )
    assert "setup: npm ci\n" in text  # no "(detected)" when it came from config


def test_render_plan_without_a_setup_command_shows_no_setup_line():
    draft = PlanDraft(plan(test_cmd="pytest"), critique("ok"), 1)
    text = text_of(render_plan, draft, test_cmd="pytest", test_cmd_note=None, git_note=None)
    assert "setup:" not in text


def test_render_plan_without_a_test_command_for_an_all_check_plan():
    task = Task(id="CALC-001", description="Edit copy", acceptance_criteria=["c"], verify="check", check_cmd="grep -q x a")
    draft = PlanDraft(plan(test_cmd=None).model_copy(update={"tasks": [task]}), critique("ok"), 1)
    text = text_of(render_plan, draft, test_cmd=None, test_cmd_source="none", test_cmd_note=None, git_note=None)
    assert "Tests: none (check tasks only)" in text
    assert "set test_cmd" not in text
