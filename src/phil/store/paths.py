import os
from dataclasses import dataclass, field
from pathlib import Path


def phil_home() -> Path:
    return Path(os.environ.get("PHIL_HOME", Path.home() / ".phil"))


@dataclass(frozen=True)
class ProjectPaths:
    slug: str
    home: Path = field(default_factory=phil_home)

    @property
    def project_dir(self) -> Path:
        return self.home / "projects" / self.slug

    @property
    def db_path(self) -> Path:
        return self.project_dir / "phil.db"

    @property
    def runs_dir(self) -> Path:
        return self.project_dir / "runs"

    @property
    def worktrees_dir(self) -> Path:
        return self.project_dir / "worktrees"

    def run_dir(self, run_id: str) -> Path:
        return self.runs_dir / run_id

    def worktree_dir(self, run_id: str) -> Path:
        return self.worktrees_dir / run_id
