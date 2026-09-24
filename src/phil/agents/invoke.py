import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from phil.agents.evidence import check_evidence
from phil.agents.spec import AgentSpec
from phil.agents.tools import CommandLog, make_shell_tool
from phil.agents.usage import extract_usage
from phil.config import PhilConfig
from phil.contracts import Contract, Ref
from phil.packets import Packet
from phil.store.artifacts import ArtifactStore, artifact_name
from phil.store.parked import park
from phil.store.telemetry import TelemetryRow, record

AgentFactory = Callable[[AgentSpec, str, Path | None, list[Callable[..., str]]], Any]


@dataclass
class AgentContext:
    config: PhilConfig
    conn: sqlite3.Connection
    layer: Literal["chat", "run"]
    run_id: str | None = None
    artifacts: ArtifactStore | None = None
    workdir: Path | None = None
    factory: AgentFactory | None = None
    sleep: Callable[[float], None] = time.sleep


class ContractViolation(Exception):
    def __init__(self, agent: str, problems: list[str]) -> None:
        super().__init__(agent, problems)
        self.agent = agent
        self.problems = problems

    def __str__(self) -> str:
        return f"{self.agent} returned invalid output: {'; '.join(self.problems)}"


def _resolve_factory(ctx: AgentContext) -> AgentFactory:
    if ctx.factory is not None:
        return ctx.factory
    from phil.agents.factory import build_deep_agent

    return build_deep_agent


def _validate(spec: AgentSpec, raw: object) -> tuple[Contract | None, list[str]]:
    if raw is None:
        return None, ["no structured output was returned"]
    try:
        if isinstance(raw, spec.out_contract):
            return raw, []
        data = raw.model_dump() if isinstance(raw, BaseModel) else raw
        return spec.out_contract.model_validate(data), []
    except ValidationError as exc:
        problems = [f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}" for err in exc.errors()]
        return None, problems


def _retry_message(problems: list[str]) -> dict[str, str]:
    listed = "\n".join(f"- {problem}" for problem in problems)
    return {
        "role": "user",
        "content": f"Your previous output was rejected. Fix these problems and return the full output again:\n{listed}",
    }


def invoke_agent(
    spec: AgentSpec,
    packet: Packet,
    ctx: AgentContext,
    *,
    node: str,
    task_id: str | None = None,
) -> Contract:
    model = ctx.config.model_for(spec.role)
    log = CommandLog()
    tools: list[Callable[..., str]] = []
    if "shell" in spec.tools and ctx.workdir is not None:
        tools.append(make_shell_tool(ctx.workdir, ctx.config.shell, log, ctx.artifacts))
    agent = _resolve_factory(ctx)(spec, model, ctx.workdir, tools)

    messages: list[dict[str, str]] = [{"role": "user", "content": packet.render()}]
    problems: list[str] = []
    for attempt in (1, 2):
        name = artifact_name(node, task_id, attempt)
        if ctx.artifacts is not None:
            ctx.artifacts.write("packets", name, packet)
        payload_messages = messages if attempt == 1 else [*messages, _retry_message(problems)]
        started = time.monotonic()
        result = agent.invoke({"messages": payload_messages})  # Task 11: provider retries
        latency_ms = int((time.monotonic() - started) * 1000)
        output, problems = _validate(spec, result.get("structured_response"))
        outcome = "ok" if not problems else "invalid"
        if output is not None:
            problems = check_evidence(output, commands=log.commands, workdir=ctx.workdir)
            if problems:
                outcome = "evidence_fail"
        usage = extract_usage(result.get("messages", []))
        record(
            ctx.conn,
            TelemetryRow(
                run_id=ctx.run_id,
                layer=ctx.layer,
                node=node,
                role=spec.role,
                model=model,
                attempt=attempt,
                packet_tokens=packet.tokens,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                latency_ms=latency_ms,
                cost_usd=usage.cost_usd,
                outcome=outcome,
            ),
        )
        if output is not None and not problems:
            output_path = ""
            if ctx.artifacts is not None:
                output_path = str(ctx.artifacts.write("outputs", name, output))
            _record_self_check(output, ctx, spec=spec, node=node, task_id=task_id, output_path=output_path)
            return output
    raise ContractViolation(spec.name, problems)


def _record_self_check(
    output: Contract, ctx: AgentContext, *, spec: AgentSpec, node: str, task_id: str | None, output_path: str
) -> None:
    self_check = getattr(output, "self_check", None)
    if self_check is None:
        return
    if ctx.artifacts is not None and self_check.assumptions:
        ctx.artifacts.append_assumptions(node=node, task_id=task_id, assumptions=self_check.assumptions)
    for note in self_check.out_of_scope:
        park(
            ctx.conn,
            raised_by=spec.name,
            note=note,
            why_not_now=f"out of scope for {node}",
            source=Ref(label=f"{node} output", path=output_path),
            run_id=ctx.run_id,
        )
