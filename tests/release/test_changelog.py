"""Every version has release notes before it ships (spec 2026-10-18 §3.5). The release workflow
checks this too; this catches it on the pull request that bumps the version."""

from tests.release import ROOT, release_check


def test_the_changelog_has_a_section_for_the_current_version():
    version = release_check.project_version((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert release_check.changelog_section(changelog, version), f"CHANGELOG.md needs a '## [{version}]' section"
