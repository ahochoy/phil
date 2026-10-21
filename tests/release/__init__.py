"""Tests for packaging and the release pipeline (spec 2026-10-18). `.github/scripts` isn't a package,
so the release script is loaded from its path once, here, for every test in this directory."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = ROOT / ".github" / "scripts" / "release_check.py"

_spec = importlib.util.spec_from_file_location("release_check", _SCRIPT)
release_check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release_check)
