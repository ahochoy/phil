"""Labelling skills right after setup has written the config, so the first chat already knows them."""

import logging
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

from phil.agents.invoke import AgentContext, AgentFactory
from phil.config import load_config
from phil.repo import RepoError, resolve_repo
from phil.skills.labels import label_skills
from phil.skills.runtime import load_runtime
from phil.store.artifacts import ArtifactStore
from phil.store.db import connect
from phil.store.paths import ProjectPaths

logger = logging.getLogger(__name__)


def label_after_setup(
    root: Path,
    overrides: Sequence[str] = (),
    *,
    factory: AgentFactory | None = None,
    labeller: Callable[..., int] = label_skills,
) -> tuple[int, int]:
    """Label the approved skills that have no label yet, with the config setup just wrote.
    Returns (how many needed a label, how many got one). Raises if anything fails."""
    config = load_config(root, overrides=list(overrides))
    try:
        info = resolve_repo(root)
        repo_root, paths = info.root, ProjectPaths(info.slug)
    except RepoError:
        repo_root, paths = None, None
    runtime = load_runtime(config, repo_root, paths)
    if runtime is None:
        return 0, 0
    pending = [
        skill
        for skill in runtime.catalog.skills
        if skill.name not in config.skills.stages and runtime.cache.get(skill.content_hash) is None
    ]
    if not pending:
        return 0, 0
    with tempfile.TemporaryDirectory(prefix="phil-skill-labels-") as scratch:
        conn = connect(Path(scratch) / "labels.db")
        try:
            ctx = AgentContext(
                config=config,
                conn=conn,
                layer="chat",
                artifacts=ArtifactStore(Path(scratch) / "artifacts"),
                workdir=root,
                factory=factory,
                skills=runtime,
            )
            labelled = labeller(ctx, pending, runtime.cache)
        finally:
            conn.close()
    return len(pending), labelled
