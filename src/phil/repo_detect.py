"""Propose a repo's test and setup commands from its files when neither the plan nor phil.toml sets them."""

import json
from pathlib import Path

from phil.config import PhilConfig

PYTHON_MARKERS = ("pyproject.toml", "pytest.ini", "conftest.py")
# `npm init` writes this script: it fails on purpose and runs no tests.
NPM_PLACEHOLDER = 'echo "Error: no test specified" && exit 1'
# Lockfile -> install command, checked in this order. No lockfile means no setup command: Phil
# never runs a plain `npm install` on its own.
LOCKFILE_SETUP_CMDS = (
    ("pnpm-lock.yaml", "pnpm install --frozen-lockfile"),
    ("yarn.lock", "yarn install --frozen-lockfile"),
    ("bun.lockb", "bun install --frozen-lockfile"),
    ("bun.lock", "bun install --frozen-lockfile"),
    ("package-lock.json", "npm ci"),
)


def _npm_test_script(root: Path) -> bool:
    try:
        data = json.loads((root / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    scripts = data.get("scripts") if isinstance(data, dict) else None
    script = scripts.get("test") if isinstance(scripts, dict) else None
    return isinstance(script, str) and bool(script.strip()) and script.strip() != NPM_PLACEHOLDER


def detect_test_cmd(root: Path) -> str | None:
    """The test command `root`'s files suggest, or None.

    In order: a package.json `test` script gives `npm test`; Python markers give `uv run pytest`
    when uv.lock exists, else `pytest`; go.mod gives `go test ./...`; Cargo.toml gives `cargo test`.
    """
    if (root / "package.json").is_file() and _npm_test_script(root):
        return "npm test"
    if any((root / marker).is_file() for marker in PYTHON_MARKERS):
        return "uv run pytest" if (root / "uv.lock").is_file() else "pytest"
    if (root / "go.mod").is_file():
        return "go test ./..."
    if (root / "Cargo.toml").is_file():
        return "cargo test"
    return None


def detect_setup_cmd(root: Path) -> str | None:
    """The dependency-install command `root`'s lockfile suggests, or None.

    Only applies when `root/package.json` exists. In order: `pnpm-lock.yaml`, `yarn.lock`,
    `bun.lockb` or `bun.lock`, then `package-lock.json`. No lockfile gives None."""
    if not (root / "package.json").is_file():
        return None
    for lockfile, cmd in LOCKFILE_SETUP_CMDS:
        if (root / lockfile).is_file():
            return cmd
    return None


def effective_setup_cmd(config: PhilConfig, root: Path | None = None) -> tuple[str | None, str]:
    """The setup command a run uses and where it came from: "config", "detected" or "none".

    `config.project.setup_cmd` of `None` means detect from `root`'s lockfile (when `root` is
    given); `""` means no setup, explicitly. It lives here, not in the chat, because the run
    engine uses it too (on the run's worktree)."""
    if config.project.setup_cmd is not None:
        return (config.project.setup_cmd, "config") if config.project.setup_cmd else (None, "none")
    detected = detect_setup_cmd(root) if root is not None else None
    if detected:
        return detected, "detected"
    return None, "none"
