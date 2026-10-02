import json
import re
import sqlite3
from pathlib import Path

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from phil.contracts import Ref
from phil.run.state import issue_line, task_lines
from phil.store.artifacts import ArtifactStore
from phil.store.paths import ProjectPaths
from phil.store.runs import get_run
from phil.store.telemetry import UsageLine, format_cost, goal_chat_usage, usage_by_role, weakest

_MAX_PER_CATEGORY = 5
_MAX_DETAIL_LINES = 2000
MAX_DETAIL_CHARS = 200_000

# C0 controls except tab and newline, DEL, and the C1 controls (U+0080–U+009F, e.g. U+009B, a
# one-character CSI): printed raw they drive the terminal (clear it, retitle it, move the cursor).
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def detail_text(path: Path) -> str:
    """A detail file as plain text for `phil show RUN N` and the chat's `/more N`: JSON is
    pretty-printed, terminal control characters are dropped, and anything past 2000 lines or
    200,000 characters is cut with a note."""
    text = path.read_text()
    if path.suffix == ".json":
        try:
            text = json.dumps(json.loads(text), indent=2, default=repr)
        except json.JSONDecodeError:
            pass
    text = _CONTROL.sub("", text)
    lines = text.splitlines()
    if len(lines) > _MAX_DETAIL_LINES:
        text = "\n".join(lines[:_MAX_DETAIL_LINES]) + f"\n… ({len(lines) - _MAX_DETAIL_LINES} more lines not shown)"
    if len(text) > MAX_DETAIL_CHARS:
        text = text[:MAX_DETAIL_CHARS] + f"\n… ({len(text) - MAX_DETAIL_CHARS} more characters not shown)"
    return text


def _newest_first(paths: list[Path]) -> list[Path]:
    """Newest first; a file deleted between the directory listing and its `stat()` is skipped."""
    stamped: list[tuple[float, str, Path]] = []
    for path in paths:
        try:
            stamped.append((path.stat().st_mtime, path.name, path))
        except OSError:
            continue
    return [path for _, _, path in sorted(stamped, reverse=True)]


def show_refs(paths: ProjectPaths, run_id: str) -> list[Ref]:
    """Numbered detail refs for `phil show <run>`: summary, worker log, test logs (newest
    first, up to 5), reviewer/tester outputs (newest first, up to 5), packets (newest first,
    up to 5) — each included only if the file exists."""
    run_dir = paths.run_dir(run_id)
    refs: list[Ref] = []

    summary = run_dir / "summary.md"
    if summary.exists():
        refs.append(Ref(label="summary", path=str(summary)))

    worker_log = run_dir / "logs" / "worker.log"
    if worker_log.exists():
        refs.append(Ref(label="worker log", path=str(worker_log)))

    logs_dir = run_dir / "logs"
    if logs_dir.exists():
        test_logs = [p for p in logs_dir.glob("*.log") if p.name != "worker.log"]
        for path in _newest_first(test_logs)[:_MAX_PER_CATEGORY]:
            refs.append(Ref(label=f"test log {path.stem}", path=str(path)))

    outputs_dir = run_dir / "outputs"
    if outputs_dir.exists():
        outputs = [p for p in outputs_dir.glob("*.json") if "review" in p.name or "tester" in p.name]
        for path in _newest_first(outputs)[:_MAX_PER_CATEGORY]:
            refs.append(Ref(label=f"output {path.stem}", path=str(path)))

    packets_dir = run_dir / "packets"
    if packets_dir.exists():
        packets = list(packets_dir.glob("*.json"))
        for path in _newest_first(packets)[:_MAX_PER_CATEGORY]:
            refs.append(Ref(label=f"packet {path.stem}", path=str(path)))

    return refs


def _tasks_section(paths: ProjectPaths, run_id: str) -> list[str]:
    run_dir = paths.run_dir(run_id)
    summary = run_dir / "summary.md"
    if summary.exists():
        lines = summary.read_text().splitlines()
        try:
            start = lines.index("## Tasks") + 1
        except ValueError:
            return []
        end = start
        while end < len(lines) and not lines[end].startswith("## "):
            end += 1
        return [line for line in lines[start:end] if line.strip()]
    plan_path = run_dir / "plan.json"
    if plan_path.exists():
        return task_lines(ArtifactStore(run_dir).read_plan())
    return []


def _open_issues(paths: ProjectPaths, run_id: str) -> list[dict] | None:
    path = paths.run_dir(run_id) / "open_issues.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


def _usage_table(usage: list[UsageLine]) -> Table:
    table = Table(box=None, pad_edge=False)
    for column in ("layer", "role", "calls", "model calls", "in", "out", "cost", "tools", "retries"):
        table.add_column(column, style="phil.muted", no_wrap=True)
    for line in usage:
        tools = ", ".join(f"{name}×{count}" for name, count in line.tool_calls.items())
        table.add_row(
            escape(line.layer),
            escape(line.role),
            str(line.calls),
            str(line.model_calls),
            f"{line.input_tokens:,}",
            f"{line.output_tokens:,}",
            escape(format_cost(line.cost_usd, line.cost_source)),
            escape(tools),
            str(line.retries),
        )
    return table


def render_show(console: Console, conn: sqlite3.Connection, paths: ProjectPaths, run_id: str) -> list[Ref]:
    """Print a run's header, tasks, usage table, open issues and numbered detail refs; return
    the refs (so the caller can print one by number)."""
    record = get_run(conn, run_id)
    assert record is not None
    depth_segment = f" · {escape(record.depth)}" if record.depth else ""
    console.print(
        f"Run [phil.id]{escape(run_id)}[/] · {escape(record.keyword)}{depth_segment} · {escape(record.state)} · "
        f"{escape(record.base_sha[:8])}..{escape(record.branch)} · {record.tasks_done}/{record.tasks_total} tasks"
    )

    console.print("")
    console.print("[bold]Tasks[/]")
    tasks = _tasks_section(paths, run_id)
    if tasks:
        for line in tasks:
            console.print(escape(line))
    else:
        console.print("[phil.muted]No tasks recorded.[/]")

    console.print("")
    console.print("[bold]Usage[/]")
    # The chat's own calls for the goal (routing, intake, planning) come first, then the run's.
    usage = [*goal_chat_usage(conn, run_id), *usage_by_role(conn, run_id)]
    if usage:
        console.print(_usage_table(usage))
        tokens = sum(line.input_tokens + line.output_tokens for line in usage)
        cost = format_cost(sum(line.cost_usd for line in usage), weakest([line.cost_source for line in usage]))
        console.print(f"Total: {tokens:,} tokens · {escape(cost)}")
    else:
        console.print("[phil.muted]No usage recorded.[/]")

    console.print("")
    console.print("[bold]Open issues[/]")
    issues = _open_issues(paths, run_id)
    if issues is None:
        console.print("[phil.muted]none recorded[/]")
    elif not issues:
        console.print("[phil.muted](none)[/]")
    else:
        for issue in issues:
            console.print(escape(issue_line(issue)))

    console.print("")
    console.print("[bold]Details[/]")
    refs = show_refs(paths, run_id)
    if refs:
        for number, ref in enumerate(refs, start=1):
            console.print(f"[phil.id]{number}[/] {escape(ref.label)}")
    else:
        console.print("[phil.muted]No details recorded.[/]")

    return refs
