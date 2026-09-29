"""The chat's side of pull requests: asking to open one after a completed run, and the PR monitor."""

import threading
import time

import pytest

from phil.chat.controller import PROMPTS, WAKE
from phil.publish.publisher import FakePublisher
from phil.store.db import connect
from phil.store.paths import ProjectPaths
from phil.store.runs import get_run
from tests.chat.test_controller import FULL_SCRIPT, run_chat
from tests.chat.test_controller_run import peek, post, to_state

PR_PROMPT = "Open a PR? [y / n] › "


@pytest.fixture
def fake(monkeypatch):
    publisher = FakePublisher()
    monkeypatch.setattr("phil.publish.publisher.make_publisher", lambda repo_root: publisher)
    return publisher


def stored(repo, run_id):
    from phil.repo import resolve_repo

    conn = connect(ProjectPaths(resolve_repo(repo).slug).db_path)
    try:
        return get_run(conn, run_id)
    finally:
        conn.close()


def test_the_pr_prompt():
    assert PROMPTS["confirm_pr"] == PR_PROMPT


def test_a_completed_run_offers_a_pr(calc_repo, fake):
    seen = {}
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        ["add subtract", "y", to_state("completed", tasks_done=1), peek(seen, "stage", lambda c: c.stage)],
        FULL_SCRIPT,
    )
    run_id = runs[0].run_id
    assert f"Open a PR for {run_id} → main?" in text
    assert seen["stage"] == "confirm_pr"
    assert PR_PROMPT in prompts
    assert fake.calls == []


def test_yes_opens_the_pr(calc_repo, fake):
    seen = {}
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        [
            "add subtract", "y", to_state("completed", tasks_done=1),
            "y",
            peek(seen, "stage", lambda c: (c.stage, c.state.view().step)),
        ],
        FULL_SCRIPT,
    )
    run_id = runs[0].run_id
    assert f"Opening a PR for {run_id}…" in text
    assert "Opened PR #12: https://github.com/example/repo/pull/12" in text
    assert stored(calc_repo, run_id).pr_number == 12
    assert seen["stage"] == ("idle", None)
    assert ("push", runs[0].branch) in fake.calls


def test_the_pr_opens_in_the_background(calc_repo, fake):
    pending = []
    seen = {}

    def run_job(controller):
        pending.pop(0)()
        return WAKE

    text, *_ = run_chat(
        calc_repo,
        [
            "add subtract", "y", to_state("completed", tasks_done=1),
            lambda c: setattr(c.io, "submit", pending.append) or WAKE,
            "y",
            peek(seen, "opening", lambda c: (c.stage, c.state.view().step)),
            run_job,
            peek(seen, "opened", lambda c: (c.stage, c.state.view().step)),
        ],
        FULL_SCRIPT,
    )
    assert seen["opening"] == ("idle", "Opening PR")
    assert seen["opened"] == ("idle", None)
    assert "Opened PR #12:" in text


def test_no_declines(calc_repo, fake):
    seen = {}
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        ["add subtract", "y", to_state("completed", tasks_done=1), "n", peek(seen, "stage", lambda c: c.stage)],
        FULL_SCRIPT,
    )
    run_id = runs[0].run_id
    assert f"No PR. `phil pr {run_id}` opens one later." in text
    assert seen["stage"] == "idle"
    assert fake.calls == []
    assert stored(calc_repo, run_id).pr_url is None


def test_a_goal_typed_at_the_question_is_kept(calc_repo, fake):
    seen = {}
    pending = []

    def hold(job):
        pending.append(job)

    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        [
            "add subtract", "y", to_state("completed", tasks_done=1),
            lambda c: setattr(c.io, "submit", hold) or WAKE,
            "add multiply",
            peek(seen, "stage", lambda c: (c.stage, c._goal_text)),
        ],
        FULL_SCRIPT,
    )
    assert f"No PR. `phil pr {runs[0].run_id}` opens one later." in text
    assert seen["stage"] == ("intake", "add multiply")
    assert fake.calls == []


def test_an_unavailable_publisher_is_reported(calc_repo, monkeypatch):
    # The autouse guard's publisher: unavailable with "gh is disabled in tests".
    text, spawned, runs, *_ = run_chat(
        calc_repo, ["add subtract", "y", to_state("completed", tasks_done=1), "y"], FULL_SCRIPT
    )
    run_id = runs[0].run_id
    assert "Couldn't open the PR: gh is disabled in tests" in text
    assert "PublishRefused" not in text
    assert f"Fix that, then `phil pr {run_id}`." in text
    assert stored(calc_repo, run_id).pr_url is None


def test_a_push_failure_is_reported(calc_repo, fake):
    fake.fail["push"] = "git push failed: [rejected] denied"
    text, spawned, runs, *_ = run_chat(
        calc_repo, ["add subtract", "y", to_state("completed", tasks_done=1), "y"], FULL_SCRIPT
    )
    assert "Couldn't open the PR: git push failed: [rejected] denied" in text
    assert f"Fix that, then `phil pr {runs[0].run_id}`." in text


@pytest.mark.parametrize("state", ["failed", "stopped"])
def test_an_unfinished_run_does_not_ask(calc_repo, fake, state):
    seen = {}
    text, *_ = run_chat(
        calc_repo,
        ["add subtract", "y", to_state(state), peek(seen, "stage", lambda c: c.stage)],
        FULL_SCRIPT,
    )
    assert "Open a PR" not in text
    assert seen["stage"] == "idle"


def test_a_run_with_a_pr_does_not_ask(calc_repo, fake):
    def complete_with_pr(controller):
        conn = connect(ProjectPaths(controller.info.slug).db_path)
        from phil.store.runs import update_run

        update_run(conn, controller._run_id, pr_url="https://x/pull/3", pr_number=3, pr_state="open")
        conn.close()
        return to_state("completed", tasks_done=1)(controller)

    text, *_ = run_chat(calc_repo, ["add subtract", "y", complete_with_pr], FULL_SCRIPT)
    assert "Open a PR" not in text


def test_a_merged_pr_change_is_printed(calc_repo):
    text, *_ = run_chat(
        calc_repo,
        [
            post("pr_changes", changes=[
                {"run_id": "r-7f3a", "number": 12, "kind": "merged", "detail": ""},
                {"run_id": "r-8b2c", "number": 13, "kind": "cleanup_failed", "detail": "[bold]boom[/]"},
            ]),
        ],
        FULL_SCRIPT,
    )
    assert "r-7f3a merged (#12); cleaned up." in text
    assert "r-8b2c merged (#13), but cleanup failed: [bold]boom[/]" in text


def test_a_merge_found_by_the_monitor_job_is_printed(calc_repo, monkeypatch):
    from phil.publish.service import PrChange

    monkeypatch.setattr(
        "phil.chat.controller.sweep_prs", lambda info, conn, publisher: [PrChange("r-7f3a", 12, "merged")]
    )
    text, *_ = run_chat(
        calc_repo, [lambda c: c._pr_tick() or WAKE, lambda c: WAKE], FULL_SCRIPT
    )
    assert "r-7f3a merged (#12); cleaned up." in text


def test_a_failed_pr_check_is_logged_not_printed(calc_repo, caplog):
    with caplog.at_level("WARNING", logger="phil"):
        text, *_ = run_chat(
            calc_repo,
            [post("pr_changes_failed", job="pr_changes", error="OSError: offline")],
            FULL_SCRIPT,
        )
    assert "offline" not in text
    assert "OSError: offline" in caplog.text


def test_the_monitor_submits_checks_until_the_chat_ends(calc_repo):
    submitted = []
    lock = threading.Lock()

    def submit(job):
        with lock:
            submitted.append(time.monotonic())
        job()

    counts = {}

    def wait(controller):
        time.sleep(0.5)
        counts["running"] = len(submitted)
        return None  # EOF ends the chat

    run_chat(
        calc_repo, [wait], FULL_SCRIPT, submit=submit, start_pr_monitor=True, pr_check_interval_s=0.05
    )
    counts["ended"] = len(submitted)
    time.sleep(0.2)
    assert counts["running"] >= 2
    assert len(submitted) == counts["ended"]


def test_the_monitor_skips_a_tick_while_a_check_runs(calc_repo):
    held = []

    def submit(job):
        held.append(job)  # never run: the first check stays in flight

    def wait(controller):
        time.sleep(0.3)
        return None

    run_chat(calc_repo, [wait], FULL_SCRIPT, submit=submit, start_pr_monitor=True, pr_check_interval_s=0.05)
    assert len(held) == 1


def test_confirm_pr_is_saved_as_idle(calc_repo, fake):
    import json

    seen = {}

    def saved(controller):
        return json.loads((controller.session.dir / "state.json").read_text())["stage"]

    run_chat(
        calc_repo,
        ["add subtract", "y", to_state("completed", tasks_done=1), peek(seen, "saved", saved)],
        FULL_SCRIPT,
    )
    assert seen["saved"] == "idle"
