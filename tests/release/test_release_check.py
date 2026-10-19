import hashlib
import io
import json
import urllib.error

import pytest

from tests.release import ROOT, release_check as rc

PYPROJECT = (ROOT / "pyproject.toml").read_text(encoding="utf-8")


# --- tag and version


@pytest.mark.parametrize(
    ("tag", "ok"),
    [("v0.1.0", True), ("0.1.0", False), ("v0.1.1", False), ("v0.1.0rc1", False), ("vv0.1.0", False)],
)
def test_a_tag_must_be_v_plus_the_version(tag, ok):
    assert rc.tag_matches(tag, "0.1.0") is ok


def test_the_project_version_is_read_from_pyproject():
    assert rc.project_version('[project]\nname = "x"\nversion = "1.2.3"\n') == "1.2.3"


def test_a_dev_version_appends_the_run_number():
    assert rc.dev_version("0.1.0", 42) == "0.1.0.dev42"


# --- rewriting the version


def test_with_version_changes_only_the_project_tables_version():
    text = '[project]\nname = "x"\nversion = "1.0.0"\n\n[tool.other]\nversion = "9.9.9"\n'
    assert rc.with_version(text, "1.0.0.dev3") == (
        '[project]\nname = "x"\nversion = "1.0.0.dev3"\n\n[tool.other]\nversion = "9.9.9"\n'
    )


def test_with_version_round_trips_the_real_pyproject():
    rewritten = rc.with_version(PYPROJECT, "0.1.0.dev7")
    assert rc.project_version(rewritten) == "0.1.0.dev7"
    changed = [(a, b) for a, b in zip(PYPROJECT.splitlines(), rewritten.splitlines()) if a != b]
    assert len(changed) == 1


def test_with_version_refuses_a_project_table_without_a_version():
    with pytest.raises(ValueError):
        rc.with_version('[project]\nname = "x"\n\n[tool.other]\nversion = "9.9.9"\n', "1.0.0")


# --- changelog

CHANGELOG = """# Changelog

## [0.2.0] - 2026-11-01

### Fixed
- Second release.

## [0.1.0.dev1] - 2026-10-01

- A dev note.

## [0.1.0] - 2026-10-20

### Added
- First release.

## [0.0.9]

[0.1.0]: https://github.com/ahochoy/phil/releases/tag/v0.1.0
"""


def test_a_changelog_section_runs_to_the_next_heading():
    assert rc.changelog_section(CHANGELOG, "0.2.0") == "### Fixed\n- Second release."


def test_a_changelog_section_is_matched_exactly_not_by_prefix():
    assert rc.changelog_section(CHANGELOG, "0.1.0") == "### Added\n- First release."
    assert rc.changelog_section(CHANGELOG, "0.1.01") is None


def test_a_missing_or_empty_changelog_section_is_none():
    assert rc.changelog_section(CHANGELOG, "3.0.0") is None
    assert rc.changelog_section(CHANGELOG, "0.0.9") is None  # only a link reference follows it


# --- sdist size


def test_the_sdist_must_be_under_two_megabytes(tmp_path):
    small, big = tmp_path / "small.tar.gz", tmp_path / "big.tar.gz"
    small.write_bytes(b"x" * (rc.SDIST_LIMIT - 1))
    big.write_bytes(b"x" * rc.SDIST_LIMIT)
    assert rc.sdist_ok(small) and not rc.sdist_ok(big)


# --- fetching the published wheel


def _release(payload: bytes) -> dict:
    return {
        "urls": [
            {"packagetype": "sdist", "filename": "phil_agent-0.1.0.tar.gz", "url": "https://f/sdist",
             "digests": {"sha256": "0"}},
            {"packagetype": "bdist_wheel", "filename": "phil_agent-0.1.0-py3-none-any.whl",
             "url": "https://f/phil_agent-0.1.0-py3-none-any.whl",
             "digests": {"sha256": hashlib.sha256(payload).hexdigest()}},
        ]
    }


def test_pick_wheel_takes_the_pure_python_wheel():
    assert rc.pick_wheel(_release(b"w")) == (
        "https://f/phil_agent-0.1.0-py3-none-any.whl", hashlib.sha256(b"w").hexdigest()
    )


def test_pick_wheel_fails_when_the_release_has_no_wheel():
    with pytest.raises(LookupError):
        rc.pick_wheel({"urls": [{"packagetype": "sdist", "filename": "x.tar.gz", "url": "u", "digests": {}}]})


class _FakeIndex:
    """`urlopen` for an index that 404s `misses` times before it knows the release."""

    def __init__(self, misses: int, payload: bytes, release: dict):
        self.misses, self.payload, self.release, self.calls = misses, payload, release, []

    def __call__(self, url, timeout=None):
        self.calls.append(url)
        if url.endswith("/json"):
            if self.misses:
                self.misses -= 1
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
            return io.BytesIO(json.dumps(self.release).encode())
        return io.BytesIO(self.payload)


def test_fetch_wheel_retries_while_the_index_catches_up(tmp_path):
    index = _FakeIndex(misses=2, payload=b"wheel-bytes", release=_release(b"wheel-bytes"))
    slept = []
    path = rc.fetch_wheel("https://test/json", tmp_path, attempts=5, delay=1, urlopen=index, sleep=slept.append)
    assert path == tmp_path / "phil_agent-0.1.0-py3-none-any.whl"
    assert path.read_bytes() == b"wheel-bytes"
    assert slept == [1, 1]


def test_fetch_wheel_gives_up_after_its_attempts(tmp_path):
    index = _FakeIndex(misses=99, payload=b"", release={})
    with pytest.raises(RuntimeError):
        rc.fetch_wheel("https://test/json", tmp_path, attempts=3, delay=0, urlopen=index, sleep=lambda s: None)
    assert len(index.calls) == 3


def test_fetch_wheel_rejects_a_wheel_whose_hash_differs(tmp_path):
    index = _FakeIndex(misses=0, payload=b"tampered", release=_release(b"original"))
    with pytest.raises(RuntimeError, match="sha256"):
        rc.fetch_wheel("https://test/json", tmp_path, attempts=1, delay=0, urlopen=index, sleep=lambda s: None)


# --- the command line


def test_check_tag_exits_1_with_a_reason_on_a_mismatch(capsys):
    assert rc.main(["check-tag", "v99.0.0"]) == 1
    assert "v99.0.0" in capsys.readouterr().err


def test_check_tag_passes_for_the_current_version():
    assert rc.main(["check-tag", "v" + rc.project_version(PYPROJECT)]) == 0


def test_notes_exits_1_when_the_changelog_has_no_section(capsys, monkeypatch, tmp_path):
    changelog = tmp_path / "CHANGELOG.md"  # CHANGELOG.md itself arrives in Task 3
    changelog.write_text(CHANGELOG, encoding="utf-8")
    monkeypatch.setattr(rc, "CHANGELOG", changelog)
    assert rc.main(["notes", "99.0.0"]) == 1
    assert "99.0.0" in capsys.readouterr().err


def test_dev_version_prints_the_version_for_this_run(capsys):
    assert rc.main(["dev-version", "5"]) == 0
    assert capsys.readouterr().out == rc.project_version(PYPROJECT) + ".dev5\n"
