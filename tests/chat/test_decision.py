import pytest

from phil.chat.decision import (
    BODY_LINES, approach_decision, approval_decision, fix_decision, pause_decision, pr_decision,
    question_decision, replace_decision, settled_line,
)


def labels(decision):
    return [o.label for o in decision.options]


def answers(decision):
    return [o.answer for o in decision.options]


def test_approval_pause():
    d = pause_decision("r-4f2a", {"reason": "approval", "task_id": "CALC-002", "commands": ["node build.mjs"],
                                  "labels": ["changes things"], "saves": ["node build.mjs"],
                                  "rules": ["node build.mjs"], "repo": "calc",
                                  "options": ["approve", "always_repo", "deny", "abort"], "summary": "x"})
    assert d.kind == "approval"
    assert d.title == "⏸ r-4f2a needs you · approve a command"
    assert d.body == ("CALC-002 wants to run:", "  node build.mjs  (changes things)  → saves node build.mjs")
    assert labels(d) == ["Allow for this run", "Always allow in calc", "Deny", "Stop the run"]
    assert answers(d) == ["approve", "always_repo", "deny", "abort"]
    assert d.default == 0
    assert settled_line(d, d.options[0]) == "✓ Allow for this run · node build.mjs"


TWO = {
    "reason": "approval", "task_id": "CALC-001", "commands": ["gh pr checks 42", "make test-unit calc"],
    "labels": ["reads only", "may change things"], "saves": ["gh pr checks *", "make test-unit *"],
    "rules": ["gh pr checks *", "make test-unit *"], "repo": "calc",
    "options": ["approve", "always_repo", "always_everywhere", "deny", "abort"], "summary": "x",
}


def test_approval_pause_offers_saving_rules_everywhere_verbatim():
    d = pause_decision("r-1", TWO)
    assert d.body == (
        "CALC-001 wants to run:",
        "  gh pr checks 42  (reads only)  → saves gh pr checks *",
        "  make test-unit calc  (may change things)  → saves make test-unit *",
    )
    # More than one rule: the "always" answers say how many they save (N2).
    assert labels(d) == ["Allow for this run", "Always allow in calc (2 rules)", "Always allow everywhere (2 rules)",
                         "Deny", "Stop the run"]
    assert answers(d) == ["approve", "always_repo", "always_everywhere", "deny", "abort"]
    assert d.default == 0


def test_approval_pause_without_saving_offers_only_the_run():
    d = pause_decision("r-1", {"reason": "approval", "task_id": "CALC-001", "commands": ["make x '*'"],
                               "labels": ["may change things"], "saves": [None], "rules": [], "repo": "calc",
                               "options": ["approve", "deny", "abort"], "summary": "x"})
    assert d.body == ("CALC-001 wants to run:", "  make x '*'  (may change things)  → can't be saved")
    assert labels(d) == ["Allow for this run", "Deny", "Stop the run"]


def test_an_approval_body_is_never_cut_here_the_renderer_decides():
    # N2: the callout renderer counts what it leaves out, so it needs every entry.
    commands = [f"make target-{n}" for n in range(12)]
    d = pause_decision("r-1", {"reason": "approval", "task_id": "CALC-001", "commands": commands,
                               "labels": ["may change things"] * 12, "saves": [f"{c} *" for c in commands],
                               "rules": [f"{c} *" for c in commands], "repo": "calc",
                               "options": ["approve", "always_repo", "deny", "abort"], "summary": "x"})
    assert len(d.body) == 13 and "… see /more 1" not in d.body
    assert labels(d)[1] == "Always allow in calc (12 rules)"


def test_approval_pause_shows_control_characters_as_question_marks():
    # M3: an agent's command can't recolour or move the terminal's cursor; only the display changes.
    command = "make test \x1bred\x07"
    d = pause_decision("r-1", {"reason": "approval", "task_id": "CALC-001", "commands": [command],
                               "labels": ["may change things"], "saves": [None], "rules": [], "repo": "ca\x1bc",
                               "options": ["approve", "always_repo", "deny", "abort"], "summary": "x"})
    assert d.body[1] == "  make test ?red?  (may change things)  → can't be saved"
    assert labels(d)[1] == "Always allow in ca?c"
    assert settled_line(d, d.options[0]) == "✓ Allow for this run · make test ?red?"
    assert not any("\x1b" in line for line in (*d.body, *labels(d), d.note))


def test_other_pauses_keep_their_abort_label():
    d = pause_decision("r-1", {"reason": "budget", "options": ["continue", "abort"], "summary": "x"})
    assert labels(d) == ["Keep going past the budget", "Abort the run"]


def test_attempts_pause_lists_three_problems_and_defaults_to_retry():
    d = pause_decision("r-1", {"reason": "attempts", "task_id": "CALC-001", "phase": "green",
                               "problems": ["a", "b", "c", "d"], "options": ["retry", "skip", "full", "abort"],
                               "summary": "CALC-001 failed 3 attempts in the green phase"})
    assert d.kind == "pause"
    assert d.title == "⏸ r-1 needs you · CALC-001 failed 3 attempts in the green phase"
    assert d.body == ("a", "b", "c", "Details: /more 1")
    assert labels(d) == ["Retry the task", "Skip this task", "Plan it fully instead", "Abort the run"]
    assert d.default == 0


def test_attempts_pause_on_the_check_task():
    d = pause_decision("r-1", {"reason": "attempts", "task_id": "CALC-001", "problems": ["a"],
                               "options": ["retry", "abort"],
                               "summary": "CALC-001 failed 3 attempts on the check task"})
    assert d.title == "⏸ r-1 needs you · CALC-001 failed 3 attempts on the check task"


def test_attempts_pause_fix_after_review_did_not_pass_the_gate():
    d = pause_decision("r-1", {"reason": "attempts", "task_id": "CALC-001", "problems": ["a"],
                               "options": ["retry", "abort"],
                               "summary": "CALC-001's fix after review didn't pass the gate"})
    assert d.title == "⏸ r-1 needs you · CALC-001's fix after review didn't pass the gate"


def test_attempts_pause_falls_back_without_a_summary():
    d = pause_decision("r-1", {"reason": "attempts", "task_id": "CALC-001", "problems": ["a"],
                               "options": ["retry", "abort"], "summary": ""})
    assert d.title == "⏸ r-1 needs you · CALC-001 needs another attempt"


@pytest.mark.parametrize("reason,options,title_end,default_answer", [
    ("cmd_not_found", ["retry", "abort"], "a command isn't installed", "retry"),
    ("setup_failed", ["retry", "abort"], "setup failed", "retry"),
    ("no_test_cmd", ["retry", "skip", "abort"], "no test command", "retry"),
    ("commit_failed", ["retry", "bypass", "abort"], "the commit failed", "retry"),
    ("review_failed", ["retry", "finish", "abort"], "the reviewer gave no valid review", "retry"),
    ("budget", ["continue", "abort"], "the budget is used up", "continue"),
])
def test_every_pause_reason_has_a_title_and_a_safe_default(reason, options, title_end, default_answer):
    d = pause_decision("r-1", {"reason": reason, "options": options, "summary": "s", "problems": ["p"]})
    assert d.title == f"⏸ r-1 needs you · {title_end}"
    assert d.options[d.default].answer == default_answer
    assert d.options[-1].answer == "abort"


def test_no_test_cmd_body_names_the_task_before_the_fix():
    d = pause_decision("r-1", {"reason": "no_test_cmd", "problems": ["CALC-001 has no test command"],
                               "options": ["retry", "skip", "abort"], "summary": "s"})
    assert d.body == ("CALC-001 has no test command", "Set [project] test_cmd in phil.toml, then retry.")


def test_abort_is_never_the_default_even_alone_first():
    d = pause_decision("r-1", {"reason": "mystery", "options": ["abort", "retry"], "summary": "Something odd"})
    assert d.options[d.default].answer != "abort"
    assert d.options[-1].answer == "abort"
    assert d.title == "⏸ r-1 needs you · Something odd"


def test_a_long_body_is_capped():
    d = pause_decision("r-1", {"reason": "review_failed", "options": ["retry", "abort"], "summary": "s",
                               "problems": [f"p{i}" for i in range(20)]})
    assert len(d.body) <= BODY_LINES
    assert d.body[-1] == "… see /more 1"


def test_a_multi_line_problem_is_capped_by_rendered_lines():
    d = pause_decision("r-1", {"reason": "commit_failed", "options": ["retry", "abort"], "summary": "s",
                               "problems": ["\n".join(f"l{i}" for i in range(12))]})
    assert len(d.body) <= BODY_LINES
    assert d.body[-1] == "… see /more 1"


def test_settled_line_for_a_decline_uses_a_dot_not_a_check():
    d = pr_decision("r-1", force=False)
    assert settled_line(d, d.options[1]) == "· Not now"


def test_question_decision():
    d = question_decision(2, 3, "Which database?", "It changes the schema.", ["Postgres", "SQLite"])
    assert d.kind == "question"
    assert d.title == "Question 2 of 3: Which database?"
    assert d.body == ("It changes the schema.",)
    assert labels(d) == ["Postgres", "SQLite", "Something else (type it)", "Plan with what you know"]
    assert answers(d) == ["1", "2", "3", "go"]
    assert d.options[2].typed
    assert settled_line(d, d.options[0]) == "Answer: Postgres"


def test_a_question_without_options_is_free_text():
    assert question_decision(1, 1, "Anything else?", "", []) is None


def test_approach_decision():
    d = approach_decision([("Inline", "Add it to calc.py."), ("Module", "A new module.")])
    assert labels(d) == ["Inline (recommended)", "Module", "Describe your own"]
    assert answers(d) == ["1", "2", "3"]
    assert d.options[0].detail == "Add it to calc.py." and d.options[2].typed


def test_plan_approval_and_confirmations():
    assert answers(approval_decision()) == ["y", "edit", "n"]
    assert labels(approval_decision()) == ["Approve and run", "Edit the plan", "Cancel"]
    assert approval_decision().options[1].typed
    assert labels(pr_decision("r-1", force=False)) == ["Open the PR", "Not now"]
    assert labels(pr_decision("r-1", force=True)) == ["Open the PR anyway", "Not now"]
    assert answers(pr_decision("r-1", force=False)) == ["y", "n"]
    assert labels(fix_decision()) == ["Quick fix", "Plan it fully", "Not now"]
    assert answers(fix_decision()) == ["y", "full", "n"]
    assert labels(replace_decision()) == ["Replace it", "Keep the current goal"]
    assert answers(replace_decision()) == ["y", "n"]
