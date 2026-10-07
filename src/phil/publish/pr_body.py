"""Render the pull request title and body Phil sends to GitHub from a run's own records.

Kept free of langgraph/langchain/deepagents at module import time (see
`implementer-common.md`), even though `phil.contracts` and `phil.store.*` are safe to import
eagerly here.
"""

import json
import re
from pathlib import Path

from phil.contracts import Plan
from phil.run.state import blocking_count, clean_note, dedupe_issues, issue_line, task_lines
from phil.store.artifacts import ArtifactStore
from phil.store.telemetry import Totals, format_cost

_TEMPLATE_CANDIDATES = (
    ".github/pull_request_template.md",
    ".github/PULL_REQUEST_TEMPLATE.md",
    "docs/pull_request_template.md",
)
_TEMPLATE_LIMIT = 20_000
_TITLE_LIMIT = 72
_FIRST_SENTENCE_RE = re.compile(r"^(.*?[.!?])(\s|$)")


def _outputs_for(run_dir: Path, kind: str) -> list[tuple[int, Path]]:
    """Readable `outputs/<kind>-*.json` files (rejected ones excluded), each paired with its
    trailing attempt number (the integer after the last `-` in the stem; -1 if non-numeric)."""
    outputs_dir = run_dir / "outputs"
    if not outputs_dir.is_dir():
        return []
    found = []
    for path in outputs_dir.glob(f"{kind}-*.json"):
        if path.name.endswith(".rejected.json"):
            continue
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        stem = path.stem
        suffix = stem.rsplit("-", 1)[-1]
        attempt = int(suffix) if suffix.isdigit() else -1
        found.append((attempt, path))
    return found


def newest_output(run_dir: Path, kind: str) -> dict | None:
    """The parsed JSON of the newest `outputs/<kind>-*.json`, by the integer after the last
    `-` in the stem. Unreadable/malformed files are skipped; `*.rejected.json` files are not
    outputs and are excluded."""
    candidates = _outputs_for(run_dir, kind)
    if not candidates:
        return None
    _, path = max(candidates, key=lambda item: item[0])
    return json.loads(path.read_text(encoding="utf-8"))


def output_count(run_dir: Path, kind: str) -> int:
    """Number of readable `outputs/<kind>-*.json` files (rejected ones excluded)."""
    return len(_outputs_for(run_dir, kind))


def pr_title(plan: Plan) -> str:
    description = " ".join(plan.description.split())
    match = _FIRST_SENTENCE_RE.match(description)
    first_sentence = match.group(1) if match else description
    title = f"{plan.keyword}: {first_sentence}"
    if len(title) > _TITLE_LIMIT:
        title = title[: _TITLE_LIMIT - 1].rstrip() + "…"
    return title


def find_pr_template(repo_root: Path) -> str | None:
    """The repo's PR template text (truncated), or None. A symlinked template is skipped: it
    could point anywhere, and the text is posted to GitHub verbatim."""
    for candidate in _TEMPLATE_CANDIDATES:
        path = repo_root / candidate
        if path.is_symlink() or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        return text[:_TEMPLATE_LIMIT]
    return None


def read_open_issues(run_dir: Path) -> list[dict]:
    """The run's open issues from open_issues.json; [] when it's missing or unreadable."""
    path = run_dir / "open_issues.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [issue for issue in data if isinstance(issue, dict)]


def blocking_issues(run_dir: Path) -> list[dict]:
    """The run's open blocker and major issues (deduped), the ones that make it `incomplete`."""
    return [issue for issue in dedupe_issues(read_open_issues(run_dir)) if blocking_count([issue])]


def _unconfirmed_assumptions(run_dir: Path, newest_review: dict | None) -> list[str]:
    if newest_review is not None:
        resolutions = newest_review.get("assumption_resolutions", [])
        return [
            clean_note(resolution)
            for resolution in resolutions
            if not resolution.strip().lower().startswith("confirmed")
        ]
    entries = ArtifactStore(run_dir).read_assumptions()
    return [clean_note(entry["assumption"]) for entry in entries if entry.get("status") == "open"]


def _action_lines(issues: list[dict], run_dir: Path, newest_review: dict | None) -> list[str]:
    lines = [issue_line(issue) for issue in issues]
    lines += [f"- Assumption not confirmed: {text}" for text in _unconfirmed_assumptions(run_dir, newest_review)]
    return lines


def _tests_line(issues: list[dict]) -> str:
    still_failing = sum(1 for issue in issues if issue["note"].startswith("still failing"))
    if still_failing:
        return f"- Tests: {still_failing} still failing (see Action needed)"
    return "- Tests: no new failures against the base"


def render_pr_body(
    *, run_id: str, run_dir: Path, totals: Totals | None, template: str | None, forced: bool = False
) -> str:
    """`forced`: an `incomplete` run published anyway; its open blocker and major issues head the body."""
    plan = ArtifactStore(run_dir).read_plan()
    newest_review = newest_output(run_dir, "review")
    issues = dedupe_issues(read_open_issues(run_dir))

    action_lines = _action_lines(issues, run_dir, newest_review)

    sections: list[str] = []
    if forced:
        blocking = [issue_line(issue) for issue in blocking_issues(run_dir)]
        sections += ["## Open issues (published with --force)", "\n".join(blocking) or "None recorded.", ""]
    sections += [
        "## Action needed",
        "\n".join(action_lines) if action_lines else "None.",
        "",
        "## What changed",
        plan.description,
        "",
        *task_lines(plan),
        "",
        "## How it was verified",
        _tests_line(issues),
    ]

    tester_reports = output_count(run_dir, "tester")
    if tester_reports:
        report_word = "report" if tester_reports == 1 else "reports"
        sections.append(f"- Tester: {tester_reports} {report_word}")

    if newest_review is not None:
        sections.append(f"- Reviewer: {newest_review.get('verdict')}")
    else:
        sections.append("- Reviewer: not run")

    if totals is not None:
        usage_line = f"Total: {totals.tokens:,} tokens · {format_cost(totals.cost_usd, totals.cost_source)}"
        sections += ["", "## Usage", usage_line]

    if template is not None:
        sections += ["", "## Template", template]

    sections += ["", "---", f"Opened by Phil from run {run_id}."]

    return "\n".join(sections) + "\n"
