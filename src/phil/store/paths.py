import os
from dataclasses import dataclass, field
from pathlib import Path


def phil_home() -> Path:
    return Path(os.environ.get("PHIL_HOME", Path.home() / ".phil"))


def global_policy_path() -> Path:
    """The permission rules that apply in every project."""
    return phil_home() / "policy.toml"


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
    def policy_path(self) -> Path:
        """This project's permission rules (spec §3.1): `policy.toml` next to its database."""
        return self.project_dir / "policy.toml"

    @property
    def runs_dir(self) -> Path:
        return self.project_dir / "runs"

    @property
    def worktrees_dir(self) -> Path:
        return self.project_dir / "worktrees"

    def run_dir(self, run_id: str) -> Path:
        return self.runs_dir / run_id

    def stop_request(self, run_id: str) -> Path:
        """The file `phil stop` writes on Windows to ask a run's worker to stop (see phil.platform)."""
        return self.run_dir(run_id) / "stop-requested"

    def worktree_dir(self, run_id: str) -> Path:
        return self.worktrees_dir / run_id
