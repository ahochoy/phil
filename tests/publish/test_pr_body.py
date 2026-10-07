import sys
from pathlib import Path

import pytest

from phil.publish.pr_body import find_pr_template, newest_output, output_count, pr_title, render_pr_body
from phil.store.artifacts import ArtifactStore
from phil.store.telemetry import Totals
from tests.run.conftest import calc_plan


def make_run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run"
    ArtifactStore(run_dir).write_plan(calc_plan())
    return run_dir


def write_review(run_dir: Path, name: str, **fields) -> None:
    data = {
        "verdict": "approve",
        "issues": [],
        "assumption_resolutions": [],
        "self_check": {"assumptions": [], "evidence": [], "risks": [], "unverified": [], "out_of_scope": []},
    }
    data.update(fields)
    ArtifactStore(run_dir).write_json("outputs", name, data)


def test_title_uses_keyword_and_first_sentence():
    plan = calc_plan().model_copy(update={"description": "Add subtract. Then more."})
    title = pr_title(plan)
    assert title.startswith("CALC: Add subtract.")
    assert "Then" not in title

    long_plan = calc_plan().model_copy(update={"description": "A" * 200})
    long_title = pr_title(long_plan)
    assert len(long_title) == 72
    assert long_title.endswith("…")


def test_body_says_none_when_nothing_needs_action(tmp_path):
    run_dir = make_run_dir(tmp_path)
    write_review(
        run_dir,
        "review-run-3",
        verdict="approve",
        issues=[],
        assumption_resolutions=["confirmed: x"],
    )
    body = render_pr_body(run_id="r-7f3a", run_dir=run_dir, totals=None, template=None)
    assert "## Action needed\nNone." in body
    assert "- Reviewer: approve" in body
    assert "- Tests: no new failures against the base" in body


def test_body_lists_open_issues_and_unconfirmed_assumptions(tmp_path):
    run_dir = make_run_dir(tmp_path)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "open_issues.json").write_text(
        '[{"severity": "major", "note": "still failing: test_a", "task_id": "CALC-001"},'
        ' {"severity": "major", "note": "still failing: test_a", "task_id": "CALC-001"},'
        ' {"severity": "minor", "note": "nit", "task_id": null}]'
    )
    write_review(
        run_dir,
        "review-run-1",
        verdict="changes",
        assumption_resolutions=["issue raised: y"],
    )
    body = render_pr_body(run_id="r-7f3a", run_dir=run_dir, totals=None, template=None)
    action = body.split("## Action needed\n", 1)[1].split("\n\n", 1)[0]
    assert action.count("still failing: test_a") == 1
    assert "nit" in action
    assert "- Assumption not confirmed: issue raised: y" in body
    assert "- Tests: 1 still failing (see Action needed)" in body


def test_body_uses_the_newest_review(tmp_path):
    run_dir = make_run_dir(tmp_path)
    write_review(run_dir, "review-run-2", verdict="changes")
    write_review(run_dir, "review-run-10", verdict="approve")
    body = render_pr_body(run_id="r-7f3a", run_dir=run_dir, totals=None, template=None)
    assert "- Reviewer: approve" in body


def test_body_tolerates_malformed_open_issues_json(tmp_path):
    run_dir = make_run_dir(tmp_path)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "open_issues.json").write_text("{")
    body = render_pr_body(run_id="r-7f3a", run_dir=run_dir, totals=None, template=None)
    assert "## Action needed\nNone." in body


def test_body_appends_the_template_unfilled(tmp_path):
    run_dir = make_run_dir(tmp_path)
    template = "## Checklist\n- [ ] docs"
    body = render_pr_body(run_id="r-7f3a", run_dir=run_dir, totals=None, template=template)
    assert "## Template\n## Checklist\n- [ ] docs" in body
    assert body.index("## How it was verified") < body.index("## Template")


def test_find_pr_template_prefers_github_dir(tmp_path):
    github_dir = tmp_path / ".github"
    github_dir.mkdir()
    (github_dir / "pull_request_template.md").write_text("github template")
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "pull_request_template.md").write_text("docs template")
    assert find_pr_template(tmp_path) == "github template"

    empty = tmp_path / "empty"
    empty.mkdir()
    assert find_pr_template(empty) is None


def test_usage_line_uses_format_cost(tmp_path):
    run_dir = make_run_dir(tmp_path)
    totals = Totals(tokens=1234, cost_usd=0.42, cost_source="estimated")
    body = render_pr_body(run_id="r-7f3a", run_dir=run_dir, totals=totals, template=None)
    assert "Total: 1,234 tokens · ~$0.42" in body


def test_newest_output_picks_highest_numbered_and_skips_rejected(tmp_path):
    run_dir = make_run_dir(tmp_path)
    outputs = run_dir / "outputs"
    outputs.mkdir(parents=True)
    (outputs / "review-run-2.json").write_text('{"n": 2}')
    (outputs / "review-run-10.json").write_text('{"n": 10}')
    (outputs / "review-run-99.rejected.json").write_text('{"n": 99}')
    assert newest_output(run_dir, "review") == {"n": 10}


def test_newest_output_skips_unreadable_files(tmp_path):
    run_dir = make_run_dir(tmp_path)
    outputs = run_dir / "outputs"
    outputs.mkdir(parents=True)
    (outputs / "review-run-1.json").write_text("{")
    (outputs / "review-run-0.json").write_text('{"n": 0}')
    assert newest_output(run_dir, "review") == {"n": 0}


def test_newest_output_is_none_without_outputs(tmp_path):
    run_dir = make_run_dir(tmp_path)
    assert newest_output(run_dir, "review") is None


def test_output_count_excludes_rejected_and_unreadable(tmp_path):
    run_dir = make_run_dir(tmp_path)
    outputs = run_dir / "outputs"
    outputs.mkdir(parents=True)
    (outputs / "tester-run-0.json").write_text('{"n": 0}')
    (outputs / "tester-run-1.json").write_text('{"n": 1}')
    (outputs / "tester-run-2.rejected.json").write_text('{"n": 2}')
    (outputs / "tester-run-3.json").write_text("{")
    assert output_count(run_dir, "tester") == 2
    assert output_count(run_dir, "review") == 0


def test_find_pr_template_skips_a_symlinked_template(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("do not post me")
    repo = tmp_path / "repo"
    (repo / ".github").mkdir(parents=True)
    (repo / ".github" / "pull_request_template.md").symlink_to(secret)
    assert find_pr_template(repo) is None

    (repo / "docs").mkdir()
    (repo / "docs" / "pull_request_template.md").write_text("docs template")
    assert find_pr_template(repo) == "docs template"


def test_find_pr_template_tolerates_bad_bytes(tmp_path):
    (tmp_path / ".github").mkdir()
    template = tmp_path / ".github" / "pull_request_template.md"
    template.write_bytes(b"caf\xe9 checklist")
    assert find_pr_template(tmp_path) == "caf\ufffd checklist"


def test_find_pr_template_tolerates_a_read_error(tmp_path, monkeypatch):
    # Portable stand-in for an unreadable file (chmod 0 doesn't block reads on Windows).
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "pull_request_template.md").write_text("checklist", encoding="utf-8")

    def denied(self, *args, **kwargs):
        raise PermissionError(13, "Permission denied", str(self))

    monkeypatch.setattr(Path, "read_text", denied)
    assert find_pr_template(tmp_path) is None


@pytest.mark.skipif(sys.platform == "win32", reason="chmod 0 doesn't make a file unreadable on Windows")
def test_find_pr_template_tolerates_unreadable_files(tmp_path):
    (tmp_path / ".github").mkdir()
    template = tmp_path / ".github" / "pull_request_template.md"
    template.write_bytes(b"caf\xe9 checklist")
    template.chmod(0)
    try:
        assert find_pr_template(tmp_path) is None
    finally:
        template.chmod(0o644)
