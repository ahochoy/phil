"""`phil models check`: one tiny real call per configured model, through the agent path Phil uses."""

import json
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import Field

from phil.agents.invoke import AgentContext, AgentFactory, ContractViolation, invoke_agent
from phil.agents.providers import provider_for_model
from phil.agents.spec import AgentSpec
from phil.config import ROLES, TIERS, ConfigError, PhilConfig
from phil.contracts import Contract
from phil.packets import build_packet
from phil.store.artifacts import ArtifactStore
from phil.store.db import connect

CHECK_WORD = "pineapple"
EXCERPT_CHARS = 120
NO_STRUCTURED_OUTPUT = "no structured output was returned"


class ModelCheckInput(Contract):
    word: str = Field(description="The word to echo back.")


class ModelCheck(Contract):
    ok: bool = Field(description="Always true.")
    echo: str = Field(description="The input's word, exactly as given.")


# Lean, and without _shared.md: its self_check rules don't apply to this tiny contract. A text
# answer ends the check at once (one model call), reported with an excerpt of the text.
MODEL_CHECK = AgentSpec(
    "model_check", "orchestrator", ModelCheckInput, ModelCheck,
    harness="lean", shared_prompt=False, end_on_text=True,
)


@dataclass(frozen=True)
class CheckResult:
    label: str  # the tiers and `role:<name>` keys that use `model`, e.g. "high, low"
    model: str
    ok: bool
    seconds: float
    detail: str  # why it failed; empty when ok


def check_targets(config: PhilConfig) -> list[tuple[str, list[str]]]:
    """Each distinct model to check, with the labels that use it: every tier that has a model set,
    then every role key under [models] (which overrides its tier), in first-seen order."""
    labels: dict[str, list[str]] = {}
    for tier in TIERS:
        if tier in config.models:
            labels.setdefault(config.models[tier], []).append(tier)
    for role in ROLES:
        if role in config.models:
            labels.setdefault(config.models[role], []).append(f"role:{role}")
    return list(labels.items())


def _first_line(text: str) -> str:
    return text.strip().splitlines()[0] if text.strip() else ""


def _error_detail(exc: Exception) -> str:
    if isinstance(exc, ConfigError):  # Phil's own message (unknown provider, missing key) says it all
        return _first_line(str(exc))
    return f"{type(exc).__name__}: {_first_line(str(exc))}"


def _violation_detail(exc: ContractViolation) -> str:
    raw = None
    if exc.rejected_path is not None:
        raw = json.loads(Path(exc.rejected_path).read_text()).get("raw")
    if exc.problems == [NO_STRUCTURED_OUTPUT]:
        if isinstance(raw, str) and raw.strip():
            excerpt = " ".join(raw.split())[:EXCERPT_CHARS]
            return f'returned text instead of the required structured output: "{excerpt}"'
        return "returned no structured output"
    return f"returned invalid structured output: {'; '.join(exc.problems)}"


def _check_one(ctx: AgentContext, model: str, where: str, call: int) -> tuple[bool, str]:
    packet = build_packet("model_check", ModelCheckInput(word=CHECK_WORD), budget_tokens=1_000)
    try:
        provider_for_model(ctx.config, model, where)  # an unknown provider's error names the tier, not the check's role
        output = invoke_agent(
            MODEL_CHECK, packet, ctx, node="model_check", call=call,
            model=model, max_attempts=1, transient_retries=False,
        )
    except ContractViolation as exc:
        return False, _violation_detail(exc)
    except Exception as exc:
        return False, _error_detail(exc)
    assert isinstance(output, ModelCheck)
    if not output.ok:
        return False, "returned ok=false"
    if output.echo.strip() != CHECK_WORD:
        return False, f'echoed "{output.echo}" instead of "{CHECK_WORD}"'
    return True, ""


def check_models(
    config: PhilConfig,
    *,
    factory: AgentFactory | None = None,
    repo_root: Path,
    clock: Callable[[], float] = time.monotonic,
) -> list[CheckResult]:
    """One call per distinct configured model (see `check_targets`): a lean agent asked to return
    `ModelCheck(ok=true, echo=<word>)`, with the configured timeout, one try and no retries. Its
    telemetry and artifacts go to a temporary directory, not the project's."""
    targets = check_targets(config)
    if not targets:
        return []
    results: list[CheckResult] = []
    with tempfile.TemporaryDirectory(prefix="phil-models-check-") as scratch:
        conn = connect(Path(scratch) / "check.db")
        try:
            ctx = AgentContext(
                config=config,
                conn=conn,
                layer="chat",
                artifacts=ArtifactStore(Path(scratch) / "artifacts"),
                workdir=repo_root,
                factory=factory,
            )
            for call, (model, labels) in enumerate(targets, 1):
                started = clock()
                ok, detail = _check_one(ctx, model, labels[0], call)
                results.append(CheckResult(", ".join(labels), model, ok, clock() - started, detail))
        finally:
            conn.close()
    return results
