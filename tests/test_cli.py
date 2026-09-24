import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner

from phil import __version__
from phil.cli.main import app
from phil.contracts import ALL_CONTRACTS, Ref
from phil.repo import resolve_repo
from phil.store.db import connect
from phil.store.parked import park
from phil.store.paths import ProjectPaths
from phil.store.runs import create_run, update_run

runner = CliRunner()


def test_version_flag_prints_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def _conn_for(repo: Path):
    return connect(ProjectPaths(resolve_repo(repo).slug).db_path)


def test_runs_empty(git_repo):
    result = runner.invoke(app, ["--repo", str(git_repo), "runs"])
    assert result.exit_code == 0
    assert "No runs yet" in result.output


def test_runs_lists_runs(git_repo):
    create_run(
        _conn_for(git_repo), run_id="r-7f3a", keyword="MAPS", base_sha="abc", worktree=Path("/wt"), tasks_total=5
    )
    result = runner.invoke(app, ["--repo", str(git_repo), "runs"])
    assert result.exit_code == 0
    assert "r-7f3a" in result.output
    assert "MAPS" in result.output
    assert "0/5" in result.output


def test_runs_escapes_state_markup(git_repo):
    create_run(
        _conn_for(git_repo), run_id="r-7f3a", keyword="MAPS", base_sha="abc", worktree=Path("/wt"), tasks_total=5
    )
    update_run(_conn_for(git_repo), "r-7f3a", state="[bold]running[/]")
    result = runner.invoke(app, ["--repo", str(git_repo), "runs"])
    assert result.exit_code == 0
    assert "[bold]running[/]" in result.output


def test_runs_outside_repo_fails(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    result = runner.invoke(app, ["--repo", str(plain), "runs"])
    assert result.exit_code == 1
    assert "Not inside a git repository" in result.output


def test_parked_lists_open_items(git_repo):
    park(
        _conn_for(git_repo),
        raised_by="reviewer",
        note="N+1 query in listings",
        why_not_now="out of scope",
        source=Ref(label="review", path="x"),
    )
    result = runner.invoke(app, ["--repo", str(git_repo), "parked"])
    assert result.exit_code == 0
    assert "P-001" in result.output
    assert "N+1 query" in result.output


def test_parked_preserves_square_brackets(git_repo):
    park(
        _conn_for(git_repo),
        raised_by="reviewer",
        note="fix list[str] typing",
        why_not_now="see [MAPS-002]",
        source=Ref(label="review", path="x"),
    )
    result = runner.invoke(app, ["--repo", str(git_repo), "parked"])
    assert result.exit_code == 0
    assert "list[str]" in result.output
    assert "[MAPS-002]" in result.output


def test_parked_empty(git_repo):
    result = runner.invoke(app, ["--repo", str(git_repo), "parked"])
    assert result.exit_code == 0
    assert "Parking lot is empty" in result.output


def test_schema_command_exports_all(tmp_path):
    out = tmp_path / "schemas"
    result = runner.invoke(app, ["schema", "--out", str(out)])
    assert result.exit_code == 0
    assert len(list(out.glob("*.schema.json"))) == len(ALL_CONTRACTS)


def test_cli_import_does_not_load_llm_stack():
    code = (
        "import sys, phil.cli.main; "
        "heavy = ('langchain', 'langgraph', 'deepagents', 'langchain_openrouter'); "
        "print(','.join(m for m in heavy if m in sys.modules))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == ""
