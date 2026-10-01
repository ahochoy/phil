"""The benchmark's small goals: each names a fixture repo, a goal and a check on the run's worktree."""

import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"
CHECK_TIMEOUT_S = 120


@dataclass(frozen=True)
class Case:
    name: str
    fixture: str
    goal: str
    # Recorded next to the plan's actual modes, never asserted: modes arrive with a later task.
    expect_modes: tuple[str, ...]
    passed: Callable[[Path], bool]
    # Per-fixture phil.toml overrides, merged over the baseline and the benchmark config.
    config: dict = field(default_factory=dict)
    # What the router should pick (spec §5.2): "quick", "full" or "answer". Asserted in test_bench.
    expect_depth: str = "full"
    # Answer cases only: a repo-relative path the answer's `files` must include.
    expect_file: str | None = None


def _exits_zero(command: list[str], cwd: Path) -> bool:
    try:
        result = subprocess.run(
            command, cwd=cwd, capture_output=True, text=True, timeout=CHECK_TIMEOUT_S, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _py_multiply(tree: Path) -> bool:
    module = tree / "calc" / "__init__.py"
    if not module.is_file() or not re.search(r"^def multiply\(", module.read_text(), re.MULTILINE):
        return False
    return _exits_zero([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], tree)


def _site_meta_tag(tree: Path) -> bool:
    # Plain `node`, not `npm run build`: the fixture has no dependencies and npm may be absent.
    if not _exits_zero(["node", "build.mjs"], tree):
        return False
    built = tree / "dist" / "index.html"
    return built.is_file() and 'name="easter-egg"' in built.read_text()


def _site_typo(tree: Path) -> bool:
    # A word match: the fix, "Welcome", contains "Welcom".
    src = tree / "src"
    return src.is_dir() and not any(
        re.search(r"\bWelcom\b", path.read_text(errors="replace")) for path in src.rglob("*") if path.is_file()
    )


def _explain_module(root: Path) -> bool:
    """No run, no commits: `root`'s history still ends at `_init_repo`'s last commit."""
    result = subprocess.run(
        ["git", "-C", str(root), "log", "-1", "--format=%s"], capture_output=True, text=True, timeout=10, check=False
    )
    return result.returncode == 0 and result.stdout.strip() == "Configure phil"


# The site has no test script: its build is the check, as a user would configure in their own site repo.
SITE_CONFIG = {"project": {"test_cmd": "node build.mjs"}}

CASES: list[Case] = [
    Case(
        "py-multiply", "py-calc", "Add a multiply(a, b) function to calc.", ("tdd",), _py_multiply,
        expect_depth="quick",
    ),
    Case(
        "site-meta-tag",
        "static-site",
        'Add a hidden <meta name="easter-egg" content="hello world"> to the page head.',
        ("check",),
        _site_meta_tag,
        SITE_CONFIG,
        expect_depth="quick",
    ),
    Case(
        "site-typo", "static-site", "Fix the typo 'Welcom' in the page heading.", ("check",), _site_typo, SITE_CONFIG,
        expect_depth="quick",
    ),
    # The fixture's module is a package (`calc/__init__.py`), not a bare `calc.py`; `divide` lives there.
    Case(
        "explain-module", "py-calc", "What does calc.divide do when the divisor is zero?", (), _explain_module,
        expect_depth="answer", expect_file="calc/__init__.py",
    ),
]
