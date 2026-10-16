"""Learning at the end of a run (spec 2026-10-15 §4): the memory agent reads what the run's agents
reported and proposes changes to Phil's memory; code decides what is written."""

import json
from pathlib import Path

from phil.agents.invoke import AgentContext, invoke_agent
from phil.agents.registry import get_spec
from phil.contracts.memory import MemoryInput, MemoryUpdate
from phil.memory.agents_md import add_suggestions, read_suggestions
from phil.memory.apply import apply_update, memory_line
from phil.memory.context import read_agent_file
from phil.memory.store import read_memory, update_memory
from phil.packets import build_packet
from phil.publish.pr_body import newest_output
from phil.store.paths import ProjectPaths

MAX_INPUT_BYTES = 6 * 1024


class MemoryNotUpdated(Exception):
    pass


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _size(*lists: list[str]) -> int:
    return sum(len(item.encode("utf-8")) for items in lists for item in items)


def _evidence_line(claim: dict) -> str | None:
    """A command and what the agent concluded from it. The command's raw output is left out: it can
    carry text the repository controls, and what the memory agent writes is reused by later runs."""
    command = claim.get("command")
    if not isinstance(command, str) or not command.strip():
        return None
    return f"`{command}`: {claim.get('statement', '')}"


def collect_inputs(run_dir: Path) -> tuple[list[str], list[str], list[str]]:
    """(notes, evidence, review issues) from a run's recorded agent outputs, each deduplicated and
    together trimmed from the end to at most 6 KB."""
    notes: list[str] = []
    evidence: list[str] = []
    outputs = run_dir / "outputs"
    paths = sorted(outputs.glob("*.json")) if outputs.is_dir() else []
    for path in paths:
        if path.name.endswith(".rejected.json"):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        check = data.get("self_check") if isinstance(data, dict) else None
        if not isinstance(check, dict):
            continue
        for note in check.get("memory_notes") or []:
            if isinstance(note, str) and note.strip():
                notes.append(note.strip())
        for claim in check.get("evidence") or []:
            if isinstance(claim, dict) and (line := _evidence_line(claim)):
                evidence.append(line)

    issues: list[str] = []
    try:
        review = newest_output(run_dir, "review")
    except (OSError, ValueError):
        review = None
    if isinstance(review, dict):
        for issue in review.get("issues") or []:
            note = issue.get("note") if isinstance(issue, dict) else None
            if isinstance(note, str) and note.strip():
                issues.append(note.strip())

    notes, evidence, issues = _dedupe(notes), _dedupe(evidence), _dedupe(issues)
    # Over the cap, drop from the end: the review issues first, then evidence, then notes.
    for items in (issues, evidence, notes):
        while items and _size(notes, evidence, issues) > MAX_INPUT_BYTES:
            items.pop()
    return notes, evidence, issues


def learn_from_run(
    ctx: AgentContext, *, run_dir: Path, repo_root: Path, paths: ProjectPaths, test_cmd: str
) -> str | None:
    """Update the memory from a finished run; returns the one-line summary of the change, or None.
    Nothing happens (and no model is called) when the run reported no notes and ran no commands."""
    notes, evidence, issues = collect_inputs(run_dir)
    if not notes and not evidence:
        return None
    doc = read_memory(paths)
    if not doc.recognised:
        raise MemoryNotUpdated("memory.md has none of Phil's sections; not updated")
    _, agent_text = read_agent_file(repo_root)
    contract = MemoryInput(
        memory=doc.annotated(), agent_file=agent_text, test_cmd=test_cmd,
        notes=notes, evidence=evidence, review_issues=issues,
    )
    packet = build_packet("memory", contract, budget_tokens=ctx.config.budget_for("memory").max_input_tokens)
    # A config without a memory model (a legacy per-role one has no low tier) uses the orchestrator's.
    model = ctx.config.model_for("orchestrator") if ctx.config.missing_models(("memory",)) else None
    out = invoke_agent(get_spec("memory"), packet, ctx, node="memory", model=model)
    assert isinstance(out, MemoryUpdate)
    applied = update_memory(
        paths, lambda d: apply_update(d, out, agent_file=agent_text, pending=read_suggestions(paths))
    )
    add_suggestions(paths, applied.suggestions)
    return memory_line(applied)
