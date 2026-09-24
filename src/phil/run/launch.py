from phil.contracts import Plan
from phil.repo import RepoInfo
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
