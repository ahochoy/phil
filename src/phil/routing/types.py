import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from phil.repo_detect import detect_test_cmd

RouteState = dict[str, object]
CHAT_TURNS = 4
TURN_CHARS = 400
FILES = 60


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int
    cost_usd: float | None = None


@dataclass(frozen=True)
class Judgement:
    task_class: str  # a CLASSES key
    probabilities: dict[str, float]  # one-hot for backends without a distribution
    confidence: float  # 0..1
    needs_detail: float  # 0..1, the probability the request must be clarified first
    source: Literal["jev", "llm", "fake"]
    latency_ms: int
    usage: Usage | None
    fallback_reason: str | None = None  # set on an llm judgement made because Jev failed


def _tracked_files(root: Path) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [line for line in result.stdout.splitlines() if line] if result.returncode == 0 else []


def route_state(request: str, chat: list[str], root: Path) -> RouteState:
    """The `state` a routing question sees (spec §3.2): the request, the last few chat turns
    (trimmed), and what Phil already knows about the repo."""
    files = _tracked_files(root)
    return {
        "request": request,
        "chat": [turn[:TURN_CHARS] for turn in chat[-CHAT_TURNS:]],
        "repo": {"test_cmd": detect_test_cmd(root), "files": files[:FILES], "file_count": len(files)},
    }
