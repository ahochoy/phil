"""Phil's version has one source: pyproject.toml, read back through the installed distribution's
metadata (spec 2026-10-18 §3.2)."""

import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import phil

ROOT = Path(__file__).resolve().parent.parent


def test_the_version_is_the_installed_distributions():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["name"] == "phil-agent"
    assert phil.__version__ == version("phil-agent") == project["version"]


def test_the_version_falls_back_when_phil_is_not_installed(monkeypatch):
    def missing(name):
        raise PackageNotFoundError(name)

    monkeypatch.setattr(phil, "_dist_version", missing)
    assert phil._installed_version() == "0+unknown"
