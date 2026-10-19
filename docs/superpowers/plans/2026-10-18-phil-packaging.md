# Phil Packaging and Release Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Phil installable as `phil-agent` from PyPI, through a tag-driven GitHub Actions release that tests, builds, smoke-tests and publishes with trusted publishing, and prove the pipeline with a TestPyPI rehearsal.

**Architecture:**
- `pyproject.toml` is the only version source. `phil.__version__` reads the installed distribution's metadata.
- A stdlib-only script, `.github/scripts/release_check.py`, holds every release decision so that unit tests can cover it: tag against version, the changelog section, the dev version, the sdist size, and fetching the published wheel.
- `release.yml` chains these jobs: check → tests (reused `tests.yml`) → build → smoke on 3 OSes → publish → verify → GitHub Release.
- The README splits into a short PyPI-safe page, `docs/guide.md` and `CONTRIBUTING.md`.

**Tech Stack:** hatchling (build backend), uv (`uv build`, `uv publish`, `uv tool install`), GitHub Actions with OIDC trusted publishing, pytest, `twine check`, actionlint.

**Spec:** `docs/superpowers/specs/2026-10-18-phil-packaging-design.md`

## Global Constraints

- Distribution name `phil-agent`. Command `phil`. Import package `phil`.
- `version = "0.1.0"` in `pyproject.toml`, the only place the version is written.
- `requires-python = ">=3.14"` stays. CI uses Python `"3.14"`.
- `license = "Apache-2.0"` (SPDX string) with `license-files = ["LICENSE"]`. Don't use License classifiers alongside it.
- The sdist contains only `src/`, `tests/`, `README.md`, `LICENSE`, `CHANGELOG.md`, `pyproject.toml`, `uv.lock` (plus the `PKG-INFO` hatchling adds). It must be under 2 MB (`2 * 1024 * 1024` bytes).
- Tag runs publish to PyPI in environment `pypi`. Manual (`workflow_dispatch`) runs publish `X.Y.Z.dev<run number>` to TestPyPI in environment `testpypi`.
- Only the publish job has `id-token: write`. Every other job is `contents: read`, except github-release, which is `contents: write`.
- Actions are pinned to commit SHAs with a version comment:
  - `actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1`
  - `astral-sh/setup-uv@c18668ad3cf93ea998bef934396af7bb5c839dc7 # v10.2.0`
  - `actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9 # v7.0.2`
  - `actions/download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333 # v8.0.2`
- README links and images are absolute (`https://…`) or in-page anchors (`#…`). Never relative.
- Never run `-m live` or `-m bench`. Tests use `PHIL_HOME` set to a temp dir, as the existing conftest already does.
- Edit files with the Edit or Write tools only, never sed, python, heredocs or printf.
- Commit by writing the message to `.git/phil-commit-msg.txt` with Write, then `git commit -F .git/phil-commit-msg.txt`. Every message ends with `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`. Stage only the files the task names.

## Review Focus

1. **A developer's old editable install named `phil`.** After the rename, `uv tool install --editable .` installs a tool named `phil-agent` that also ships a `phil` executable, and that clashes with the existing `phil` tool. CONTRIBUTING has to say to run `uv tool uninstall phil` first (Task 4 covers the text; nothing tests it automatically).
2. **The index hasn't caught up yet.** Right after `uv publish`, the JSON API or simple index can return 404 for a minute or more. The verify job must retry and not fail the release. Task 2 pins this with a fetch-retry test using a fake opener.
3. **A runner with no OS key store.** `phil keys list` in the smoke job must print `missing` and exit 0 on a runner without a working key store. Task 5 pins this with a fail-backend test.
4. **Changelog edge cases.** A heading for a different version that shares a prefix (`## [0.1.0.dev1]`, `## [0.1.01]`), a section that is empty, and link-reference lines at the bottom must not be taken as the requested section. Task 2 covers these.
5. **Rewriting the version touches the wrong line.** The dev-version rewrite of `pyproject.toml` must change only `[project]`'s `version`, never a `version =` in another table. Task 2 covers this.

## Rulings made while planning

- **Spec §3.1 Homepage.** The spec says Homepage is "the site", but the site has no deployed URL in the repo (`site/astro.config.*` sets no `site:`). So Homepage is the GitHub repo, and a `Documentation` URL points at `docs/guide.md`. Cost if wrong: a one-line URL change when the site goes live.
- **Spec §5.2 TestPyPI verify mechanism.** The spec suggests a temporary uv config that pins `phil-agent` to TestPyPI. uv's `[tool.uv.sources]` pinning only applies inside a project, not to `uv tool install`, and an extra `--index` lets any package resolve from TestPyPI (the dependency-squatting risk the spec wants to avoid). Instead, the verify job downloads the exact wheel from TestPyPI's JSON API, checks its sha256 and `uv tool install`s that file, with dependencies from PyPI only. This keeps the spec's intent: the published file is the one installed, and no dependency comes from TestPyPI. Cost if wrong: the rehearsal skips TestPyPI's simple index, which real PyPI installs exercise anyway on the tag run.
- **Spec §6.1 test locations.** The packaging tests live in `tests/release/`, a new directory. The release script is loaded from `tests/release/__init__.py`, so every test in that directory shares one import.

## File structure

| File | Responsibility |
|---|---|
| `pyproject.toml` | Distribution name, metadata and the version (Task 1); sdist include list (Task 3) |
| `uv.lock` | Relocked for the rename (Task 1) |
| `src/phil/__init__.py` | `__version__` from distribution metadata (Task 1) |
| `src/phil/cli/main.py` | Banner uses `__version__` (Task 1) |
| `tests/test_version.py` | Version-source tests (Task 1) |
| `tests/cli/test_banner_startup.py` | Banner version test, replaced (Task 1) |
| `.github/scripts/release_check.py` | Every release decision, as a stdlib CLI (Task 2) |
| `tests/release/__init__.py` | Loads `release_check` for the tests (Task 2) |
| `tests/release/test_release_check.py` | Script unit tests (Task 2) |
| `CHANGELOG.md` | Release notes, a `## [0.1.0]` section (Task 3) |
| `tests/release/test_dist.py` | Wheel and sdist contents and size (Task 3) |
| `tests/release/test_changelog.py` | The current version has a changelog section (Task 3) |
| `README.md` | Short user page, also the PyPI description (Task 4) |
| `docs/guide.md` | Full user reference, moved from README (Task 4) |
| `CONTRIBUTING.md` | Development, tests, benchmark, releasing (Task 4) |
| `tests/release/test_readme.py` | README is PyPI-safe (Task 4) |
| `.github/workflows/tests.yml` | Gains `workflow_call`; concurrency group per caller (Task 5) |
| `.github/workflows/release.yml` | The release pipeline (Task 5) |
| `tests/release/test_workflows.py` | Workflow invariants that actionlint doesn't check (Task 5) |
| `tests/cli/test_keys_command.py` | `keys list` without a key store (Task 5) |

---

### Task 1: Rename to `phil-agent`, add metadata, one version source

**Files:**
- Modify: `pyproject.toml` (the `[project]` table and `[build-system]`)
- Modify: `uv.lock` (regenerated by `uv lock`)
- Modify: `src/phil/__init__.py`
- Modify: `src/phil/cli/main.py:3` (the import) and `:260-263` (the banner version)
- Create: `tests/test_version.py`
- Modify: `tests/cli/test_banner_startup.py:1` and `:258-264`

**Interfaces:**
- Produces:
  - `phil.DIST_NAME: str = "phil-agent"`
  - `phil._installed_version() -> str`
  - `phil._dist_version` (the module-level alias of `importlib.metadata.version`, patched by tests)
  - `phil.__version__: str`

- [ ] **Step 1: Write the failing version tests**

Create `tests/test_version.py`:

```python
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
```

Replace the last test in `tests/cli/test_banner_startup.py` (`test_version_falls_back_to_dev`, lines 258-264) with:

```python
def test_the_banner_shows_phils_version(calc_repo, monkeypatch):
    monkeypatch.setattr(cli, "__version__", "9.8.7")
    result = runner.invoke(cli.app, ["--repo", str(calc_repo)], input="")
    assert result.exit_code == 0, result.output
    assert "Phil v9.8.7" in result.output
```

Then delete `import importlib.metadata` (line 1) from that file. Nothing else in it uses it.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_version.py tests/cli/test_banner_startup.py::test_the_banner_shows_phils_version -n 0 -q`

Expected: 3 FAIL:
- `test_the_version_is_the_installed_distributions`: `assert 'phil' == 'phil-agent'`.
- `test_the_version_falls_back…`: `AttributeError` (no `_dist_version`).
- `test_the_banner_shows_phils_version`: the banner still prints `Phil v0.1.0` from `importlib.metadata.version("phil")`.

- [ ] **Step 3: Rename and add the metadata in `pyproject.toml`**

Replace the top of the `[project]` table, from `[project]` through `requires-python = ">=3.14"`, with:

```toml
[project]
name = "phil-agent"
version = "0.1.0"
description = "A terminal coding agent that plans with you, works in its own git worktree, and stops at the budget you set"
readme = "README.md"
license = "Apache-2.0"
license-files = ["LICENSE"]
authors = [{ name = "Andrew HoChoy" }]
keywords = ["coding agent", "llm", "cli", "langgraph", "ai agent"]
classifiers = [
    "Development Status :: 3 - Alpha",
    "Environment :: Console",
    "Intended Audience :: Developers",
    "Operating System :: MacOS",
    "Operating System :: Microsoft :: Windows",
    "Operating System :: POSIX :: Linux",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3 :: Only",
    "Programming Language :: Python :: 3.14",
    "Topic :: Software Development",
]
requires-python = ">=3.14"
```

Leave `dependencies` unchanged. After `[project.scripts]` (which stays `phil = "phil.cli.main:app"`), add:

```toml
[project.urls]
Homepage = "https://github.com/ahochoy/phil"
Documentation = "https://github.com/ahochoy/phil/blob/main/docs/guide.md"
Repository = "https://github.com/ahochoy/phil"
Issues = "https://github.com/ahochoy/phil/issues"
Changelog = "https://github.com/ahochoy/phil/blob/main/CHANGELOG.md"
```

Change `[build-system]` `requires = ["hatchling"]` to `requires = ["hatchling>=1.27"]`. 1.27 is the first hatchling version that writes SPDX `License-Expression` metadata.

- [ ] **Step 4: Read the version from metadata**

Replace all of `src/phil/__init__.py` with:

```python
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _dist_version

DIST_NAME = "phil-agent"


def _installed_version() -> str:
    """The installed distribution's version, which comes from pyproject.toml. "0+unknown" when
    Phil runs from a source tree that was never installed."""
    try:
        return _dist_version(DIST_NAME)
    except PackageNotFoundError:
        return "0+unknown"


__version__ = _installed_version()
```

In `src/phil/cli/main.py`, replace lines 260-263:

```python
    try:
        version = importlib.metadata.version("phil")
    except Exception:
        version = "dev"
```

with:

```python
    version = __version__
```

Then delete `import importlib.metadata` (line 3), after confirming with `grep -n "importlib" src/phil/cli/main.py` that nothing else uses it.

- [ ] **Step 5: Relock and resync**

Run: `uv lock && uv sync`

Expected:
- `uv lock` reports the root package renamed (`phil` removed, `phil-agent` added), with no other version changes.
- `uv sync` reinstalls the project as `phil-agent`.
- `git diff --stat uv.lock` shows a small diff.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_version.py tests/cli/test_banner_startup.py tests/test_cli.py -n 0 -q`

Expected: all pass, including `test_version_flag_prints_version`.

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest -q > .superpowers/task1-suite.txt 2>&1; tail -3 .superpowers/task1-suite.txt`

Expected: all passed. Nothing else depends on the distribution name.

- [ ] **Step 8: Commit**

Write `.git/phil-commit-msg.txt`:

```
Packaging: publish as phil-agent, with one version source

The name phil is taken on PyPI, so the distribution becomes
phil-agent. The command and the import package stay phil.
pyproject.toml gains the metadata PyPI shows: description, SPDX
license, classifiers and project URLs.

pyproject.toml is now the only place the version is written:
phil.__version__ reads it back from the installed metadata, and the
banner uses it. Before, the banner asked for the "phil" distribution,
which after the rename would have shown "dev" to every user.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
```

Run: `git add pyproject.toml uv.lock src/phil/__init__.py src/phil/cli/main.py tests/test_version.py tests/cli/test_banner_startup.py && git commit -F .git/phil-commit-msg.txt`

---

### Task 2: The release-check script

**Files:**
- Create: `.github/scripts/release_check.py`
- Create: `tests/release/__init__.py`
- Create: `tests/release/test_release_check.py`

**Interfaces:**
- Consumes: `pyproject.toml` `[project] version` (Task 1).
- Produces (`release_check` module, also a CLI run as `uv run --no-project python .github/scripts/release_check.py <command>`):
  - `project_version(pyproject: str) -> str`
  - `tag_matches(tag: str, version: str) -> bool`
  - `dev_version(version: str, run_number: int) -> str`
  - `with_version(pyproject: str, version: str) -> str` (raises `ValueError`)
  - `changelog_section(changelog: str, version: str) -> str | None`
  - `sdist_ok(path: Path) -> bool`
  - `pick_wheel(release: dict) -> tuple[str, str]` (raises `LookupError`)
  - `fetch_wheel(json_url: str, out_dir: Path, *, attempts: int = 20, delay: float = 15, urlopen=urllib.request.urlopen, sleep=time.sleep) -> Path`
  - `main(argv: list[str]) -> int`, with the commands `version`, `check-tag TAG`, `dev-version RUN_NUMBER`, `set-version VERSION`, `notes VERSION`, `check-sdist PATH`, `fetch-wheel JSON_URL OUT_DIR`
  - `SDIST_LIMIT = 2 * 1024 * 1024`
  - `tests.release.release_check`: the loaded module, for tests.

- [ ] **Step 1: Write the loader and the failing tests**

Create `tests/release/__init__.py`:

```python
"""Tests for packaging and the release pipeline (spec 2026-10-18). `.github/scripts` isn't a package,
so the release script is loaded from its path once, here, for every test in this directory."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = ROOT / ".github" / "scripts" / "release_check.py"

_spec = importlib.util.spec_from_file_location("release_check", _SCRIPT)
release_check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release_check)
```

Create `tests/release/test_release_check.py`:

```python
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
             "url": "https://f/wheel", "digests": {"sha256": hashlib.sha256(payload).hexdigest()}},
        ]
    }


def test_pick_wheel_takes_the_pure_python_wheel():
    assert rc.pick_wheel(_release(b"w")) == ("https://f/wheel", hashlib.sha256(b"w").hexdigest())


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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/release -n 0 -q`

Expected: collection ERROR, `FileNotFoundError` for `.github/scripts/release_check.py`.

- [ ] **Step 3: Write the script**

Create `.github/scripts/release_check.py`:

```python
"""Release checks for .github/workflows/release.yml (spec 2026-10-18 §5). Stdlib only, so the
workflow can run it without installing Phil:

    uv run --no-project python .github/scripts/release_check.py <command> ...

Commands (paths are relative to the repository root):
  version                  print pyproject.toml's [project] version
  check-tag TAG            exit 1 unless TAG is "v" + that version
  dev-version RUN_NUMBER   print VERSION.devRUN_NUMBER, the version a TestPyPI rehearsal publishes
  set-version VERSION      rewrite pyproject.toml's [project] version (the CI checkout only)
  notes VERSION            print CHANGELOG.md's section for VERSION; exit 1 if it has none
  check-sdist PATH         exit 1 if the sdist is 2 MB or larger
  fetch-wheel JSON_URL DIR download the wheel a PyPI JSON-API release lists into DIR, checking
                           its sha256 and retrying while the index catches up; print its path
"""

import argparse
import hashlib
import json
import re
import sys
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = ROOT / "pyproject.toml"
CHANGELOG = ROOT / "CHANGELOG.md"
SDIST_LIMIT = 2 * 1024 * 1024

_VERSION_LINE = re.compile(r'^version = "[^"]*"$', re.MULTILINE)


def project_version(pyproject: str) -> str:
    return tomllib.loads(pyproject)["project"]["version"]


def tag_matches(tag: str, version: str) -> bool:
    return tag == f"v{version}"


def dev_version(version: str, run_number: int) -> str:
    return f"{version}.dev{run_number}"


def with_version(pyproject: str, version: str) -> str:
    """`pyproject` with the [project] table's `version` line replaced and every other line kept."""
    start = pyproject.index("[project]\n")
    end = pyproject.find("\n[", start)
    end = len(pyproject) if end == -1 else end
    table, count = _VERSION_LINE.subn(f'version = "{version}"', pyproject[start:end], count=1)
    if count != 1:
        raise ValueError("pyproject.toml's [project] table has no version line")
    return pyproject[:start] + table + pyproject[end:]


def changelog_section(changelog: str, version: str) -> str | None:
    """The body under `## [VERSION]`, up to the next `## ` heading or link reference. None when
    there's no such heading or its body is empty."""
    pattern = re.compile(
        rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## |^\[[^\]]+\]: |\Z)", re.MULTILINE | re.DOTALL
    )
    match = pattern.search(changelog)
    if match is None:
        return None
    return match.group(1).strip() or None


def sdist_ok(path: Path) -> bool:
    return path.stat().st_size < SDIST_LIMIT


def pick_wheel(release: dict) -> tuple[str, str]:
    """(url, sha256) of the pure-Python wheel in a PyPI JSON-API release document."""
    for entry in release.get("urls", []):
        if entry.get("packagetype") == "bdist_wheel" and entry["filename"].endswith("-py3-none-any.whl"):
            return entry["url"], entry["digests"]["sha256"]
    raise LookupError("the release lists no py3-none-any wheel")


def fetch_wheel(json_url: str, out_dir: Path, *, attempts: int = 20, delay: float = 15,
                urlopen=urllib.request.urlopen, sleep=time.sleep) -> Path:
    """Download the wheel `json_url` lists into `out_dir`. A 404, or a release with no wheel yet,
    is retried `attempts` times, `delay` seconds apart: an index takes a while to serve a new
    upload. A wheel whose sha256 differs from the listed one is an error, not retried."""
    for attempt in range(attempts):
        try:
            with urlopen(json_url, timeout=30) as response:
                url, sha256 = pick_wheel(json.load(response))
            break
        except (urllib.error.HTTPError, LookupError) as error:
            if isinstance(error, urllib.error.HTTPError) and error.code != 404:
                raise
            if attempt == attempts - 1:
                raise RuntimeError(f"{json_url} had no wheel after {attempts} attempts") from error
            sleep(delay)
    with urlopen(url, timeout=120) as response:
        payload = response.read()
    if hashlib.sha256(payload).hexdigest() != sha256:
        raise RuntimeError(f"the downloaded wheel's sha256 doesn't match the index's ({url})")
    path = out_dir / url.rsplit("/", 1)[-1]
    path.write_bytes(payload)
    return path


def _fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 1


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="release_check")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("version")
    commands.add_parser("check-tag").add_argument("tag")
    commands.add_parser("dev-version").add_argument("run_number", type=int)
    commands.add_parser("set-version").add_argument("version")
    commands.add_parser("notes").add_argument("version")
    commands.add_parser("check-sdist").add_argument("path", type=Path)
    fetch = commands.add_parser("fetch-wheel")
    fetch.add_argument("json_url")
    fetch.add_argument("out_dir", type=Path)
    args = parser.parse_args(argv)

    version = project_version(PYPROJECT.read_text(encoding="utf-8"))
    if args.command == "version":
        print(version)
    elif args.command == "check-tag":
        if not tag_matches(args.tag, version):
            return _fail(f"tag {args.tag} doesn't match pyproject.toml's version {version} (expected v{version})")
    elif args.command == "dev-version":
        print(dev_version(version, args.run_number))
    elif args.command == "set-version":
        PYPROJECT.write_text(with_version(PYPROJECT.read_text(encoding="utf-8"), args.version), encoding="utf-8")
    elif args.command == "notes":
        section = changelog_section(CHANGELOG.read_text(encoding="utf-8"), args.version)
        if section is None:
            return _fail(f"CHANGELOG.md has no section for {args.version}; add a '## [{args.version}]' heading")
        print(section)
    elif args.command == "check-sdist":
        if not sdist_ok(args.path):
            return _fail(f"{args.path.name} is {args.path.stat().st_size} bytes; the limit is {SDIST_LIMIT}")
    elif args.command == "fetch-wheel":
        print(fetch_wheel(args.json_url, args.out_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/release -n 0 -q`

Expected: all pass.

- [ ] **Step 5: Run the CLI by hand once**

Run: `uv run --no-project python .github/scripts/release_check.py version && uv run --no-project python .github/scripts/release_check.py check-tag v0.1.0; echo "exit $?"`

Expected: `0.1.0`, then `exit 0`.

- [ ] **Step 6: Run the full suite and commit**

Run: `uv run pytest -q > .superpowers/task2-suite.txt 2>&1; tail -3 .superpowers/task2-suite.txt`

Expected: all passed.

Write `.git/phil-commit-msg.txt`:

```
Release: one script for every release decision

.github/scripts/release_check.py holds the checks the release workflow
makes, so they have unit tests instead of living as shell in YAML:
- the tag matches pyproject.toml's version;
- the changelog has a section for it;
- the dev version a TestPyPI rehearsal publishes, and rewriting it into
  the CI checkout;
- the sdist size limit;
- fetching the published wheel, retrying while the index catches up and
  checking its sha256.

It's stdlib only, so the workflow runs it without installing Phil.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
```

Run: `git add .github/scripts/release_check.py tests/release/__init__.py tests/release/test_release_check.py && git commit -F .git/phil-commit-msg.txt`

---

### Task 3: Sdist include list, CHANGELOG, distribution tests

**Files:**
- Modify: `pyproject.toml` (new `[tool.hatch.build.targets.sdist]` table after `[tool.hatch.build.targets.wheel]`)
- Create: `CHANGELOG.md`
- Create: `tests/release/test_dist.py`
- Create: `tests/release/test_changelog.py`

**Interfaces:**
- Consumes:
  - `tests.release.ROOT` and `tests.release.release_check` (Task 2);
  - `release_check.project_version`, `release_check.changelog_section` and `release_check.SDIST_LIMIT` (Task 2);
  - distribution name `phil-agent`, so the files are `phil_agent-<version>…` (Task 1).
- Produces: `CHANGELOG.md` with `## [0.1.0]`, which the release workflow's check job reads (Task 5).

- [ ] **Step 1: Write the failing tests**

Create `tests/release/test_dist.py`:

```python
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
    assert "phil = phil.cli.main:app" in wheel.read(entry_points).decode()


def test_the_wheel_holds_only_the_phil_package(dist):
    tops = {name.split("/")[0] for name in _wheel(dist).namelist()}
    assert {t for t in tops if not t.endswith(".dist-info")} == {"phil"}


def test_the_sdist_holds_only_the_include_list(dist):
    with tarfile.open(_sdist(dist)) as sdist:
        names = sdist.getnames()
    tops = {parts[1] for parts in (n.split("/") for n in names) if len(parts) > 1 and parts[1]}
    assert tops - {"PKG-INFO"} == SDIST_TOP
    assert [n for n in names if "__pycache__" in n or n.endswith(".pyc")] == []


def test_the_sdist_is_under_the_size_limit(dist):
    assert release_check.sdist_ok(_sdist(dist)), _sdist(dist).stat().st_size
```

Create `tests/release/test_changelog.py`:

```python
"""Every version has release notes before it ships (spec 2026-10-18 §3.5). The release workflow
checks this too; this catches it on the pull request that bumps the version."""

from tests.release import ROOT, release_check


def test_the_changelog_has_a_section_for_the_current_version():
    version = release_check.project_version((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert release_check.changelog_section(changelog, version), f"CHANGELOG.md needs a '## [{version}]' section"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/release/test_dist.py tests/release/test_changelog.py -n 0 -q`

Expected:
- `test_the_sdist_holds_only_the_include_list` FAILS (extra tops such as `site`, `docs`, `prototype`).
- `test_the_sdist_is_under_the_size_limit` FAILS (about 17 MB).
- `test_the_changelog_has_a_section…` FAILS (`FileNotFoundError: CHANGELOG.md`).
- The three wheel tests PASS. They pin the wheel's current contents, which this task must not change.

- [ ] **Step 3: Add the sdist include list**

In `pyproject.toml`, after the `[tool.hatch.build.targets.wheel]` table, add:

```toml
# Only what rebuilds and tests the package: the site, docs, prototype and assets stay out.
[tool.hatch.build.targets.sdist]
include = [
    "/src",
    "/tests",
    "/README.md",
    "/LICENSE",
    "/CHANGELOG.md",
    "/pyproject.toml",
    "/uv.lock",
]
```

- [ ] **Step 4: Write `CHANGELOG.md`**

```markdown
# Changelog

All notable changes to Phil are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [0.1.0] - Unreleased

The first public release, published to PyPI as `phil-agent`.

### Added

- `phil`, a chat in the terminal that plans a goal with you, then runs it in its own git worktree
  and leaves a tested, reviewed branch.
- Two model tiers, `high` and `low`. Providers: OpenRouter, OpenAI, Anthropic, Google, Ollama and
  any OpenAI-compatible endpoint.
- `phil setup` for choosing a provider, storing its key in the OS key store and checking each model.
- Request routing with the Jev classifier.
- A run budget that stops a run before it spends more than you allowed.
- `phil pr` to push a finished run and open its pull request, and `phil clean` to remove it.
- `phil show`, `phil diff` and `phil runs` for following and inspecting runs.
- Rules for the shell commands agents may run (`phil permissions`).
- Repo memory that Phil keeps between runs (`phil memory`).
- Native Windows support through Git Bash.
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/release -n 0 -q`

Expected: all pass.

- [ ] **Step 6: Run the full suite and commit**

Run: `uv run pytest -q > .superpowers/task3-suite.txt 2>&1; tail -3 .superpowers/task3-suite.txt`

Expected: all passed.

Write `.git/phil-commit-msg.txt`:

```
Packaging: a small sdist, and a changelog

The sdist took the whole repository (17 MB, with the site, docs and
prototype). It now holds only what rebuilds and tests the package:
src, tests, the README, license, changelog, pyproject.toml and
uv.lock.

CHANGELOG.md starts with the 0.1.0 section. A test requires a section
for the current version, so a version bump can't merge without notes.
Further tests pin the wheel: every prompt file, the phil command, and
nothing but the phil package.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
```

Run: `git add pyproject.toml CHANGELOG.md tests/release/test_dist.py tests/release/test_changelog.py && git commit -F .git/phil-commit-msg.txt`

---

### Task 4: Split the README

**Files:**
- Modify: `README.md` (rewritten)
- Create: `docs/guide.md`
- Create: `CONTRIBUTING.md`
- Create: `tests/release/test_readme.py`

**Interfaces:**
- Consumes: `tests.release.ROOT` (Task 2).
- Produces: `CONTRIBUTING.md` with a `## Releasing` section, which Task 5's workflow comments point to.

- [ ] **Step 1: Write the failing test**

Create `tests/release/test_readme.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/release/test_readme.py -n 0 -q`

Expected: 2 FAIL:
- `relative == ['assets/banner.jpg']`;
- `uv tool install phil-agent` not in the README.

- [ ] **Step 3: Write `docs/guide.md`**

Read `README.md` in full first. Then create `docs/guide.md` from:

1. This header:

   ```markdown
   # Phil user guide

   Everything about using Phil: setup, configuration, chats and runs, and the commands around
   them. For installing Phil, see the [README](../README.md). For working on Phil itself, see
   [CONTRIBUTING](../CONTRIBUTING.md).

   The design behind it is in `superpowers/specs/2026-09-23-phil-v1-design.md`.
   ```

2. Then today's `README.md` from the line `## Getting started` (line 11) through the end of the `## Memory` section (the line before `## Benchmark`, line 677), copied verbatim.

The moved text has no relative Markdown links (`grep -nE '\]\(' README.md` shows only absolute URLs in that range). Re-run that grep on `docs/guide.md`. Any link to a repository path must be relative to `docs/`.

- [ ] **Step 4: Write `CONTRIBUTING.md`**

````markdown
# Contributing to Phil

## Development

    uv sync
    uv run pytest
    uv run phil --version

To use your working copy as `phil` from any repo, install it as an editable tool. Code changes
are picked up automatically; reinstall after dependencies change:

    uv tool install --editable . --force

The distribution is named `phil-agent`. If you installed an editable `phil` tool before the
rename, remove it first, or the two tools clash over the `phil` command:

    uv tool uninstall phil

The original LangGraph prototype is kept in `prototype/` for reference and is not part of the package.

## Tests

Tests run in parallel with pytest-xdist; use `uv run pytest -n 0` to run serially when debugging.
CI runs the same suite on Linux, macOS and Windows for every pull request.

<<copy here, verbatim, the README's "Live tests call a real model…" paragraph through
"…it never changes or removes one." (README.md lines 728-736), and the
"Start a run from a plan file and follow it:" block after it (lines 740-743)>>

## Benchmark

<<copy here, verbatim, the body of README.md's "## Benchmark" section (lines 680-714),
without its heading>>

## Releasing

Releases are tag-driven. The version is written in one place, `pyproject.toml`.

1. In a pull request, bump `version` in `pyproject.toml` and add a `## [X.Y.Z] - YYYY-MM-DD`
   section to `CHANGELOG.md`. A test fails if the section is missing.
2. Merge it, then tag the merge commit on `main` and push the tag:

       git tag vX.Y.Z
       git push origin vX.Y.Z

3. The `release` workflow then runs the checks below. If any fails, nothing is published:
   - the tag must equal `v` plus the version;
   - the changelog must have that version's section;
   - the full test suite;
   - the build, `twine check`, and an sdist under 2 MB;
   - a smoke test of the built wheel on Linux, macOS and Windows.

   It then publishes to PyPI with trusted publishing, installs the published version back from
   PyPI, and creates the GitHub Release with that changelog section as notes.

### Rehearsing on TestPyPI

Run the `release` workflow by hand (Actions → release → Run workflow). It runs the same checks
and publishes `X.Y.Z.dev<run number>` to TestPyPI, so every rehearsal has a new version. To try
that build locally, download its wheel and install it with dependencies from PyPI:

    uv run --no-project python .github/scripts/release_check.py fetch-wheel \
      https://test.pypi.org/pypi/phil-agent/X.Y.Z.devN/json .
    uv tool install ./phil_agent-X.Y.Z.devN-py3-none-any.whl --force
    phil --version
    phil setup

### One-time setup

Trusted publishing needs no API token, but each index has to know the workflow:

1. On PyPI (Account → Publishing → Add a new pending publisher): project `phil-agent`, owner
   `ahochoy`, repository `phil`, workflow `release.yml`, environment `pypi`.
2. On TestPyPI, the same with environment `testpypi`.
3. In this repository (Settings → Environments), create `pypi` and `testpypi`. Add yourself as
   a required reviewer on `pypi`, so every real publish waits for a click.
````

Replace each `<<…>>` instruction with the text it names, copied verbatim from the current `README.md`. Check the line numbers against the file you read in Step 3 first. Indented code blocks stay indented.

- [ ] **Step 5: Rewrite `README.md`**

Replace all of `README.md` with:

```markdown
<p align="center">
  <img src="https://raw.githubusercontent.com/ahochoy/phil/main/assets/banner.jpg" alt="Phil — The Friendly, Token-Efficient Development Agent" width="100%" />
</p>

# Phil

Named after *philia* (ancient Greek for friendship and mutual goodwill), Phil is a friendly
terminal coding agent built to be a token-efficient teammate. Run it inside a git repository:
it plans a goal with you, implements it in its own git worktree, and leaves a tested, reviewed
branch. It stops before it spends more than the budget you set.

## Install

Phil needs Python 3.14. [uv](https://docs.astral.sh/uv/) installs it for you if it's missing:

    uv tool install phil-agent

Or with [pipx](https://pipx.pypa.io/):

    pipx install phil-agent

Either way you get the `phil` command. To upgrade later, run `uv tool upgrade phil-agent` (or
`pipx upgrade phil-agent`).

**Windows:** Phil runs natively and needs [Git for Windows](https://git-scm.com/download/win),
which includes Git Bash. See the guide's
[Windows section](https://github.com/ahochoy/phil/blob/main/docs/guide.md#windows).

## Set up

    cd your-repo
    phil

The first time, setup starts automatically. It asks for a provider (OpenRouter, OpenAI,
Anthropic, Google, Ollama or any OpenAI-compatible endpoint), stores its API key in your OS key
store, picks a `high` and a `low` model and checks that each one answers. Run `phil setup` any
time to change them.

## Your first goal

In the chat, describe what you want:

    you › add a --verbose flag to the CLI that prints each step

Phil asks what it needs to know, shows you the plan, and runs it when you agree. When the run
finishes, look at the branch it left and open a pull request:

    phil diff <run-id>
    phil pr <run-id>

## Commands

| Command | What it does |
|---|---|
| `phil` | Open the chat in the current repository |
| `phil setup` | Choose a provider, store its key and pick your models |
| `phil runs` | List this repository's runs |
| `phil show <run>` | A run's tasks, usage and open issues |
| `phil attach <run>` | Follow a run and answer it when it pauses |
| `phil resume <run>` | Answer a paused run, or continue a stopped one |
| `phil stop <run>` | Stop a running run |
| `phil diff <run>` | The changes a run made |
| `phil pr <run>` | Push a finished run's branch and open its pull request |
| `phil clean <run>` | Remove a finished run's worktree and branch |
| `phil keys list` | Where each provider's key comes from |
| `phil models` | Check the configured models |
| `phil config` | The effective settings, and where each came from |
| `phil permissions` | The rules for agents' shell commands |
| `phil memory` | What Phil remembers about this repository |

`phil --help` and `phil <command> --help` list every option.

## Learn more

- [User guide](https://github.com/ahochoy/phil/blob/main/docs/guide.md): configuration, routing,
  budgets, permissions, memory and every command in detail.
- [Changelog](https://github.com/ahochoy/phil/blob/main/CHANGELOG.md)
- [Contributing](https://github.com/ahochoy/phil/blob/main/CONTRIBUTING.md): development, tests
  and releases.
- [Issues](https://github.com/ahochoy/phil/issues)

## License

Apache-2.0. See [LICENSE](https://github.com/ahochoy/phil/blob/main/LICENSE).
```

Before writing it, check two things against `phil --help` and the guide:
- each command in the table, and its one-line description;
- that `phil diff` and `phil pr` take a run id as shown.

Correct the table if anything differs.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/release -n 0 -q`

Expected: all pass. That includes `test_dist.py`, because the README is in the sdist.

- [ ] **Step 7: Check that no content was lost**

Run: `git show HEAD:README.md | grep -c '' ; grep -c '' README.md docs/guide.md CONTRIBUTING.md`

Expected:
- the old README is 743 lines;
- `docs/guide.md` is about 675 lines (667 moved plus the header);
- `CONTRIBUTING.md` is about 120 lines.

Next, run `git show HEAD:README.md | grep '^##'` and compare it with `grep -h '^##' docs/guide.md CONTRIBUTING.md`. Every old heading must appear in one of the two files, except `## Development`, which became CONTRIBUTING's Development and Tests sections.

- [ ] **Step 8: Run the full suite and commit**

Run: `uv run pytest -q > .superpowers/task4-suite.txt 2>&1; tail -3 .superpowers/task4-suite.txt`

Expected: all passed.

Write `.git/phil-commit-msg.txt`:

```
Docs: a short README for PyPI, a user guide, and CONTRIBUTING

The README was 746 lines. It is also the PyPI page, so it becomes the
short version: what Phil is, how to install it (uv tool install
phil-agent, or pipx), setup, a first goal and a commands table. Its
links are absolute, because relative ones break on PyPI, and a test
keeps them that way.

The full reference moves intact to docs/guide.md. Development, tests,
the benchmark and a new Releasing section, with the trusted-publisher
setup, move to CONTRIBUTING.md.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
```

Run: `git add README.md docs/guide.md CONTRIBUTING.md tests/release/test_readme.py && git commit -F .git/phil-commit-msg.txt`

---

### Task 5: The release workflow

**Files:**
- Modify: `.github/workflows/tests.yml` (the `on:` and `concurrency:` blocks)
- Create: `.github/workflows/release.yml`
- Create: `tests/release/test_workflows.py`
- Modify: `tests/cli/test_keys_command.py` (one test added)

**Interfaces:**
- Consumes:
  - `release_check.py` commands `check-tag`, `notes`, `dev-version`, `version`, `set-version`, `check-sdist` and `fetch-wheel` (Task 2);
  - `CHANGELOG.md` (Task 3);
  - the files `phil_agent-<version>-py3-none-any.whl` and `phil_agent-<version>.tar.gz` (Tasks 1 and 3).
- Produces: the workflow the user runs for the rehearsal, and the `pypi` and `testpypi` environment names the one-time setup uses.

- [ ] **Step 1: Write the failing tests**

Create `tests/release/test_workflows.py`:

```python
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


def test_every_action_is_pinned_to_a_commit():
    uses = re.findall(r"uses: ([^\s]+)", RELEASE)
    external = [u for u in uses if not u.startswith("./")]
    assert external
    assert [u for u in external if not re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", u)] == []
```

Add to `tests/cli/test_keys_command.py` (after `test_keys_list_prints_a_pending_source_like_any_other`):

```python
def test_keys_list_without_a_key_store_reports_missing(git_repo, monkeypatch):
    # The release smoke test runs `phil keys list` on CI runners, which, like headless Linux
    # machines, may have no usable key store (spec 2026-10-18 §5.2).
    import keyring
    from keyring.backends.fail import Keyring as FailKeyring

    wide(monkeypatch)
    (git_repo / "phil.toml").write_text('[models]\nhigh = "openai:gpt"\nlow = "openai:gpt"\n')
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    previous = keyring.get_keyring()
    keyring.set_keyring(FailKeyring())
    try:
        result = runner.invoke(cli.app, ["--repo", str(git_repo), "keys", "list"])
    finally:
        keyring.set_keyring(previous)
    assert result.exit_code == 0, result.output
    assert "openai  OPENAI_API_KEY  missing" in result.output.splitlines()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/release/test_workflows.py tests/cli/test_keys_command.py -n 0 -q`

Expected:
- collection ERROR in `test_workflows.py`: `FileNotFoundError: release.yml`.
- `test_keys_list_without_a_key_store_reports_missing` PASSES. It pins behavior that already holds: `_keychain_get` turns any keyring error into None. If it fails instead, that is the user-facing bug the spec says to fix. Fix it in `src/phil/key_store.py` before going on.

- [ ] **Step 3: Make `tests.yml` callable**

In `.github/workflows/tests.yml`, replace:

```yaml
on:
  pull_request:
    branches: [main]
  push:
    branches: [main]
```

with:

```yaml
on:
  pull_request:
    branches: [main]
  push:
    branches: [main]
  # The release workflow runs this same suite before it builds anything.
  workflow_call:
```

And replace:

```yaml
concurrency:
  group: tests-${{ github.ref }}
  cancel-in-progress: true
```

with:

```yaml
# The workflow name keeps a release's call to this suite out of the group a push to main uses.
concurrency:
  group: tests-${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true
```

Then update the comment above `on:` to read: `# Phil's offline test suite on every pull request, every push to main, and every release.`

- [ ] **Step 4: Write `release.yml`**

Create `.github/workflows/release.yml`:

```yaml
name: release

# A pushed vX.Y.Z tag publishes X.Y.Z to PyPI. Running this by hand publishes X.Y.Z.dev<run number>
# to TestPyPI as a rehearsal. Nothing is published unless every check before it passes. See
# CONTRIBUTING.md, "Releasing", for the one-time trusted-publisher setup.
on:
  push:
    tags: ["v*"]
  workflow_dispatch:

permissions:
  contents: read

# One release at a time, and never cancel one partway through publishing.
concurrency:
  group: release
  cancel-in-progress: false

jobs:
  check:
    runs-on: ubuntu-latest
    outputs:
      version: ${{ steps.version.outputs.version }}
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
      - uses: astral-sh/setup-uv@c18668ad3cf93ea998bef934396af7bb5c839dc7 # v10.2.0
        with:
          python-version: "3.14"

      - name: Tag matches the version
        if: github.event_name == 'push'
        run: uv run --no-project python .github/scripts/release_check.py check-tag "$GITHUB_REF_NAME"

      - name: Changelog has the version's section
        run: |
          base="$(uv run --no-project python .github/scripts/release_check.py version)"
          uv run --no-project python .github/scripts/release_check.py notes "$base" > /dev/null

      - name: Version to publish
        id: version
        run: |
          if [ "$GITHUB_EVENT_NAME" = push ]; then
            version="$(uv run --no-project python .github/scripts/release_check.py version)"
          else
            version="$(uv run --no-project python .github/scripts/release_check.py dev-version "$GITHUB_RUN_NUMBER")"
          fi
          echo "version=$version" >> "$GITHUB_OUTPUT"
          echo "Publishing $version" >> "$GITHUB_STEP_SUMMARY"

  tests:
    needs: check
    uses: ./.github/workflows/tests.yml

  build:
    needs: [check, tests]
    runs-on: ubuntu-latest
    env:
      VERSION: ${{ needs.check.outputs.version }}
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
      - uses: astral-sh/setup-uv@c18668ad3cf93ea998bef934396af7bb5c839dc7 # v10.2.0
        with:
          python-version: "3.14"

      # Rehearsals publish a dev version; this edits only this checkout, never a commit.
      - name: Set the dev version
        if: github.event_name == 'workflow_dispatch'
        run: uv run --no-project python .github/scripts/release_check.py set-version "$VERSION"

      - name: Build
        run: uv build --sdist --wheel --out-dir dist

      - name: Check the files
        run: |
          ls -l dist
          test -f "dist/phil_agent-$VERSION-py3-none-any.whl"
          test -f "dist/phil_agent-$VERSION.tar.gz"
          uvx twine check --strict dist/*
          uv run --no-project python .github/scripts/release_check.py check-sdist "dist/phil_agent-$VERSION.tar.gz"

      - uses: actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9 # v7.0.2
        with:
          name: dist
          path: dist/
          if-no-files-found: error

  smoke:
    needs: [check, build]
    name: smoke (${{ matrix.os }})
    runs-on: ${{ matrix.os }}
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, macos-latest, windows-latest]
    env:
      VERSION: ${{ needs.check.outputs.version }}
    defaults:
      run:
        shell: bash
    steps:
      - uses: astral-sh/setup-uv@c18668ad3cf93ea998bef934396af7bb5c839dc7 # v10.2.0
        with:
          python-version: "3.14"
      - uses: actions/download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333 # v8.0.2
        with:
          name: dist
          path: dist

      - name: Install the wheel as a tool
        run: |
          uv tool install --python 3.14 "dist/phil_agent-$VERSION-py3-none-any.whl"
          uv tool dir --bin >> "$GITHUB_PATH"

      # Outside any repository, with an empty Phil home: what a new user's first commands see.
      - name: Run it
        run: |
          export PHIL_HOME="$RUNNER_TEMP/phil-home"
          cd "$RUNNER_TEMP"
          test "$(phil --version)" = "$VERSION"
          phil --help > /dev/null
          phil keys list

  publish:
    needs: [check, smoke]
    runs-on: ubuntu-latest
    environment:
      name: ${{ github.event_name == 'push' && 'pypi' || 'testpypi' }}
      url: ${{ github.event_name == 'push' && 'https://pypi.org/p/phil-agent' || 'https://test.pypi.org/p/phil-agent' }}
    permissions:
      contents: read
      id-token: write
    steps:
      - uses: astral-sh/setup-uv@c18668ad3cf93ea998bef934396af7bb5c839dc7 # v10.2.0
      - uses: actions/download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333 # v8.0.2
        with:
          name: dist
          path: dist

      - name: Publish to PyPI
        if: github.event_name == 'push'
        run: uv publish --trusted-publishing always dist/*

      - name: Publish to TestPyPI
        if: github.event_name == 'workflow_dispatch'
        run: uv publish --trusted-publishing always --publish-url https://test.pypi.org/legacy/ dist/*

  verify:
    needs: [check, publish]
    runs-on: ubuntu-latest
    env:
      VERSION: ${{ needs.check.outputs.version }}
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
      - uses: astral-sh/setup-uv@c18668ad3cf93ea998bef934396af7bb5c839dc7 # v10.2.0
        with:
          python-version: "3.14"

      - name: Install from PyPI
        if: github.event_name == 'push'
        run: |
          for attempt in $(seq 1 20); do
            uv tool install --python 3.14 --refresh "phil-agent==$VERSION" && break
            [ "$attempt" = 20 ] && exit 1
            sleep 15
          done

      # Only the wheel comes from TestPyPI; its dependencies come from PyPI, so a look-alike
      # package on TestPyPI can't be pulled in.
      - name: Install from TestPyPI
        if: github.event_name == 'workflow_dispatch'
        run: |
          mkdir -p "$RUNNER_TEMP/wheel"
          wheel="$(uv run --no-project python .github/scripts/release_check.py fetch-wheel \
            "https://test.pypi.org/pypi/phil-agent/$VERSION/json" "$RUNNER_TEMP/wheel")"
          uv tool install --python 3.14 "$wheel"

      - name: Put uv's tool directory on PATH
        run: uv tool dir --bin >> "$GITHUB_PATH"

      - name: Run it
        run: |
          export PHIL_HOME="$RUNNER_TEMP/phil-home"
          test "$(phil --version)" = "$VERSION"

  github-release:
    needs: [check, verify]
    if: github.event_name == 'push'
    runs-on: ubuntu-latest
    permissions:
      contents: write
    env:
      VERSION: ${{ needs.check.outputs.version }}
      GH_TOKEN: ${{ github.token }}
    steps:
      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1
      - uses: astral-sh/setup-uv@c18668ad3cf93ea998bef934396af7bb5c839dc7 # v10.2.0
        with:
          python-version: "3.14"
      - uses: actions/download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333 # v8.0.2
        with:
          name: dist
          path: dist

      - name: Create the release
        run: |
          uv run --no-project python .github/scripts/release_check.py notes "$VERSION" > "$RUNNER_TEMP/notes.md"
          gh release create "$GITHUB_REF_NAME" dist/* --title "Phil $VERSION" --notes-file "$RUNNER_TEMP/notes.md" --verify-tag
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/release tests/cli/test_keys_command.py -n 0 -q`

Expected: all pass.

- [ ] **Step 6: Lint the workflows**

Run: `actionlint .github/workflows/release.yml .github/workflows/tests.yml`

Expected: no output, exit 0.

If actionlint reports shellcheck findings in a `run:` block, fix them in the YAML. A finding that is a false positive gets a `# shellcheck disable=SCxxxx` line with a reason, inside that `run:` block.

- [ ] **Step 7: Run the full suite and commit**

Run: `uv run pytest -q > .superpowers/task5-suite.txt 2>&1; tail -3 .superpowers/task5-suite.txt`

Expected: all passed.

Write `.git/phil-commit-msg.txt`:

```
Release: a tag-driven workflow with a TestPyPI rehearsal

Pushing vX.Y.Z publishes X.Y.Z to PyPI. Running the workflow by hand
publishes X.Y.Z.dev<run number> to TestPyPI. Before anything is
published, the workflow:
- checks the tag against pyproject.toml and the changelog;
- reruns the full 3-OS test suite through workflow_call;
- builds and runs twine check, and holds the sdist under 2 MB;
- installs the wheel as a tool on Linux, macOS and Windows and runs
  phil --version, --help and keys list from an empty Phil home.

Publishing uses trusted publishing. Only the publish job can mint an
OIDC token, and it runs in the pypi or testpypi environment. The
workflow then installs the published version back from the index and,
for tags, creates the GitHub Release from the changelog section.

The test suite's concurrency group now includes the calling workflow's
name, so a manual release on main no longer cancels a push to main's
test run, or the other way round.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
```

Run: `git add .github/workflows/tests.yml .github/workflows/release.yml tests/release/test_workflows.py tests/cli/test_keys_command.py && git commit -F .git/phil-commit-msg.txt`

---

## After the plan: the rehearsal (the user)

This isn't an executor task. It needs the user's accounts and the branch merged to main, because PyPI and TestPyPI trust only the workflow file on the repository's default branch.

1. Do the one-time setup in `CONTRIBUTING.md` → Releasing → One-time setup.
2. Merge the PR.
3. Go to Actions → release → Run workflow on `main`.
4. Check that every job is green and that `https://test.pypi.org/p/phil-agent` lists `0.1.0.dev<N>`.
5. On your machine, follow "Rehearsing on TestPyPI" in `CONTRIBUTING.md`: `phil --version` prints `0.1.0.dev<N>`, and `phil setup` runs.
