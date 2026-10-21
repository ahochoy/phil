"""README.md is also the PyPI project page, where relative links break (spec 2026-10-18 §4.1)."""

import re

from tests.release import ROOT

README = (ROOT / "README.md").read_text(encoding="utf-8")
_TARGETS = re.compile(r"\]\(([^)\s]+)[^)]*\)|\b(?:src|href)=\"([^\"]+)\"")


def test_every_readme_link_and_image_is_absolute():
    targets = [a or b for a, b in _TARGETS.findall(README)]
    assert targets, "the README should link to the guide and the changelog"
    relative = [t for t in targets if not t.startswith(("https://", "http://", "#", "mailto:"))]
    assert relative == []


def test_the_readme_installs_phil_agent():
    assert "uv tool install phil-agent" in README
    assert "pipx install phil-agent" in README
