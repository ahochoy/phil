"""Chat jobs on real threads: each job must use its own SQLite connection (telemetry, parking)."""

import threading

from phil.contracts import Brief
from tests.chat.conftest import critique, goal, plan
from tests.chat.test_controller import FULL_SCRIPT, run_chat

JOIN_S = 30.0


def thread_submit(threads):
    """Run each job on a real thread and wait for it, so the test stays deterministic."""

    def submit(job):
        thread = threading.Thread(target=job, daemon=True)
        thread.start()
        thread.join(JOIN_S)
        assert not thread.is_alive(), "job did not finish"
        threads.append(thread)

    return submit


def test_goal_plan_approve_on_real_threads(calc_repo):
    threads = []
    text, spawned, runs, factory, _ = run_chat(
        calc_repo,
        ["add subtract", "y"],
        {"intake": [goal()], "architect": [plan()], "critic": [critique()]},
        submit=thread_submit(threads),
    )
    assert "Details: /more 1" not in text, text  # no failure callout
    assert len(threads) == 3  # routing, intake, then the plan
    assert "Plan CALC v1" in text
    assert [r.run_id for r in runs] == [spawned[0][0]]
    assert spawned[0][1:] == ("start", None)


def test_revision_on_a_real_thread(calc_repo):
    threads = []
    text, spawned, runs, *_ = run_chat(
        calc_repo,
        ["add subtract", "edit", "smaller", "y"],
        {"intake": [goal()], "architect": [plan(), plan(n=2)], "critic": [critique(), critique()]},
        submit=thread_submit(threads),
    )
    assert "Details: /more 1" not in text, text  # no failure callout
    assert "Plan CALC v2" in text
    assert len(runs) == 1


def test_btw_on_a_real_thread(calc_repo):
    threads = []
    text, *_ = run_chat(
        calc_repo,
        ["add subtract", "/btw where is add?", "n"],
        {**FULL_SCRIPT, "btw": [Brief(headline="add lives in calc.py")]},
        submit=thread_submit(threads),
    )
    assert "/btw failed" not in text, text
    assert "add lives in calc.py" in text
    assert len(threads) == 4  # routing, intake, /btw, the plan
