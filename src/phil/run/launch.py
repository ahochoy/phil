import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from phil.contracts import Plan
from phil.repo import RepoInfo, resolve_repo
from phil.store.artifacts import ArtifactStore
from phil.store.db import connect
from phil.store.paths import ProjectPaths
from phil.store.runs import RunRecord, create_run, new_run_id


def prepare_run(info: RepoInfo, plan: Plan, base_sha: str) -> RunRecord:
    paths = ProjectPaths(info.slug)
    conn = connect(paths.db_path)
    try:
        run_id = new_run_id(conn)
        ArtifactStore(paths.run_dir(run_id)).write_plan(plan)
        return create_run(
            conn,
            run_id=run_id,
            keyword=plan.keyword,
            base_sha=base_sha,
            worktree=paths.worktree_dir(run_id),
            tasks_total=len(plan.tasks),
            story_ref=plan.story_ref,
        )
    finally:
        conn.close()


def worker_command(repo_root: Path, run_id: str, mode: str, decision: dict | None = None) -> list[str]:
    # -P: cwd is the target repo, so without it Python would auto-prepend cwd to sys.path for
    # this -m invocation, letting the repo's own top-level packages shadow phil's modules.
    command = [sys.executable, "-P", "-m", "phil", "--repo", str(repo_root), "_worker", run_id, "--mode", mode]
    if decision is not None:
        command += ["--decision", json.dumps(decision)]
    return command


def spawn_worker(
    repo_root: Path, run_id: str, mode: str, decision: dict | None = None, *, env: dict | None = None
) -> subprocess.Popen:
    info = resolve_repo(repo_root)
    log_path = ProjectPaths(info.slug).run_dir(run_id) / "logs" / "worker.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as log:
        return subprocess.Popen(
            worker_command(info.root, run_id, mode, decision),
            cwd=info.root,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=env,
        )


def is_worker_alive(record: RunRecord, *, stale_after_s: float = 30.0) -> bool:
    if record.pid is None:
        return False
    try:
        os.kill(record.pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    if record.heartbeat_at is None:
        return True
    age = (datetime.now(UTC) - datetime.fromisoformat(record.heartbeat_at)).total_seconds()
    return age <= stale_after_s
