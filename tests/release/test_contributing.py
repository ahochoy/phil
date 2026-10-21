"""CONTRIBUTING's commands run top to bottom (spec 2026-10-18 §4.3)."""

from tests.release import ROOT

CONTRIBUTING = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")


def test_the_old_phil_tool_is_removed_before_the_editable_install():
    # Uninstalling the old `phil` tool after `--force` installed phil-agent over its executable
    # would delete the shared `phil` command.
    assert CONTRIBUTING.index("uv tool uninstall phil") < CONTRIBUTING.index("uv tool install --editable")
