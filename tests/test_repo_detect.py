import json

import pytest

from phil.repo_detect import detect_test_cmd


def write(root, name, text=""):
    (root / name).write_text(text)


def test_package_json_with_a_test_script_gives_npm_test(tmp_path):
    write(tmp_path, "package.json", json.dumps({"scripts": {"test": "vitest run"}}))
    assert detect_test_cmd(tmp_path) == "npm test"


def test_package_json_without_a_test_script_gives_none(tmp_path):
    write(tmp_path, "package.json", json.dumps({"scripts": {"build": "vite build"}}))
    assert detect_test_cmd(tmp_path) is None


def test_npm_init_placeholder_test_script_counts_as_none(tmp_path):
    write(tmp_path, "package.json", json.dumps({"scripts": {"test": 'echo "Error: no test specified" && exit 1'}}))
    assert detect_test_cmd(tmp_path) is None


def test_unreadable_package_json_is_ignored(tmp_path):
    write(tmp_path, "package.json", "{not json")
    assert detect_test_cmd(tmp_path) is None


@pytest.mark.parametrize("marker", ["pyproject.toml", "pytest.ini", "conftest.py"])
def test_python_markers_give_pytest(tmp_path, marker):
    write(tmp_path, marker)
    assert detect_test_cmd(tmp_path) == "pytest"


def test_python_with_uv_lock_gives_uv_run_pytest(tmp_path):
    write(tmp_path, "pyproject.toml")
    write(tmp_path, "uv.lock")
    assert detect_test_cmd(tmp_path) == "uv run pytest"


def test_go_mod_gives_go_test(tmp_path):
    write(tmp_path, "go.mod", "module example.com/x\n")
    assert detect_test_cmd(tmp_path) == "go test ./..."


def test_cargo_toml_gives_cargo_test(tmp_path):
    write(tmp_path, "Cargo.toml", "[package]\n")
    assert detect_test_cmd(tmp_path) == "cargo test"


def test_nothing_recognised_gives_none(tmp_path):
    write(tmp_path, "index.html", "<p>hi</p>")
    assert detect_test_cmd(tmp_path) is None


def test_a_missing_root_gives_none(tmp_path):
    assert detect_test_cmd(tmp_path / "missing") is None


def test_package_json_with_a_test_script_wins_over_python(tmp_path):
    write(tmp_path, "package.json", json.dumps({"scripts": {"test": "jest"}}))
    write(tmp_path, "pyproject.toml")
    assert detect_test_cmd(tmp_path) == "npm test"


def test_python_wins_when_package_json_has_no_test_script(tmp_path):
    write(tmp_path, "package.json", json.dumps({"scripts": {"build": "vite build"}}))
    write(tmp_path, "pyproject.toml")
    write(tmp_path, "uv.lock")
    assert detect_test_cmd(tmp_path) == "uv run pytest"


def test_python_wins_over_go_and_cargo(tmp_path):
    write(tmp_path, "conftest.py")
    write(tmp_path, "go.mod")
    write(tmp_path, "Cargo.toml")
    assert detect_test_cmd(tmp_path) == "pytest"


def test_go_wins_over_cargo(tmp_path):
    write(tmp_path, "go.mod")
    write(tmp_path, "Cargo.toml")
    assert detect_test_cmd(tmp_path) == "go test ./..."


def test_a_real_test_script_that_mentions_the_placeholder_words_still_counts(tmp_path):
    write(tmp_path, "package.json", json.dumps({"scripts": {"test": "node check.js --why 'no test specified yet'"}}))
    assert detect_test_cmd(tmp_path) == "npm test"
