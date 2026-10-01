"""Run the classifier benchmark (spec §5.1) through Phil's real routing backends.

    PHIL_BENCH_CONFIG=~/phil-bench.toml uv run pytest -m bench tests/live/bench/classify -n 0
    uv run python -m tests.live.bench.classify.run --report

Every case is read straight from `cases.jsonl` into a `RouteState` (its `repo` is already
recorded, so no git repository is touched); each backend's answers are appended to
`~/.phil/bench/classify.jsonl` (or `$PHIL_BENCH_CLASSIFY_RESULTS`), one JSON record per case.
`--report` replays the stored records with no new calls."""

import argparse
import json
import os
import tempfile
import time
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from phil.agents.invoke import AgentContext
from phil.agents.providers import provider_for_model, split_model
from phil.config import PhilConfig, RoutingConfig
from phil.key_store import key_lookup
from phil.routing.jev import JevError, judge_jev
from phil.routing.llm import judge_llm
from phil.routing.types import Judgement
from phil.store.artifacts import ArtifactStore
from phil.store.db import connect
from phil.store.paths import phil_home
from tests.live.bench.classify import metrics
from tests.live.bench.harness import phil_sha

CASES_PATH = Path(__file__).parent / "cases.jsonl"

# Jev is worth recommending over the llm backend when all four conditions hold (spec §5.1).
DEPTH_ACCURACY_SLACK = 0.02
LATENCY_FACTOR = 3


def results_path() -> Path:
    """Where records go: `$PHIL_BENCH_CLASSIFY_RESULTS`, else `bench/classify.jsonl` under ~/.phil."""
    override = os.environ.get("PHIL_BENCH_CLASSIFY_RESULTS")
    return Path(override) if override else phil_home() / "bench" / "classify.jsonl"


def load_cases(path: Path = CASES_PATH) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _state(case: dict) -> dict:
    return {"request": case["request"], "chat": list(case.get("chat", [])), "repo": case["repo"]}


def _error_value(exc: Exception) -> str:
    """The exception's class name, plus `JevError.reason` for a `JevError` -- never `str(exc)`,
    which could otherwise carry something sensitive from an HTTP client or a model SDK."""
    if isinstance(exc, JevError):
        return f"{type(exc).__name__}: {exc.reason}"
    return type(exc).__name__


def _record(
    case: dict, backend: str, model: str, *, latency_ms: int, judgement: Judgement | None = None,
    input_tokens: int = 0, output_tokens: int = 0, cost_usd: float | None = None, error: str | None = None,
) -> dict:
    return {
        "case_id": case["id"],
        "backend": backend,
        "expected_class": case["expected_class"],
        "expected_depth": case["expected_depth"],
        "ambiguous": case["ambiguous"],
        "task_class": judgement.task_class if judgement else None,
        "probabilities": judgement.probabilities if judgement else {},
        "confidence": judgement.confidence if judgement else None,
        "needs_detail": judgement.needs_detail if judgement else None,
        "latency_ms": latency_ms,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": cost_usd,
        "error": error,
        "phil_sha": phil_sha(),
        "model": model,
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
    }


def _llm_usage(conn, chat_id: str, call: int) -> tuple[int, int, float | None]:
    """The one telemetry row `judge_llm`'s call wrote: tokens, and cost unless its source is
    "unknown" (no price for that model) -- this benchmark only counts a known cost."""
    row = conn.execute(
        "SELECT input_tokens, output_tokens, cost_usd, cost_source FROM telemetry"
        " WHERE chat_id = ? AND call = ? ORDER BY id DESC LIMIT 1",
        (chat_id, call),
    ).fetchone()
    if row is None:
        return 0, 0, None
    cost_usd = None if row["cost_source"] == "unknown" else float(row["cost_usd"])
    return int(row["input_tokens"]), int(row["output_tokens"]), cost_usd


def _run_jev(cases: list[dict], config: PhilConfig) -> list[dict]:
    model = config.model_for("classifier")
    spec = provider_for_model(config, model, "classifier")
    model_name = split_model(model)[1]
    records = []
    for case in cases:
        started = time.monotonic()
        try:
            judgement = judge_jev(
                spec, model_name, _state(case), timeout_s=config.routing.jev_timeout_s, environ=key_lookup()
            )
            records.append(_record(case, "jev", model, latency_ms=judgement.latency_ms, judgement=judgement))
        except Exception as exc:  # one bad case never stops the other 39
            latency_ms = int((time.monotonic() - started) * 1000)
            records.append(_record(case, "jev", model, latency_ms=latency_ms, error=_error_value(exc)))
    return records


def _run_llm(cases: list[dict], config: PhilConfig) -> list[dict]:
    model = config.tier_model("low")
    records = []
    with tempfile.TemporaryDirectory(prefix="phil-bench-classify-") as scratch:
        conn = connect(Path(scratch) / "bench.db")
        try:
            chat_id = f"bench-classify-{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}"
            ctx = AgentContext(
                config=config, conn=conn, layer="chat", chat_id=chat_id,
                artifacts=ArtifactStore(Path(scratch) / "artifacts"),
            )
            for call, case in enumerate(cases, start=1):
                started = time.monotonic()
                try:
                    judgement = judge_llm(ctx, _state(case), model=model, call=call)
                    input_tokens, output_tokens, cost_usd = _llm_usage(conn, chat_id, call)
                    records.append(
                        _record(
                            case, "llm", model, latency_ms=judgement.latency_ms, judgement=judgement,
                            input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=cost_usd,
                        )
                    )
                except Exception as exc:
                    latency_ms = int((time.monotonic() - started) * 1000)
                    records.append(_record(case, "llm", model, latency_ms=latency_ms, error=_error_value(exc)))
        finally:
            conn.close()
    return records


def run_backend(backend: Literal["jev", "llm"], cases: list[dict], config: PhilConfig) -> list[dict]:
    """Every case through `backend`'s classifier, as a list of benchmark records (spec §5.1)."""
    return _run_jev(cases, config) if backend == "jev" else _run_llm(cases, config)


def append_record(record: dict) -> None:
    path = results_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as out:
        out.write(json.dumps(record) + "\n")


def run_and_record(backend: Literal["jev", "llm"], cases: list[dict], config_path: Path) -> dict | None:
    """Run `backend` over `cases` from `config_path`'s `phil.toml`, append every record, and
    return the backend's `summarise`. `None` for `jev` when no TYPESAFE_API_KEY is configured --
    the caller skips rather than recording 40 doomed "missing key" errors."""
    if backend == "jev" and not key_lookup().get("TYPESAFE_API_KEY"):
        return None
    config = PhilConfig(**tomllib.loads(Path(config_path).read_text()))
    records = run_backend(backend, cases, config)
    for record in records:
        append_record(record)
    return metrics.summarise(
        records, confidence_threshold=config.routing.confidence_threshold,
        detail_threshold=config.routing.detail_threshold,
    )


def load_results(path: Path | None = None) -> list[dict]:
    target = path or results_path()
    if not target.is_file():
        return []
    return [json.loads(line) for line in target.read_text().splitlines() if line.strip()]


def _latest_per_case(records: list[dict]) -> list[dict]:
    """The newest record for each (case id, backend) pair, in file order. "Newest" means the
    last one in `records` -- the newest record across the whole results file, not one batch:
    there's no explicit "run id" grouping every case from a single invocation together, so a
    partial or interrupted run can leave a mix of ages behind this picks the latest of."""
    by_case: dict[tuple[str, str | None], dict] = {}
    for record in records:
        by_case[(record["case_id"], record.get("backend"))] = record
    return list(by_case.values())


def decision_rule(jev: dict, llm: dict) -> dict:
    """Spec §5.1's decision rule: is Jev worth recommending over the llm backend? All four
    conditions must hold; an unknown cost on either side makes the verdict undecided rather
    than yes or no."""
    depth_ok = jev["depth_accuracy"] >= llm["depth_accuracy"] - DEPTH_ACCURACY_SLACK
    answer_as_change_ok = jev["answer_as_change"] <= llm["answer_as_change"]
    latency_ok = jev["latency_p95_ms"] <= llm["latency_p95_ms"] / LATENCY_FACTOR
    jev_cost, llm_cost = jev["cost_per_100"], llm["cost_per_100"]
    cost_unknown = jev_cost is None or llm_cost is None
    cost_ok: bool | Literal["unknown"] = "unknown" if cost_unknown else jev_cost < llm_cost
    conditions = {
        "depth_accuracy_within_2_points": depth_ok,
        "answer_as_change_no_more_than_llm": answer_as_change_ok,
        "p95_latency_at_most_a_third_of_llm": latency_ok,
        "cost_per_100_lower_than_llm": cost_ok,
    }
    if cost_unknown:
        verdict = "undecided (cost unknown)"
    else:
        verdict = "yes" if depth_ok and answer_as_change_ok and latency_ok and cost_ok else "no"
    return {"conditions": conditions, "verdict": verdict}


def _print_summary(backend: str, summary: dict, sweep: list[dict]) -> None:
    print(f"--- {backend} ---")
    for key, value in summary.items():
        print(f"  {key}: {value}")
    print("  sweep (confidence_threshold -> depth_accuracy, intake_rate):")
    for row in sweep:
        print(f"    {row['threshold']}: {row['depth_accuracy']:.3f}, {row['intake_rate']:.3f}")


def _report(records: list[dict]) -> None:
    thresholds = RoutingConfig()
    latest = _latest_per_case(records)
    summaries: dict[str, dict] = {}
    for backend in ("jev", "llm"):
        backend_records = [record for record in latest if record.get("backend") == backend]
        if not backend_records:
            print(f"--- {backend}: no results ---")
            continue
        summary = metrics.summarise(
            backend_records, confidence_threshold=thresholds.confidence_threshold,
            detail_threshold=thresholds.detail_threshold,
        )
        summaries[backend] = summary
        sweep = metrics.sweep(backend_records, detail_threshold=thresholds.detail_threshold)
        _print_summary(backend, summary, sweep)
    if "jev" in summaries and "llm" in summaries:
        result = decision_rule(summaries["jev"], summaries["llm"])
        print("--- decision rule (spec §5.1) ---")
        for name, value in result["conditions"].items():
            print(f"  {name}: {value}")
        print(f"  jev worth recommending: {result['verdict']}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m tests.live.bench.classify.run")
    parser.add_argument("--report", action="store_true", help="print the latest run and the decision rule")
    args = parser.parse_args(argv)
    if not args.report:
        parser.print_help()
        return
    records = load_results()
    if not records:
        print(f"no benchmark results in {results_path()}")
        return
    _report(records)


if __name__ == "__main__":
    main()
