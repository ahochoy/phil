import subprocess

from phil.routing import route_state


def test_route_state_trims_chat_and_lists_files(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for i in range(70):
        (tmp_path / f"f{i:02d}.py").write_text("x = 1\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    chat = [f"turn {i} " + "y" * 500 for i in range(6)]
    state = route_state("fix it", chat, tmp_path)
    assert state["request"] == "fix it"
    assert len(state["chat"]) == 4 and all(len(turn) <= 400 for turn in state["chat"])
    assert state["chat"][0].startswith("turn 2")  # the last four turns
    assert state["repo"]["test_cmd"] == "pytest"
    assert len(state["repo"]["files"]) == 60 and state["repo"]["file_count"] == 72


def test_route_state_outside_git_has_no_files(tmp_path):
    state = route_state("hi", [], tmp_path)
    assert state["repo"] == {"test_cmd": None, "files": [], "file_count": 0}
