from phil.chat.planning import PlanDraft
from phil.contracts import Goal
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
