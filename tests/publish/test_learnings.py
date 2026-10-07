from pathlib import Path

from phil import platform
from phil.publish.learnings import append_learnings, learnings_entry
from phil.store.artifacts import ArtifactStore
from phil.store.paths import ProjectPaths
from phil.store.runs import RunRecord
from tests.run.conftest import calc_plan


def make_run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run"
    ArtifactStore(run_dir).write_plan(calc_plan())
    return run_dir


def make_record(run_id: str = "r-7f3a", pr_number: int | None = 12) -> RunRecord:
    return RunRecord(
        run_id=run_id,
        keyword="CALC",
        base_sha="a" * 40,
        branch=f"phil/{run_id}",
        worktree="/tmp/does-not-matter",
        state="completed",
        current_node=None,
        tasks_done=1,
        tasks_total=1,
        pid=None,
        heartbeat_at=None,
        needs_attention=None,
        story_ref=None,
        created_at="2026-09-29T00:00:00Z",
        updated_at="2026-09-29T00:00:00Z",
        pr_number=pr_number,
    )


def write_review(run_dir: Path, name: str, **fields) -> None:
    data = {
        "verdict": "approve",
        "issues": [],
        "assumption_resolutions": [],
        "self_check": {"assumptions": [], "evidence": [], "risks": [], "unverified": [], "out_of_scope": []},
    }
    data.update(fields)
    ArtifactStore(run_dir).write_json("outputs", name, data)


def test_entry_reports_assumptions_and_deduplicated_reviewer_notes(tmp_path):
    run_dir = make_run_dir(tmp_path)
    write_review(
        run_dir,
        "review-run-1",
        assumption_resolutions=["confirmed: a", "confirmed: b", "issue raised: y"],
        issues=[
            {"severity": "minor", "note": "rename helper", "task_id": "CALC-001"},
            {"severity": "minor", "note": "rename helper", "task_id": "CALC-001"},
            {"severity": "major", "note": "add a test", "task_id": "CALC-002"},
        ],
    )
    entry = learnings_entry(record=make_record(), run_dir=run_dir, today="2026-09-29")

    assert entry.startswith("## r-7f3a — 2026-09-29 — PR #12\n")
    assert "Goal: CALC: Add arithmetic" in entry
    assert "Assumptions confirmed: 2 · not confirmed: issue raised: y" in entry
    assert entry.count("rename helper") == 1
    assert "Reviewer notes:\n" in entry
    assert "add a test" in entry


def test_entry_caps_reviewer_notes_at_five(tmp_path):
    run_dir = make_run_dir(tmp_path)
    write_review(
        run_dir,
        "review-run-1",
        issues=[{"severity": "minor", "note": f"note {i}", "task_id": f"CALC-{i:03d}"} for i in range(8)],
    )
    entry = learnings_entry(record=make_record(), run_dir=run_dir, today="2026-09-29")
    notes_block = entry.split("Reviewer notes:\n", 1)[1]
    assert notes_block.count("- (") == 5


def test_entry_without_a_review_says_none_recorded(tmp_path):
    run_dir = make_run_dir(tmp_path)
    entry = learnings_entry(record=make_record(), run_dir=run_dir, today="2026-09-29")
    assert "Assumptions: none recorded" in entry
    assert "Reviewer notes: none" in entry


def test_entry_omits_the_pr_number_when_absent(tmp_path):
    run_dir = make_run_dir(tmp_path)
    entry = learnings_entry(record=make_record(pr_number=None), run_dir=run_dir, today="2026-09-29")
    assert entry.startswith("## r-7f3a — 2026-09-29\n")
    assert "PR #" not in entry


def test_entry_uses_a_placeholder_goal_when_the_plan_is_unreadable(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    entry = learnings_entry(record=make_record(), run_dir=run_dir, today="2026-09-29")
    assert "Goal: (plan unavailable)" in entry


def test_append_learnings_creates_the_file_with_a_header(tmp_path, monkeypatch):
    monkeypatch.setenv("PHIL_HOME", str(tmp_path / "home"))
    paths = ProjectPaths("calc-abc123")
    run_dir = make_run_dir(tmp_path)
    entry = learnings_entry(record=make_record(), run_dir=run_dir, today="2026-09-29")

    assert append_learnings(paths, entry, "r-7f3a") is True

    text = (paths.project_dir / "learnings.md").read_text(encoding="utf-8")
    assert text.startswith("# Phil learnings — calc-abc123\n\n")
    assert "## r-7f3a — 2026-09-29 — PR #12" in text


def test_append_learnings_adds_a_second_entry_after_a_blank_line(tmp_path, monkeypatch):
    monkeypatch.setenv("PHIL_HOME", str(tmp_path / "home"))
    paths = ProjectPaths("calc-abc123")
    run_dir = make_run_dir(tmp_path)
    first = learnings_entry(record=make_record("r-0001"), run_dir=run_dir, today="2026-09-29")
    second = learnings_entry(record=make_record("r-0002"), run_dir=run_dir, today="2026-09-30")

    append_learnings(paths, first, "r-0001")
    append_learnings(paths, second, "r-0002")

    text = (paths.project_dir / "learnings.md").read_text(encoding="utf-8")
    assert "## r-0001 — 2026-09-29" in text
    assert "## r-0002 — 2026-09-30" in text
    first_end = text.index("## r-0002")
    assert text[:first_end].endswith("\n\n")


def test_append_learnings_is_idempotent_for_an_existing_run_id(tmp_path, monkeypatch):
    monkeypatch.setenv("PHIL_HOME", str(tmp_path / "home"))
    paths = ProjectPaths("calc-abc123")
    run_dir = make_run_dir(tmp_path)
    entry = learnings_entry(record=make_record(), run_dir=run_dir, today="2026-09-29")
    append_learnings(paths, entry, "r-7f3a")
    before = (paths.project_dir / "learnings.md").read_text(encoding="utf-8")

    assert append_learnings(paths, entry, "r-7f3a") is False
    assert (paths.project_dir / "learnings.md").read_text(encoding="utf-8") == before


def test_append_learnings_keeps_existing_content_and_never_rewrites_it(tmp_path, monkeypatch):
    monkeypatch.setenv("PHIL_HOME", str(tmp_path / "home"))
    paths = ProjectPaths("calc-abc123")
    run_dir = make_run_dir(tmp_path)
    path = paths.project_dir / "learnings.md"
    path.parent.mkdir(parents=True)
    path.write_text("# my own notes\n\nsomething I wrote by hand")
    writes = []
    real_write_text = Path.write_text
    monkeypatch.setattr(Path, "write_text", lambda self, *a, **k: writes.append(self) or real_write_text(self, *a, **k))

    entry = learnings_entry(record=make_record(), run_dir=run_dir, today="2026-09-29")
    assert append_learnings(paths, entry, "r-7f3a") is True

    text = path.read_text(encoding="utf-8")
    assert text.startswith("# my own notes\n\nsomething I wrote by hand\n\n## r-7f3a ")
    assert text.count("# Phil learnings") == 0
    assert writes == []  # appended, never truncated and rewritten


def test_append_learnings_writes_the_header_once(tmp_path, monkeypatch):
    monkeypatch.setenv("PHIL_HOME", str(tmp_path / "home"))
    paths = ProjectPaths("calc-abc123")
    run_dir = make_run_dir(tmp_path)
    for run_id in ("r-0001", "r-0002", "r-0001"):
        append_learnings(paths, learnings_entry(record=make_record(run_id), run_dir=run_dir, today="2026-09-29"), run_id)

    text = (paths.project_dir / "learnings.md").read_text(encoding="utf-8")
    assert text.count("# Phil learnings") == 1
    assert text.count("## r-0001 ") == 1
    assert text.count("## r-0002 ") == 1


def test_append_learnings_locks_and_unlocks_once_through_platform(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(platform, "lock_file", lambda handle: calls.append("lock"))
    monkeypatch.setattr(platform, "unlock_file", lambda handle: calls.append("unlock"))
    monkeypatch.setenv("PHIL_HOME", str(tmp_path / "home"))
    paths = ProjectPaths("calc-abc123")
    run_dir = make_run_dir(tmp_path)
    entry = learnings_entry(record=make_record(), run_dir=run_dir, today="2026-09-29")

    assert append_learnings(paths, entry, "r-7f3a") is True

    assert calls == ["lock", "unlock"]


def test_concurrent_appends_both_land(tmp_path, monkeypatch):
    import threading

    monkeypatch.setenv("PHIL_HOME", str(tmp_path / "home"))
    paths = ProjectPaths("calc-abc123")
    run_dir = make_run_dir(tmp_path)
    run_ids = [f"r-{n:04x}" for n in range(16)]
    entries = {run_id: learnings_entry(record=make_record(run_id), run_dir=run_dir, today="2026-09-29")
               for run_id in run_ids}
    barrier = threading.Barrier(len(run_ids))

    def append(run_id):
        barrier.wait()
        append_learnings(paths, entries[run_id], run_id)

    threads = [threading.Thread(target=append, args=(run_id,)) for run_id in run_ids]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    text = (paths.project_dir / "learnings.md").read_text(encoding="utf-8")
    assert text.count("# Phil learnings") == 1
    assert all(text.count(f"## {run_id} ") == 1 for run_id in run_ids)
