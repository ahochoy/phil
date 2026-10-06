"""Pure metrics over classifier-benchmark records (spec §5.1).

`summarise` and `sweep` take no models and make no calls: they replay the routing policy
(`phil.routing.decide`) over already-recorded judgements, so a threshold sweep costs nothing."""

import math
from collections import Counter

from phil.routing.policy import decide
from phil.routing.types import Judgement

DEFAULT_THRESHOLDS = (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)


def _judgement(record: dict) -> Judgement | None:
    """The record's `Judgement`, or `None` for an error record (no classification happened)."""
    if record.get("error"):
        return None
    return Judgement(
        task_class=record["task_class"],
        probabilities=record.get("probabilities") or {},
        confidence=record["confidence"],
        needs_detail=record["needs_detail"],
        source=record.get("backend") or "llm",
        latency_ms=record.get("latency_ms") or 0,
        usage=None,
    )


def _routed_depth(record: dict, *, confidence_threshold: float, detail_threshold: float) -> str:
    """The depth `decide` routes `record` to, or `"intake"` when it defers."""
    depth, _ = decide(
        _judgement(record), confidence_threshold=confidence_threshold, detail_threshold=detail_threshold
    )
    return depth or "intake"


def _depth_correct(depth: str, record: dict) -> bool:
    """Routed to the case's expected depth; for an ambiguous case, deferring to intake is right too."""
    return depth == record["expected_depth"] or (depth == "intake" and bool(record.get("ambiguous")))


def _percentile(values: list[int], p: float) -> int:
    """The `p`th percentile by nearest rank; 0 for an empty list."""
    if not values:
        return 0
    ordered = sorted(values)
    rank = max(1, min(len(ordered), math.ceil(p / 100 * len(ordered))))
    return int(ordered[rank - 1])


def summarise(records: list[dict], *, confidence_threshold: float, detail_threshold: float) -> dict:
    """Accuracy, confusion, detail precision/recall, latency and cost over `records` (spec §5.1)."""
    n = len(records)
    errors = sum(1 for record in records if record.get("error"))
    errors_by_kind = dict(Counter(record["error"] for record in records if record.get("error")))
    non_error = [record for record in records if not record.get("error")]
    class_correct = sum(1 for record in non_error if record.get("task_class") == record.get("expected_class"))
    class_accuracy = class_correct / len(non_error) if non_error else 0.0

    routed = [
        (_routed_depth(record, confidence_threshold=confidence_threshold, detail_threshold=detail_threshold), record)
        for record in records
    ]
    depth_correct = sum(1 for depth, record in routed if _depth_correct(depth, record))
    depth_accuracy = depth_correct / n if n else 0.0
    intake_count = sum(1 for depth, _ in routed if depth == "intake")
    intake_rate = intake_count / n if n else 0.0

    confusion: dict[str, dict[str, int]] = {}
    answer_as_change = change_as_answer = quick_as_full = full_as_quick = 0
    wrong_path = missed_detail = 0
    for depth, record in routed:
        expected = record["expected_depth"]
        bucket = confusion.setdefault(expected, {})
        bucket[depth] = bucket.get(depth, 0) + 1
        if expected == "answer" and depth in ("quick", "full"):
            answer_as_change += 1
        elif expected in ("quick", "full") and depth == "answer":
            change_as_answer += 1
        if expected == "quick" and depth == "full":
            quick_as_full += 1
        elif expected == "full" and depth == "quick":
            full_as_quick += 1
        if depth != "intake" and depth != record["expected_depth"]:
            wrong_path += 1
        if record.get("ambiguous") and depth != "intake":
            missed_detail += 1

    detail_tp = detail_fp = detail_fn = 0
    for record in records:
        predicted = (record.get("needs_detail") or 0.0) >= detail_threshold
        actual = bool(record.get("ambiguous"))
        if predicted and actual:
            detail_tp += 1
        elif predicted and not actual:
            detail_fp += 1
        elif not predicted and actual:
            detail_fn += 1
    detail_precision = detail_tp / (detail_tp + detail_fp) if (detail_tp + detail_fp) else 0.0
    detail_recall = detail_tp / (detail_tp + detail_fn) if (detail_tp + detail_fn) else 0.0

    latencies = [int(record["latency_ms"]) for record in records if record.get("latency_ms") is not None]
    costs = [record.get("cost_usd") for record in records]
    cost_per_100 = None if not records or any(cost is None for cost in costs) else sum(costs) / n * 100

    return {
        "n": n,
        "errors": errors,
        "errors_by_kind": errors_by_kind,
        "class_accuracy": class_accuracy,
        "depth_accuracy": depth_accuracy,
        "intake_rate": intake_rate,
        "confusion": confusion,
        "answer_as_change": answer_as_change,
        "change_as_answer": change_as_answer,
        "quick_as_full": quick_as_full,
        "full_as_quick": full_as_quick,
        "wrong_path": wrong_path,
        "unsafe": answer_as_change + full_as_quick,
        "intake_count": intake_count,
        "missed_detail": missed_detail,
        "detail_precision": detail_precision,
        "detail_recall": detail_recall,
        "latency_p50_ms": _percentile(latencies, 50),
        "latency_p95_ms": _percentile(latencies, 95),
        "cost_per_100": cost_per_100,
    }


def sweep(
    records: list[dict], thresholds: tuple[float, ...] = DEFAULT_THRESHOLDS, *, detail_threshold: float = 0.6
) -> list[dict]:
    """`summarise` replayed at each confidence threshold, `detail_threshold` held fixed."""
    rows = []
    for threshold in thresholds:
        summary = summarise(records, confidence_threshold=threshold, detail_threshold=detail_threshold)
        rows.append(
            {"threshold": threshold, "depth_accuracy": summary["depth_accuracy"], "intake_rate": summary["intake_rate"]}
        )
    return rows


DEFAULT_CONFIDENCE = 0.5
DEFAULT_DETAIL = 0.6


def sweep_grid(
    records: list[dict],
    confidence_grid: tuple[float, ...] = DEFAULT_THRESHOLDS,
    detail_grid: tuple[float, ...] = DEFAULT_THRESHOLDS,
) -> list[dict]:
    """`summarise` at every (confidence, detail) pair, replayed from stored answers (spec §4.2)."""
    rows = []
    for confidence in confidence_grid:
        for detail in detail_grid:
            summary = summarise(records, confidence_threshold=confidence, detail_threshold=detail)
            rows.append({
                "confidence_threshold": confidence, "detail_threshold": detail,
                **{key: summary[key] for key in ("depth_accuracy", "wrong_path", "unsafe", "intake_count", "missed_detail")},
            })
    return rows


def choose_thresholds(
    records: list[dict],
    confidence_grid: tuple[float, ...] = DEFAULT_THRESHOLDS,
    detail_grid: tuple[float, ...] = DEFAULT_THRESHOLDS,
) -> dict:
    """The grid row a backend should run at (plan B, ruling R1): fewest unsafe errors, then wrong
    paths, then vague requests not asked about, then deferrals; ties go to the row nearest today's
    defaults, then the lower thresholds."""

    def key(row: dict) -> tuple:
        distance = abs(row["confidence_threshold"] - DEFAULT_CONFIDENCE) + abs(row["detail_threshold"] - DEFAULT_DETAIL)
        return (
            row["unsafe"], row["wrong_path"], row["missed_detail"], row["intake_count"], round(distance, 6),
            row["confidence_threshold"], row["detail_threshold"],
        )

    return min(sweep_grid(records, confidence_grid, detail_grid), key=key)
