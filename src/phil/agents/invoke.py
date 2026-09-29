import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from phil.agents.evidence import check_evidence
from phil.agents.pricing import PriceBook, default_price_book
from phil.agents.retry import call_with_retry
from phil.agents.spec import AgentSpec
from phil.agents.tools import CommandLog, make_shell_tool
from phil.agents.usage import extract_usage
from phil.config import PhilConfig
from phil.contracts import Contract, Ref
from phil.packets import Packet
from phil.store.artifacts import ArtifactStore, artifact_name
from phil.store.parked import park
from phil.store.telemetry import CallRow, CostSource, TelemetryRow, record, record_calls, weakest
from phil.workspace.shell import literal_pattern

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
    command_log: CommandLog | None = None
    extra_allow: tuple[str, ...] = ()
    chat_id: str | None = None
    prices: PriceBook | None = None  # None: the process-wide default book, created on first use


class ContractViolation(Exception):
    def __init__(self, agent: str, problems: list[str], rejected_path: str | None = None) -> None:
        super().__init__(agent, problems)
        self.agent = agent
        self.problems = problems
        self.rejected_path = rejected_path

    def __str__(self) -> str:
        return f"{self.agent} returned invalid output: {'; '.join(self.problems)}"


_default_prices: PriceBook | None = None
_default_prices_lock = threading.Lock()


def _price_book(ctx: AgentContext) -> PriceBook:
    """The context's price book, or a process-wide default created (and fetched) only when a
    model call actually needs an estimate."""
    global _default_prices
    if ctx.prices is not None:
        return ctx.prices
    with _default_prices_lock:
        if _default_prices is None:
            _default_prices = default_price_book()
        return _default_prices


@dataclass
class _Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    tool_calls: dict[str, int] = field(default_factory=dict)
    cost_source: CostSource = "reported"
    calls: list[CallRow] = field(default_factory=list)


def _pricing_models(configured: str, call_model: str | None) -> list[str]:
    """Model ids to price a call by: the call's own model when it belongs to the configured
    model's family (same provider and vendor, e.g. ``openrouter:openai/...``), then the
    configured model."""
    provider, sep, model_id = configured.partition(":")
    if call_model and sep and "/" in model_id and "/" in call_model:
        if call_model.split("/", 1)[0] == model_id.split("/", 1)[0]:
            candidate = f"{provider}:{call_model}"
            if candidate != configured:
                return [candidate, configured]
    return [configured]


def _estimate(
    ctx: AgentContext, configured: str, call_model: str | None, input_tokens: int, output_tokens: int
) -> float | None:
    prices = _price_book(ctx)
    for name in _pricing_models(configured, call_model):
        cost = prices.estimate(name, input_tokens, output_tokens)
        if cost is not None:
            return cost
    return None


def _usage(ctx: AgentContext, collector: Any, messages: list, configured: str) -> _Usage:
    """Callback totals win when the collector saw model calls; otherwise (scripted fakes) fall
    back to the usage on the returned messages."""
    tool_calls = dict(collector.tool_calls)
    if not collector.calls:
        usage = extract_usage(messages)
        return _Usage(usage.input_tokens, usage.output_tokens, usage.cost_usd, tool_calls)
    rows: list[CallRow] = []
    for model_call in collector.calls:
        source: CostSource
        if model_call.reported_cost is not None:
            cost, source = model_call.reported_cost, "reported"
        else:
            estimate = _estimate(ctx, configured, model_call.model, model_call.input_tokens, model_call.output_tokens)
            cost, source = (estimate, "estimated") if estimate is not None else (0.0, "unknown")
        rows.append(
            CallRow(
                model=model_call.model or configured,
                input_tokens=model_call.input_tokens,
                output_tokens=model_call.output_tokens,
                cost_usd=cost,
                cost_source=source,
            )
        )
    return _Usage(
        input_tokens=sum(row.input_tokens for row in rows),
        output_tokens=sum(row.output_tokens for row in rows),
        cost_usd=sum(row.cost_usd for row in rows),
        tool_calls=tool_calls,
        cost_source=weakest([row.cost_source for row in rows]),
        calls=rows,
    )


def _record_usage(ctx: AgentContext, row: TelemetryRow, usage: _Usage) -> None:
    telemetry_id = record(ctx.conn, row)
    if usage.calls:
        record_calls(ctx.conn, telemetry_id, usage.calls)


def _resolve_factory(ctx: AgentContext) -> AgentFactory:
    if ctx.factory is not None:
        return ctx.factory
    from phil.agents.factory import build_agent

    return build_agent


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


def _is_structured_output_parse_error(exc: BaseException) -> bool:
    return any(cls.__name__ == "StructuredOutputValidationError" for cls in type(exc).__mro__)


def _retry_message(problems: list[str]) -> dict[str, str]:
    listed = "\n".join(f"- {problem}" for problem in problems)
    return {
        "role": "user",
        "content": f"Your previous output was rejected. Fix these problems and return the full output again:\n{listed}",
    }


def _record_error(
    ctx: AgentContext,
    *,
    spec: AgentSpec,
    model: str,
    node: str,
    attempt: int,
    call: int,
    packet: Packet,
    started: float,
    usage: _Usage,
    retries: int,
) -> None:
    _record_usage(
        ctx,
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
            latency_ms=int((time.monotonic() - started) * 1000),
            cost_usd=usage.cost_usd,
            outcome="error",
            call=call,
            chat_id=ctx.chat_id,
            model_calls=len(usage.calls),
            tool_calls=usage.tool_calls,
            retries=retries,
            cost_source=usage.cost_source,
        ),
        usage,
    )


def invoke_agent(
    spec: AgentSpec,
    packet: Packet,
    ctx: AgentContext,
    *,
    node: str,
    task_id: str | None = None,
    call: int = 1,
) -> Contract:
    if "shell" in spec.tools and ctx.workdir is None:
        raise ValueError(f"{spec.name} needs a workdir for its shell tool")
    if packet.contract_type != spec.in_contract.__name__:
        raise ValueError(
            f"{spec.name} expects a {spec.in_contract.__name__} packet, got {packet.contract_type}"
        )
    model = ctx.config.model_for(spec.role)
    log = ctx.command_log if ctx.command_log is not None else CommandLog()
    shell = ctx.config.shell
    if ctx.extra_allow:
        shell = shell.model_copy(update={"allow": [*shell.allow, *(literal_pattern(cmd) for cmd in ctx.extra_allow)]})
    effective_node = node if call == 1 else f"{node}-c{call}"
    log_prefix = artifact_name(effective_node, task_id, 1)
    tools: list[Callable[..., str]] = []
    if "shell" in spec.tools and ctx.workdir is not None:
        tools.append(make_shell_tool(ctx.workdir, shell, log, ctx.artifacts, log_prefix=log_prefix))
    agent = _resolve_factory(ctx)(spec, model, ctx.workdir, tools)
    from phil.agents.collector import UsageCollector  # lazy: keeps langchain out of module import

    messages: list[dict[str, str]] = [{"role": "user", "content": packet.render()}]
    problems: list[str] = []
    last_rejected_path: str | None = None
    for attempt in (1, 2):
        name = artifact_name(effective_node, task_id, attempt)
        payload_messages = messages if attempt == 1 else [*messages, _retry_message(problems)]
        if ctx.artifacts is not None:
            ctx.artifacts.write("packets", name, packet)
            if attempt == 2:
                ctx.artifacts.write_json("packets", f"{name}.retry", {"messages": payload_messages})
        started = time.monotonic()
        parse_problems: list[str] | None = None
        collector = UsageCollector(ignore_tools={spec.out_contract.__name__})
        sleeps: list[float] = []

        def counting_sleep(delay: float) -> None:
            sleeps.append(delay)
            ctx.sleep(delay)

        try:
            result, retries = call_with_retry(
                agent, {"messages": payload_messages}, sleep=counting_sleep, config={"callbacks": [collector]}
            )
        except Exception as exc:
            retries = len(sleeps)  # each transient retry sleeps exactly once
            if not _is_structured_output_parse_error(exc):
                _record_error(
                    ctx,
                    spec=spec,
                    model=model,
                    node=node,
                    attempt=attempt,
                    call=call,
                    packet=packet,
                    started=started,
                    usage=_usage(ctx, collector, [], model),
                    retries=retries,
                )
                raise
            result = {}
            parse_problems = [f"structured output failed to parse: {exc}"]
        latency_ms = int((time.monotonic() - started) * 1000)
        if parse_problems is not None:
            output, problems = None, parse_problems
        else:
            output, problems = _validate(spec, result.get("structured_response"))
        outcome = "ok" if not problems else "invalid"
        if output is not None:
            problems = check_evidence(output, commands=log.commands, workdir=ctx.workdir)
            if problems:
                outcome = "evidence_fail"
        usage = _usage(ctx, collector, result.get("messages", []), model)
        _record_usage(
            ctx,
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
                call=call,
                chat_id=ctx.chat_id,
                model_calls=len(usage.calls),
                tool_calls=usage.tool_calls,
                retries=retries,
                cost_source=usage.cost_source,
            ),
            usage,
        )
        if problems and ctx.artifacts is not None:
            raw = result.get("structured_response")
            raw_data = raw.model_dump() if isinstance(raw, BaseModel) else raw
            last_rejected_path = str(
                ctx.artifacts.write_json("outputs", f"{name}.rejected", {"raw": raw_data, "problems": problems})
            )
        if output is not None and not problems:
            output_path = ""
            if ctx.artifacts is not None:
                output_path = str(ctx.artifacts.write("outputs", name, output))
            _record_self_check(output, ctx, spec=spec, node=node, task_id=task_id, output_path=output_path)
            return output
    raise ContractViolation(spec.name, problems, last_rejected_path)


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
