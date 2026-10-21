# Phil: Packaging and Release Pipeline

**Status:** approved in conversation, 2026-10-18. This is the packaging part of release item 4 (release work). The other release-work items are separate: diagnostics kept out of users' terminals, and confirming bring-your-own-provider. MCP comes after launch.

## 1. Goal

Anyone can install Phil with one command:

```
uv tool install phil-agent
```

That installs the `phil` command. A release is a pushed `v*` tag. CI then tests, builds, smoke-tests and publishes the release to PyPI, with no manual upload and no stored API token.

This spec covers the pipeline and a TestPyPI rehearsal of it. The real PyPI publish is out of scope. It happens later, when the user pushes `v0.1.0` after the other release items are done.

**Success:** a manual run of the release workflow publishes a dev build to TestPyPI. On a clean machine, `uv tool install` from TestPyPI gives a working `phil --version` and `phil setup`.

## 2. Decisions (user, 2026-10-18)

| Topic | Decision |
|---|---|
| Distribution name | `phil-agent`, because `phil` is taken on PyPI. The command stays `phil`, and the import package stays `phil`. |
| Python | The floor stays at 3.14. Docs lead with `uv tool install phil-agent`, which fetches 3.14 if needed. `pipx install phil-agent` is the alternative. |
| README | Split it into three: a short user README (also the PyPI page), `docs/guide.md` (the full user reference) and `CONTRIBUTING.md`. |
| Releases | Tag-driven. The version lives in `pyproject.toml` only, and a `vX.Y.Z` tag must match it. |
| Scope | The pipeline plus a TestPyPI rehearsal. The real publish comes later. |
| Not in scope | Homebrew, standalone binaries, lowering the Python floor. |

## 3. Package metadata and contents

### 3.1 `pyproject.toml`

- `name = "phil-agent"`, `version = "0.1.0"`.
- New metadata:
  - `description`: one line saying Phil is a terminal coding agent.
  - `readme = "README.md"`.
  - `license = "Apache-2.0"` (SPDX form) and `license-files = ["LICENSE"]`.
  - `authors`.
  - `keywords`: coding agent, LLM, CLI, LangGraph.
  - `classifiers`: Development Status 3 (Alpha), Environment Console, Python 3.14, Python 3 only, OS: macOS, POSIX Linux and Windows, Topic: Software Development.
  - `[project.urls]`: Homepage (the site), Repository, Issues, Changelog (`CHANGELOG.md` on GitHub).
- `requires-python` and `[project.scripts] phil = "phil.cli.main:app"` stay unchanged.

### 3.2 One version source

Today the version lives in two places: `pyproject.toml` and `__version__ = "0.1.0"` in `src/phil/__init__.py`. Separately, the banner calls `importlib.metadata.version("phil")` (`src/phil/cli/main.py`, about line 261), which would fall back to "dev" for every installed user once the distribution is renamed.

After the change:
- `pyproject.toml` is the only source.
- `src/phil/__init__.py` sets `__version__` from `importlib.metadata.version("phil-agent")`. If the distribution isn't installed (`PackageNotFoundError`), it falls back to `"0+unknown"`.
- `--version` and the banner both use `phil.__version__`. The banner's own `importlib.metadata` call is removed.

### 3.3 Wheel

The wheel's contents don't change: `src/phil` only, including `phil/prompts/*.md`, which `phil.agents.spec` reads with `importlib.resources`. A test pins this (see section 6).

### 3.4 Sdist

Today the sdist is 17 MB because it takes the whole repo: `site/`, `docs/`, `prototype/`, `.claude/`, `assets/` and more. An explicit include list fixes this:

- `src/`, `tests/`, `README.md`, `LICENSE`, `CHANGELOG.md`, `pyproject.toml`, `uv.lock`.

The sdist must be under 2 MB.

### 3.5 `CHANGELOG.md`

A new file at the repo root, in Keep a Changelog style, with a `## [0.1.0]` section describing the first release. Each release adds a `## [X.Y.Z]` section, and the release workflow requires it.

## 4. README split

### 4.1 `README.md` (user-facing, about 120 lines)

This is also the PyPI project page, so every link and image uses an absolute GitHub URL. Sections:

1. **Pitch:** what Phil is, in two or three sentences.
2. **Install:** `uv tool install phil-agent`, then `pipx install phil-agent` as the alternative, and a short Windows note.
3. **Set up:** `phil setup`.
4. **Your first goal:** one short example.
5. **Commands:** a table of the main commands.
6. **Learn more:** links to `docs/guide.md`, `CONTRIBUTING.md`, the site and `CHANGELOG.md`.
7. **License.**

### 4.2 `docs/guide.md`

The full user reference, moved from today's README with its wording intact:
- Configuration: Tiers, Providers, Keys, Checking your models.
- Usage, Routing, Pull requests and cleanup, Observability, Shell commands, Permissions, Plans, Test command, Dependencies, Memory.

Links inside it are fixed for its new location.

### 4.3 `CONTRIBUTING.md`

- The editable install (`uv tool install --editable . --force`).
- Running tests, and the `live` and `bench` markers. These are never run in CI.
- Benchmark, moved from the README.
- The note about `prototype/`.
- **Releasing** (new):
  - bump the version in `pyproject.toml`;
  - add a `CHANGELOG.md` section;
  - merge, then tag `vX.Y.Z` on main and push the tag;
  - how to run a TestPyPI rehearsal;
  - the one-time trusted-publisher setup (section 5.4).

## 5. Release workflow

### 5.1 `tests.yml`

It gains a `workflow_call` trigger, so the release workflow reuses the same 3-OS suite instead of copying it. Its `pull_request` and `push` triggers and its jobs are unchanged.

### 5.2 `release.yml`

**Triggers:**

| Trigger | Target | Version published |
|---|---|---|
| push of a tag matching `v*` | PyPI | the `pyproject.toml` version, which must equal the tag without its `v` |
| `workflow_dispatch` (manual) | TestPyPI | `X.Y.Z.dev<run number>`, so every rehearsal has a unique version |

**Jobs, in order:**

1. **check**
   - Tag runs: the tag equals `v` + the `pyproject.toml` version.
   - Every run: `CHANGELOG.md` has a section for that version.
   - Dispatch runs: computes the dev version.
   - The checks are small scripts in `.github/scripts/`.
2. **tests:** calls `tests.yml`.
3. **build**
   - For dispatch runs, it writes the dev version into `pyproject.toml` in the CI checkout only. Nothing is committed.
   - `uv build`, then `twine check` on both files, then a check that the sdist is under 2 MB.
   - It uploads `dist/` as an artifact.
4. **smoke:** on ubuntu, macOS and Windows:
   - `uv tool install` the built wheel;
   - with `PHIL_HOME` set to a temp dir, run `phil --version` (it must print the expected version), `phil --help` and `phil keys list`.
   - `phil keys list` loads the default config and asks the OS key store about each provider. CI runners, like headless Linux machines, may have no key store. There it must print `missing` for each key and exit 0. If it crashes instead, that's a bug users would hit too, and it gets fixed, not skipped.
5. **publish**
   - `uv publish` with trusted publishing (OIDC).
   - It runs in the GitHub environment `pypi` for tags and `testpypi` for dispatch runs.
   - Only this job has `id-token: write`. Every other job has read-only permissions.
6. **verify**
   - It installs the just-published version from the index on ubuntu and runs `phil --version`.
   - For TestPyPI it uses a temporary uv config that takes `phil-agent` from TestPyPI and its dependencies from PyPI, so a squatted dependency name on TestPyPI can't be pulled in.
   - It retries for a few minutes while the index catches up.
7. **github-release:** tag runs only. It creates a GitHub Release with that version's changelog section as notes and the wheel and sdist attached.

Actions are pinned to commit SHAs, as `tests.yml` already does.

### 5.3 What stops a release

Each of these fails the run before anything is published:
- tag and version mismatch;
- no changelog section;
- a red test suite;
- `twine check` errors;
- an sdist over 2 MB;
- a smoke failure on any OS.

### 5.4 One-time setup (the user)

1. **PyPI:** add a pending trusted publisher for `phil-agent`: this repo, workflow `release.yml`, environment `pypi`.
2. **TestPyPI:** the same, with environment `testpypi`.
3. **GitHub:** create the environments `pypi` and `testpypi`. Adding a required reviewer on `pypi` is recommended, so each real publish needs a click.

### 5.5 Rehearsal

1. Run `release.yml` manually from the Actions tab.
2. It publishes `0.1.0.dev<run number>` to TestPyPI and the verify job passes.
3. On a local machine, install that version from TestPyPI, with the same index pinning as the verify job, then run `phil --version` and `phil setup`.

## 6. Testing and verification

### 6.1 Unit tests

These are part of the normal suite, so they run on every PR:

- **Version:**
  - `phil.__version__` equals `importlib.metadata.version("phil-agent")` and the `pyproject.toml` version;
  - the banner shows that version, not "dev".
- **Wheel contents:**
  - build the wheel once per test session (`uv build --wheel` into a temp dir) and share it across the assertions;
  - it contains every `src/phil/prompts/*.md` and the `phil` console-script entry point;
  - it contains nothing from `tests/`, `site/` or `docs/`.
- **Sdist contents:** only the include list from section 3.4, and under 2 MB.
- **README:**
  - no relative links or images;
  - the install line names `phil-agent`.
- **Changelog:** `CHANGELOG.md` has a section for the current version. The release workflow enforces this too, but the test catches it at PR time.
- **Release scripts:** the `.github/scripts/` checks have their own tests: matching and mismatching tags, a missing changelog section, and the dev-version computation.

### 6.2 Workflow checks

- If `actionlint` is available locally, `release.yml` and `tests.yml` are linted with it before committing.
- Everything else in section 5.3 is enforced by the workflow itself.

### 6.3 Acceptance

The rehearsal in section 5.5 passes end to end.

## 7. Out of scope

- The real PyPI publish (the user's `v0.1.0` tag, later).
- Diagnostics out of users' terminals, and confirming bring-your-own-provider (the other release-work items).
- Homebrew and standalone binaries.
- Lowering the Python floor.
