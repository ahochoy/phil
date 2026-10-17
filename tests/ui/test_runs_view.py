from pathlib import Path

from phil.store.db import connect
from phil.store.paths import ProjectPaths
from phil.store.runs import create_run
from phil.ui.runs_view import render_runs
from phil.ui.theme import make_console


def test_phil_runs_lists_the_name_and_the_id_and_an_old_run_by_its_id(phil_home):
    conn = connect(ProjectPaths("demo-12345678").db_path)
    create_run(conn, run_id="r-0001", keyword="CALC", base_sha="abc", worktree=Path("/wt"), tasks_total=1,
               name="subtract-calc")
    create_run(conn, run_id="r-0002", keyword="CALC", base_sha="abc", worktree=Path("/wt"), tasks_total=1)
    console = make_console(record=True, width=160)
    render_runs(console, conn)
    text = console.export_text()
    assert "subtract-calc (r-0001)" in text
    assert "r-0002" in text and "None" not in text
