"""N2: the `policy=None` fallbacks carry no deny rules, so every production caller must pass the
rules in effect. This scans the source for each call and checks it passes `policy=`."""

import re
from pathlib import Path

import pytest

from tests.run.conftest import calc_repo, make_harness  # noqa: F401  (fixtures)

SRC = Path(__file__).resolve().parents[2] / "src" / "phil"

# Functions whose `policy=None` fallback has no deny rules, and the file each is defined in.
FALLBACKS = {
    "make_shell_tool": "agents/tools.py",
    "launch_problems": "chat/approval.py",
    "test_cmd_problem": "chat/approval.py",
    "check_cmd_problems": "chat/approval.py",
}


def _calls(name: str) -> list[tuple[Path, str]]:
    """Every call of `name` in src (not its definition), as (file, the call's text)."""
    found = []
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(rf"(?<![\w.]){name}\(", text):
            if text[max(0, match.start() - 4) : match.start()] == "def ":
                continue
            depth, end = 0, match.end() - 1
            for end in range(match.end() - 1, len(text)):
                depth += {"(": 1, ")": -1}.get(text[end], 0)
                if depth == 0:
                    break
            found.append((path, text[match.start() : end + 1]))
    return found


@pytest.mark.parametrize("name", sorted(FALLBACKS))
def test_every_production_call_passes_a_policy(name):
    calls = _calls(name)
    assert calls, f"no call of {name} found"
    for path, call in calls:
        if path.relative_to(SRC).as_posix() == FALLBACKS[name] and "policy=policy" in call:
            continue  # the function's own internal delegation passes its argument through
        assert "policy=" in call, f"{path.relative_to(SRC)}: {call!r} passes no policy"


def test_the_engines_agents_get_a_policy_at_runtime(make_harness, monkeypatch):
    # F12: beyond the source text, the shell tool the engine's context leads to is built with the rules.
    import phil.agents.invoke as invoke_module
    from tests.run.conftest import write_red

    real = invoke_module._shell_for
    policies = []

    def spy(*args, **kwargs):
        policies.append(kwargs.get("policy"))
        return real(*args, **kwargs)

    monkeypatch.setattr(invoke_module, "_shell_for", spy)

    def asks(turn):
        assert turn.tools["run_shell"]("gh pr merge 42").startswith("DENIED:")
        return write_red(turn)

    harness = make_harness({"implementer": [asks]})
    assert harness.start()["__interrupt__"][0].value["reason"] == "approval"
    assert policies and all(policy is not None for policy in policies)


def test_the_engine_gives_agents_the_rules():
    engine = (SRC / "run" / "engine.py").read_text(encoding="utf-8")
    [context] = re.findall(r"return AgentContext\((.*?)\n        \)", engine, re.S)
    assert "repo_root=self.deps.repo_root" in context and "paths=self.deps.paths" in context
    worker = (SRC / "run" / "worker.py").read_text(encoding="utf-8")
    assert "paths=paths" in worker
