"""Invariants of the release pipeline that actionlint can't check (spec 2026-10-18 §5)."""

import re

from tests.release import ROOT

WORKFLOWS = ROOT / ".github" / "workflows"
RELEASE = (WORKFLOWS / "release.yml").read_text(encoding="utf-8")
TESTS = (WORKFLOWS / "tests.yml").read_text(encoding="utf-8")


def test_only_the_publish_job_can_mint_an_oidc_token():
    assert RELEASE.count("id-token: write") == 1
    publish = RELEASE.split("\n  publish:\n", 1)[1].split("\n  verify:\n", 1)[0]
    assert "id-token: write" in publish


def test_the_release_reuses_the_test_suite():
    assert "workflow_call:" in TESTS
    assert "uses: ./.github/workflows/tests.yml" in RELEASE


def test_a_called_test_run_never_cancels_the_main_branch_run():
    # A manual release on main and a push to main share github.ref; the group must also include
    # the calling workflow's name, or one run cancels the other.
    assert "group: tests-${{ github.workflow }}-${{ github.ref }}" in TESTS


def test_version_checks_ignore_a_windows_carriage_return():
    # On windows-latest, Python writes "0.1.0\r\n" to the pipe; the comparison must not see the \r.
    assert '"$(phil --version)"' not in RELEASE
    assert RELEASE.count("\"$(phil --version | tr -d '\\r')\"") == 2


def test_a_rerun_skips_files_already_uploaded():
    # A publish that uploaded the wheel but not the sdist must be re-runnable without a new version.
    publishes = [line for line in RELEASE.splitlines() if "uv publish" in line]
    assert len(publishes) == 2
    assert "--check-url https://pypi.org/simple/" in publishes[0]
    assert "--check-url https://test.pypi.org/simple/" in publishes[1]


def test_every_action_is_pinned_to_a_commit():
    uses = re.findall(r"uses: ([^\s]+)", RELEASE)
    external = [u for u in uses if not u.startswith("./")]
    assert external
    assert [u for u in external if not re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", u)] == []
