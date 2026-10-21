"""What the built wheel and sdist contain (spec 2026-10-18 §3.3-3.4 and §6.1). The package is
built once per module, with the same `uv build` the release workflow runs."""

import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

from tests.release import ROOT, release_check

SDIST_TOP = {"src", "tests", "README.md", "LICENSE", "CHANGELOG.md", "pyproject.toml", "uv.lock"}


@pytest.fixture(scope="module")
def dist(tmp_path_factory) -> Path:
    uv = shutil.which("uv")
    assert uv, "uv must be on PATH to build the package"
    out = tmp_path_factory.mktemp("dist")
    subprocess.run([uv, "build", "--sdist", "--wheel", "--out-dir", str(out), str(ROOT)],
                   check=True, capture_output=True, text=True)
    return out


def _wheel(dist: Path) -> zipfile.ZipFile:
    return zipfile.ZipFile(next(dist.glob("phil_agent-*-py3-none-any.whl")))


def _sdist(dist: Path) -> Path:
    return next(dist.glob("phil_agent-*.tar.gz"))


def test_the_wheel_ships_every_prompt(dist):
    names = set(_wheel(dist).namelist())
    prompts = sorted(p.name for p in (ROOT / "src" / "phil" / "prompts").glob("*.md"))
    assert prompts
    assert [p for p in prompts if f"phil/prompts/{p}" not in names] == []


def test_the_wheel_installs_the_phil_command(dist):
    wheel = _wheel(dist)
    entry_points = next(n for n in wheel.namelist() if n.endswith(".dist-info/entry_points.txt"))
    assert "phil = phil.cli.entry:main" in wheel.read(entry_points).decode()


def test_the_wheel_holds_only_the_phil_package(dist):
    tops = {name.split("/")[0] for name in _wheel(dist).namelist()}
    assert {t for t in tops if not t.endswith(".dist-info")} == {"phil"}


def test_the_sdist_holds_only_the_include_list(dist):
    with tarfile.open(_sdist(dist)) as sdist:
        names = sdist.getnames()
    tops = {parts[1] for parts in (n.split("/") for n in names) if len(parts) > 1 and parts[1]}
    # Hatchling adds PKG-INFO, and always ships .gitignore so a wheel built from the sdist
    # leaves out the same files.
    assert tops - {"PKG-INFO", ".gitignore"} == SDIST_TOP
    assert [n for n in names if "__pycache__" in n or n.endswith(".pyc")] == []


def test_the_sdist_is_under_the_size_limit(dist):
    assert release_check.sdist_ok(_sdist(dist)), _sdist(dist).stat().st_size
