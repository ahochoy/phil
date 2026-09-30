"""Run one benchmark case through Phil's real code paths: the chat's planner, then a foreground worker.

`run_case` copies a fixture into a fresh git repo, plans the goal with the real `Planner`, starts the
run with `prepare_run` and drives it with `run_worker` (as the chat's spawned worker would), checks the
result on the run's worktree, and appends one JSON record to the results file.

Planning starts at the architect: intake is not run, so the case's goal goes to the architect as the
objective (a benchmark goal has no open questions to ask). The architect reads an exported snapshot of
the base commit, as in the chat, and the chat's start gate (`launch_problems`) is applied before the run.
"""

import json
import secrets
import shutil
import subprocess
import time
import tomllib
from datetime import UTC, datetime
from pathlib import Path

from phil.agents.invoke import AgentContext, AgentFactory
from phil.chat.approval import launch_problems
from phil.chat.overview import repo_overview
from phil.chat.planning import Planner
from phil.chat.snapshot import export_tree
from phil.config import load_config
from phil.contracts import Goal, Plan
from phil.repo import resolve_repo
from phil.run.launch import prepare_run
from phil.run.worker import run_worker
from phil.store.artifacts import ArtifactStore
from phil.store.db import connect
from phil.store.paths import ProjectPaths
from phil.store.runs import get_run
from phil.store.telemetry import chat_usage
from phil.tomlw import dump_toml
from tests.live.bench.cases import FIXTURES, Case
from tests.live.bench.report import results_path

PHIL_REPO = Path(__file__).resolve().parents[3]

# Generous run limits so the benchmark measures what a goal costs instead of stopping at the
# default guard; the benchmark config can still override them.
BASELINE: dict = {"run": {"max_tokens": 5_000_000, "max_cost_usd": 10.0}}


def phil_sha(repo: Path = PHIL_REPO) -> str:
    """The short HEAD sha of the Phil checkout being measured, or "unknown" outside a git repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo, capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    sha = result.stdout.strip()
    return sha if result.returncode == 0 and sha else "unknown"


def deep_merge(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def case_config(case: Case, user_config: dict) -> dict:
    """The run's phil.toml: the baseline, then the benchmark config, then the case's own overrides."""
    return deep_merge(deep_merge(BASELINE, user_config), case.config)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _init_repo(case: Case, work: Path, config: dict) -> Path:
    root = work / case.name
    if root.exists():
        shutil.rmtree(root)
    shutil.copytree(
        FIXTURES / case.fixture, root,
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", "dist", "node_modules"),
    )
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Phil Bench")
    _git(root, "config", "user.email", "bench@example.com")
    _git(root, "config", "commit.gpgsign", "false")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "init")
    # A committed phil.toml, as in a configured project, so the tree the run starts from is clean.
    (root / "phil.toml").write_text(dump_toml(config))
    _git(root, "add", "phil.toml")
    _git(root, "commit", "-m", "Configure phil")
    return root.resolve()


def _usage(conn, chat_id: str, totals) -> dict:
    # The same rows chat_usage totals: the chat's own calls and those of every run it started.
    row = conn.execute(
        "SELECT COUNT(*) AS calls, COALESCE(SUM(input_tokens), 0) AS tokens_in,"
        " COALESCE(SUM(output_tokens), 0) AS tokens_out, COALESCE(SUM(model_calls), 0) AS model_calls,"
        " COALESCE(SUM(retries), 0) AS retries FROM telemetry"
        " WHERE (layer = 'chat' AND chat_id = ?) OR run_id IN (SELECT run_id FROM runs WHERE chat_id = ?)",
        (chat_id, chat_id),
    ).fetchone()
    return {
        "calls": int(row["calls"]),
        "tokens_in": int(row["tokens_in"]),
        "tokens_out": int(row["tokens_out"]),
        "cost_usd": totals.cost_usd,
        "cost_source": totals.cost_source,
        "model_calls": int(row["model_calls"]),
        "retries": int(row["retries"]),
    }


def _modes(plan: Plan | None) -> list[str]:
    # Tasks gain a `verify` mode in a later task; until then every task is test-first.
    return [getattr(task, "verify", None) or "tdd" for task in plan.tasks] if plan else []


def append_record(record: dict) -> None:
    path = results_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as out:
        out.write(json.dumps(record) + "\n")


def run_case(case: Case, config_path: Path, work: Path, *, factory: AgentFactory | None = None) -> dict:
    """Plan and run `case` end to end, then append and return its record. `factory=None` uses real models."""
    config_data = case_config(case, tomllib.loads(Path(config_path).read_text()))
    root = _init_repo(case, Path(work), config_data)
    info = resolve_repo(root)
    paths = ProjectPaths(info.slug)
    chat_id = f"bench-{case.name}-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(2)}"
    conn = connect(paths.db_path)
    chat_dir = paths.project_dir / "chats" / chat_id
    ctx = AgentContext(
        config=load_config(info.root), conn=conn, layer="chat", chat_id=chat_id,
        artifacts=ArtifactStore(chat_dir), factory=factory,
    )
    ts = datetime.now(UTC).isoformat(timespec="seconds")
    started = time.monotonic()
    plan: Plan | None = None
    run_id: str | None = None
    error: str | None = None
    refused = False
    try:
        # Like the chat: the architect reads the base commit's tracked files, never the live tree.
        tree = export_tree(info.root, info.head_sha, chat_dir / "tree" / info.head_sha[:12])
        plan = Planner(ctx, repo_overview(info.root)).draft(Goal(objective=case.goal), tree=tree).plan
        problems = launch_problems(plan, ctx.config, tree)  # detects on the snapshot, as the chat does
        if problems:  # the chat would refuse to start this run
            refused, error = True, "; ".join(problems)
        else:
            run_id = prepare_run(info, plan, info.head_sha, chat_id=chat_id).run_id
            outcome = run_worker(info.root, run_id, "start", factory=factory)
            if outcome.escalation is not None:
                error = outcome.escalation.get("error") or outcome.escalation.get("summary")
    except Exception as exc:  # the record says what went wrong; the benchmark carries on
        error = f"{type(exc).__name__}: {exc}"
    minutes = round((time.monotonic() - started) / 60, 2)
    try:
        run = get_run(conn, run_id) if run_id else None
        state = run.state if run else "launch_refused" if refused else "planning_failed"
        if error is None and state != "completed" and run is not None:
            error = run.needs_attention
        # Checked on the run's worktree before anything cleans it up. An escalated run answered nothing.
        passed = state == "completed" and run is not None and case.passed(Path(run.worktree))
        record = {
            "case": case.name,
            "ts": ts,
            "phil_sha": phil_sha(),
            "models": dict(config_data.get("models", {})),
            "tasks": len(plan.tasks) if plan else 0,
            "modes": _modes(plan),
            "expect_modes": list(case.expect_modes),
            "state": state,
            "passed": passed,
            "minutes": minutes,
            **_usage(conn, chat_id, chat_usage(conn, chat_id)),
            "run_id": run_id,
            "chat_id": chat_id,
            "error": error,
        }
    finally:
        conn.close()
    append_record(record)
    return record
