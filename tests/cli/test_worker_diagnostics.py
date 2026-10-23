import logging
from types import SimpleNamespace

from typer.testing import CliRunner

import phil.run.worker as worker_module
from phil.cli.main import _worker_log, app
from phil.repo import resolve_repo
from phil.store.paths import ProjectPaths

runner = CliRunner()


def test_a_worker_logs_into_its_runs_worker_log(git_repo, monkeypatch):
    def fake_run_worker(repo_root, run_id, mode, decision=None, **kwargs):
        logging.getLogger("httpx").warning("a library log line in the worker")
        return SimpleNamespace(status="done")

    monkeypatch.setattr(worker_module, "run_worker", fake_run_worker)
    result = runner.invoke(app, ["--repo", str(git_repo), "_worker", "r-test", "--mode", "start"])
    assert result.exit_code == 0, result.output
    log = ProjectPaths(resolve_repo(git_repo).slug).run_dir("r-test") / "logs" / "worker.log"
    assert "a library log line in the worker" in log.read_text(encoding="utf-8")
    assert "a library log line" not in result.output


def test_the_worker_log_outside_a_repository_is_none(tmp_path):
    assert _worker_log(tmp_path, "r-test") is None
