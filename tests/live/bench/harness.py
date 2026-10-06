"""Run one benchmark case through Phil's real code paths: the router, then the path it picks.

`run_case` copies a fixture into a fresh git repo, classifies and routes the goal exactly as the chat
does (`classify` then `decide`), and runs whichever path the router chose:

- **quick**: intake writes the one task (as `route_depth="quick"`), then the one-task plan and a
  foreground quick run — falling back to the architect, as the chat does, if the task doesn't make a
  valid plan or the start gate finds a problem.
- **full** (or the router leaving it to intake): the architect and critic plan the goal directly (a
  benchmark goal has no open questions to ask, so intake itself is skipped here), then a foreground run.
- **answer**: the read-only answerer, on a snapshot of the working tree; no run, no commits.

Planning reads an exported snapshot of the base commit, as in the chat, and the chat's start gate
(`launch_problems`) is applied before any run.
"""

import json
import secrets
import shutil
import subprocess
import time
import tomllib
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from phil.agents.invoke import AgentContext, AgentFactory
from phil.chat.answer import ask_answer
from phil.chat.approval import effective_test_cmd, launch_problems
from phil.chat.overview import repo_overview
from phil.chat.planning import Planner, intake, quick_plan
from phil.chat.snapshot import export_tree, export_worktree
import phil.config
from phil.config import ROLES, ConfigError, PhilConfig, load_config
from phil.contracts import Goal, Plan
from phil.repo import resolve_repo
from phil.repo_detect import detect_test_cmd
from phil.routing import decide, route_state
from phil.routing.classify import classify
from phil.run.launch import prepare_run
from phil.run.worker import run_worker
from phil.store.artifacts import ArtifactStore
from phil.store.db import connect
from phil.store.paths import ProjectPaths
from phil.store.runs import get_run
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


def _usage(conn, chat_id: str, run_id: str | None) -> dict:
    """The chat's own calls plus its run's. A run's telemetry carries no `chat_id` (phil.store.db
    SCHEMA), so `run_id` is matched directly rather than through a `runs` subselect."""
    row = conn.execute(
        "SELECT COUNT(*) AS calls, COALESCE(SUM(input_tokens), 0) AS tokens_in,"
        " COALESCE(SUM(output_tokens), 0) AS tokens_out, COALESCE(SUM(model_calls), 0) AS model_calls,"
        " COALESCE(SUM(retries), 0) AS retries, COALESCE(SUM(cost_usd), 0.0) AS cost_usd,"
        " COALESCE(MAX(CASE cost_source WHEN 'unknown' THEN 2 WHEN 'estimated' THEN 1 ELSE 0 END), 0) AS cost_rank"
        " FROM telemetry WHERE chat_id = ? OR run_id = ?",
        (chat_id, run_id),
    ).fetchone()
    return {
        "calls": int(row["calls"]),
        "tokens_in": int(row["tokens_in"]),
        "tokens_out": int(row["tokens_out"]),
        "cost_usd": round(float(row["cost_usd"]), 6),
        "cost_source": ("reported", "estimated", "unknown")[row["cost_rank"]],
        "model_calls": int(row["model_calls"]),
        "retries": int(row["retries"]),
    }


def _modes(plan: Plan | None) -> list[str]:
    # Tasks gain a `verify` mode in a later task; until then every task is test-first.
    return [getattr(task, "verify", None) or "tdd" for task in plan.tasks] if plan else []


@contextmanager
def without_global_config(work: Path) -> Iterator[None]:
    """Hide the user's ~/.phil/config.toml while a case runs, so a global [project], [shell], [git],
    [tiers] or [models] setting can't change results. The worker runs in-process, so this reaches it."""
    original = phil.config.global_config_path
    phil.config.global_config_path = lambda: Path(work) / "no-global-config.toml"  # never created
    try:
        yield
    finally:
        phil.config.global_config_path = original


def resolved_models(config: PhilConfig) -> dict[str, str]:
    """The model each role actually resolves to, skipping roles that have none."""
    models = {}
    for role in ROLES:
        try:
            models[role] = config.model_for(role)
        except ConfigError:
            continue
    return models


def append_record(record: dict) -> None:
    path = results_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as out:
        out.write(json.dumps(record) + "\n")


def _quick_plan_for(goal: Goal, config: PhilConfig, tree: Path, root: Path) -> tuple[Plan | None, list[str]]:
    """The quick run's plan, or `None` and why not: the same checks the chat's approval makes before
    a run starts (mirrors `ChatController._quick_plan`, spec §4.1, without importing the controller)."""
    if goal.task is None:
        return None, ["intake wrote no quick task"]
    draft = quick_plan(goal, None)
    if draft is None:
        return None, ["the quick task doesn't make a valid plan"]
    # Detect from the base commit's snapshot, as the chat does (`_detection_root`): skip it only when
    # the task or phil.toml already names a test command, and phil.toml also sets setup_cmd, so
    # nothing needs detecting.
    detection_root = (
        None
        if (draft.test_cmd or config.project.test_cmd) and config.project.setup_cmd is not None
        else tree
    )
    plan = quick_plan(goal, effective_test_cmd(draft, config, detection_root)[0])
    if plan is None:
        return None, ["the quick task doesn't make a valid plan"]
    problems = launch_problems(plan, config, detection_root, check_root=detection_root or root)
    return (None, problems) if problems else (plan, [])


def run_case(case: Case, config_path: Path, work: Path, *, factory: AgentFactory | None = None) -> dict:
    """Plan and run `case` end to end, then append and return its record. `factory=None` uses real models.

    The user's global config is ignored: the case's phil.toml alone decides its settings."""
    with without_global_config(work):
        return _run_case(case, config_path, work, factory=factory)


def _run_case(case: Case, config_path: Path, work: Path, *, factory: AgentFactory | None) -> dict:
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
    quick_fallback = False
    answer_files: list[str] = []
    depth: str | None = None
    try:
        judgement = classify(ctx, route_state(case.goal, [], info.root)).judgement
        depth, _ = decide(
            judgement,
            confidence_threshold=ctx.config.routing.confidence_threshold,
            detail_threshold=ctx.config.routing.detail_threshold_for(judgement.source if judgement else None),
        )
        overview = repo_overview(info.root)
        # Like the chat: the architect (and quick-plan detection) reads the base commit's tracked
        # files, never the live tree. The answer path needs no snapshot of it.
        tree: Path | None = None
        if depth != "answer":
            tree = export_tree(info.root, info.head_sha, chat_dir / "tree" / info.head_sha[:12])
        if depth == "answer":
            answer_tree = chat_dir / "tree" / "answer"
            try:
                # The answerer reads a working-tree snapshot, never the live root, as the chat does.
                snapshot = export_worktree(info.root, answer_tree)
                answer = ask_answer(ctx, case.goal, root=snapshot, overview=overview)
            finally:
                shutil.rmtree(answer_tree, ignore_errors=True)
            answer_files = answer.files
        elif depth == "quick":
            # As in the chat: the hint comes from the live root, the run itself from the snapshot
            # (a known mismatch, tracked in the M3b follow-ups).
            goal = intake(
                ctx, case.goal, overview=overview, route_depth="quick", detected_test_cmd=detect_test_cmd(info.root),
            )
            quick, problems = _quick_plan_for(goal, ctx.config, tree, info.root)
            if quick is None:
                quick_fallback = True
                plan = Planner(ctx, overview).draft(goal, tree=tree).plan
                full_problems = launch_problems(plan, ctx.config, tree)
                if full_problems:
                    refused, error = True, "; ".join(full_problems)
                else:
                    run_id = prepare_run(info, plan, info.head_sha, chat_id=chat_id, depth="full").run_id
            else:
                plan = quick
                run_id = prepare_run(info, plan, info.head_sha, chat_id=chat_id, depth="quick").run_id
        else:  # "full", or the router left it to intake: today's architect path (spec §5.2)
            plan = Planner(ctx, overview).draft(Goal(objective=case.goal), tree=tree).plan
            problems = launch_problems(plan, ctx.config, tree)  # detects on the snapshot, as the chat does
            if problems:  # the chat would refuse to start this run
                refused, error = True, "; ".join(problems)
            else:
                run_id = prepare_run(info, plan, info.head_sha, chat_id=chat_id, depth="full").run_id
        if run_id is not None:
            outcome = run_worker(info.root, run_id, "start", factory=factory)
            if outcome.escalation is not None:
                error = outcome.escalation.get("error") or outcome.escalation.get("summary")
    except Exception as exc:  # the record says what went wrong; the benchmark carries on
        error = f"{type(exc).__name__}: {exc}"
    minutes = round((time.monotonic() - started) / 60, 2)
    routed_depth = depth or "intake"
    try:
        run = get_run(conn, run_id) if run_id else None
        if run is not None:
            state = run.state
        elif refused:
            state = "launch_refused"
        elif depth == "answer":
            state = "answer_failed" if error else "answered"
        else:
            state = "planning_failed"
        if error is None and state not in ("completed", "answered") and run is not None:
            error = run.needs_attention
        if depth == "answer":
            # Checked on the repo itself: the answer path starts no run and makes no commits.
            passed = (
                error is None and case.passed(info.root)
                and (case.expect_file is None or case.expect_file in answer_files)
            )
        else:
            # Checked on the run's worktree before anything cleans it up. An escalated run answered nothing.
            passed = state == "completed" and run is not None and case.passed(Path(run.worktree))
        record = {
            "case": case.name,
            "ts": ts,
            "phil_sha": phil_sha(),
            "models": resolved_models(ctx.config),
            "tasks": len(plan.tasks) if plan else 0,
            "modes": _modes(plan),
            "expect_modes": list(case.expect_modes),
            "routed_depth": routed_depth,
            "expect_depth": case.expect_depth,
            "quick_fallback": quick_fallback,
            "files": answer_files,
            "state": state,
            "passed": passed,
            "minutes": minutes,
            **_usage(conn, chat_id, run_id),
            "run_id": run_id,
            "chat_id": chat_id,
            "error": error,
        }
    finally:
        conn.close()
    append_record(record)
    return record
