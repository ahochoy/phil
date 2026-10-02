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
from tests.chat.test_controller_run import commit_on_branch, completed_with_a_commit, peek, post, to_state
from tests.helpers import run_git

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
        ["add subtract", "y", completed_with_a_commit, peek(seen, "stage", lambda c: c.stage)],
        FULL_SCRIPT,
    )
    run_id = runs[0].run_id
    assert f"Open a PR for {run_id} → main?" in text
    assert seen["stage"] == "confirm_pr"
    assert PR_PROMPT in prompts
    assert fake.calls == []


def test_a_completed_run_without_commits_does_not_ask(calc_repo, fake):
    def branch_without_commits(controller):
        conn = connect(ProjectPaths(controller.info.slug).db_path)
        record = get_run(conn, controller._run_id)
        conn.close()
        run_git(controller.info.root, "branch", record.branch, record.base_sha)
        return to_state("completed", tasks_done=1)(controller)

    seen = {}
    text, spawned, runs, *_ = run_chat(
        calc_repo, ["add subtract", "y", branch_without_commits, peek(seen, "stage", lambda c: c.stage)], FULL_SCRIPT
    )
    assert "Open a PR" not in text
    assert f"No commits on {runs[0].branch}; nothing to open a PR for." in text
    assert seen["stage"] == "idle"
    assert fake.calls == []


def test_yes_opens_the_pr(calc_repo, fake):
    seen = {}
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        [
            "add subtract", "y", completed_with_a_commit,
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
            "add subtract", "y", completed_with_a_commit,
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
        ["add subtract", "y", completed_with_a_commit, "n", peek(seen, "stage", lambda c: c.stage)],
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
            "add subtract", "y", completed_with_a_commit,
            lambda c: setattr(c.io, "submit", hold) or WAKE,
            "add multiply",
            peek(seen, "stage", lambda c: (c.stage, c._goal_text)),
        ],
        FULL_SCRIPT,
    )
    assert f"No PR. `phil pr {runs[0].run_id}` opens one later." in text
    assert seen["stage"] == ("routing", "add multiply")
    assert fake.calls == []


def test_an_unavailable_publisher_is_reported(calc_repo, monkeypatch):
    # The autouse guard's publisher: unavailable with "gh is disabled in tests".
    text, spawned, runs, *_ = run_chat(
        calc_repo, ["add subtract", "y", completed_with_a_commit, "y"], FULL_SCRIPT
    )
    run_id = runs[0].run_id
    assert "Couldn't open the PR: gh is disabled in tests" in text
    assert "PublishRefused" not in text
    assert f"Fix that, then `phil pr {run_id}`." in text
    assert stored(calc_repo, run_id).pr_url is None


def test_a_push_failure_is_reported(calc_repo, fake):
    fake.fail["push"] = "git push failed: [rejected] denied"
    text, spawned, runs, *_ = run_chat(
        calc_repo, ["add subtract", "y", completed_with_a_commit, "y"], FULL_SCRIPT
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


def finish_incomplete(controller):
    import json

    issues = [
        {"severity": "blocker", "note": "Diff is empty"},
        {"severity": "major", "note": "no tests"},
        {"severity": "minor", "note": "nit"},
    ]
    run_dir = ProjectPaths(controller.info.slug).run_dir(controller._run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "open_issues.json").write_text(json.dumps(issues))
    commit_on_branch(controller)
    return to_state("incomplete", tasks_done=1)(controller)


ANYWAY_PROMPT = "Open a PR anyway? [y / n] › "


def test_an_incomplete_run_gets_a_notice_and_an_offer_to_open_a_pr_anyway(calc_repo, fake):
    seen = {}
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        ["add subtract", "y", finish_incomplete, peek(seen, "after", lambda c: (c.stage, c._run_id, c._pr_offer))],
        FULL_SCRIPT,
    )
    run_id = runs[0].run_id
    assert f"Run {run_id} finished with 2 blocking issue(s) open; nothing to merge yet. See /show." in text
    assert f"Review it: phil diff {run_id}" in text
    assert f"Run {run_id} completed" not in text
    assert "/resume" not in text  # a finished run can't be resumed
    assert ANYWAY_PROMPT in prompts and PR_PROMPT not in prompts
    assert seen["after"] == ("confirm_pr", None, run_id)
    assert fake.calls == []


def test_yes_opens_an_incomplete_runs_pr_with_its_open_issues_listed(calc_repo, fake):
    text, spawned, runs, *_ = run_chat(calc_repo, ["add subtract", "y", finish_incomplete, "y"], FULL_SCRIPT)
    assert "Opened PR #12: https://github.com/example/repo/pull/12" in text
    body = next(call for call in fake.calls if call[0] == "create_pr")[4]
    assert "## Open issues (published with --force)" in body
    forced = body.split("## Open issues (published with --force)", 1)[1].split("\n## ", 1)[0]
    assert "- (blocker) Diff is empty" in forced and "- (major) no tests" in forced
    assert "nit" not in forced


def test_an_incomplete_run_without_commits_is_not_offered_even_anyway(calc_repo, fake):
    def incomplete_without_commits(controller):
        return to_state("incomplete", tasks_done=1)(controller)

    seen = {}
    text, spawned, runs, factory, prompts = run_chat(
        calc_repo,
        ["add subtract", "y", incomplete_without_commits, peek(seen, "stage", lambda c: c.stage)],
        FULL_SCRIPT,
    )
    assert f"No commits on {runs[0].branch}; nothing to open a PR for." in text
    assert ANYWAY_PROMPT not in prompts
    assert seen["stage"] == "idle"


def test_no_leaves_an_incomplete_run_unpublished(calc_repo, fake):
    seen = {}
    text, spawned, runs, *_ = run_chat(
        calc_repo, ["add subtract", "y", finish_incomplete, "n", peek(seen, "stage", lambda c: c.stage)], FULL_SCRIPT
    )
    assert seen["stage"] == "idle"
    assert fake.calls == []


def test_a_run_with_a_pr_does_not_ask(calc_repo, fake):
    def complete_with_pr(controller):
        conn = connect(ProjectPaths(controller.info.slug).db_path)
        from phil.store.runs import update_run

        update_run(conn, controller._run_id, pr_url="https://x/pull/3", pr_number=3, pr_state="open")
        conn.close()
        return completed_with_a_commit(controller)

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
        ["add subtract", "y", completed_with_a_commit, peek(seen, "saved", saved)],
        FULL_SCRIPT,
    )
    assert seen["saved"] == "idle"
