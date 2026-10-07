"""Light per-project learnings notes, appended to `learnings.md` on a merge cleanup.

Kept free of langgraph/langchain/deepagents at module import time (see
`implementer-common.md`), even though the modules used here are all safe to import eagerly.
"""

try:  # SPIKE: Windows has no fcntl
    import fcntl
except ImportError:
    fcntl = None
from pathlib import Path

from phil.publish.pr_body import newest_output, pr_title
from phil.run.state import clean_note, dedupe_issues, issue_line
from phil.store.artifacts import ArtifactStore
from phil.store.paths import ProjectPaths
from phil.store.runs import RunRecord

_MAX_REVIEWER_NOTES = 5


def _goal_line(run_dir: Path) -> str:
    try:
        plan = ArtifactStore(run_dir).read_plan()
    except (OSError, ValueError):
        return "Goal: (plan unavailable)"
    return f"Goal: {pr_title(plan)}"


def _assumptions_line(newest_review: dict | None) -> str:
    if newest_review is None:
        return "Assumptions: none recorded"
    resolutions = newest_review.get("assumption_resolutions", [])
    confirmed = [text for text in resolutions if text.strip().lower().startswith("confirmed")]
    not_confirmed = [clean_note(text) for text in resolutions if not text.strip().lower().startswith("confirmed")]
    joined = "; ".join(not_confirmed) if not_confirmed else "none"
    return f"Assumptions confirmed: {len(confirmed)} · not confirmed: {joined}"


def _reviewer_notes_block(newest_review: dict | None) -> str:
    if newest_review is None:
        return "Reviewer notes: none"
    issues = dedupe_issues(newest_review.get("issues", []))[:_MAX_REVIEWER_NOTES]
    if not issues:
        return "Reviewer notes: none"
    return "Reviewer notes:\n" + "\n".join(issue_line(issue) for issue in issues)


def learnings_entry(*, record: RunRecord, run_dir: Path, today: str) -> str:
    """Render one `learnings.md` entry for `record`, ending with a single trailing newline."""
    header = f"## {record.run_id} — {today}"
    if record.pr_number is not None:
        header += f" — PR #{record.pr_number}"
    newest_review = newest_output(run_dir, "review")
    lines = [header, _goal_line(run_dir), _assumptions_line(newest_review), _reviewer_notes_block(newest_review)]
    return "\n".join(lines) + "\n"


def append_learnings(paths: ProjectPaths, entry: str, run_id: str) -> bool:
    """Append `entry` to the project's `learnings.md`, creating it with its header if needed.

    Does nothing (and returns False) if a line starting `## <run_id> ` is already present.
    The file is only ever appended to, under an exclusive lock, so concurrent sweeps (the
    chat's monitor and `phil runs` in another terminal) can neither lose nor duplicate entries.
    """
    path = paths.project_dir / "learnings.md"
    marker = f"## {run_id} "
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+", encoding="utf-8", errors="replace") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            handle.seek(0)
            existing = handle.read()
            if any(line.startswith(marker) for line in existing.splitlines()):
                return False
            if not existing:
                handle.write(f"# Phil learnings — {paths.slug}\n\n{entry}")
            else:
                handle.write(("" if existing.endswith("\n") else "\n") + "\n" + entry)
            handle.flush()
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
    return True
