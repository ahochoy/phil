"""Pure, offline tests for `metrics.summarise`/`metrics.sweep` against hand-computed records.

No network, no models: every number here is derived by hand from the routing policy
(`phil.routing.decide`) applied to the fixed judgements below, so this test pins down
`metrics.py`'s behaviour precisely rather than just smoke-testing it."""

from tests.live.bench.classify import metrics

CONFIDENCE_THRESHOLD = 0.5
DETAIL_THRESHOLD = 0.6

RECORDS = [
    # Correct in every respect: class, depth (-> "answer").
    {
        "case_id": "q-01", "backend": "llm", "expected_class": "question", "expected_depth": "answer",
        "ambiguous": False, "task_class": "question", "probabilities": {"question": 0.9}, "confidence": 0.9,
        "needs_detail": 0.1, "latency_ms": 100, "input_tokens": 50, "output_tokens": 10, "cost_usd": 0.0001,
        "error": None,
    },
    {
        "case_id": "d-01", "backend": "llm", "expected_class": "diagnosis", "expected_depth": "answer",
        "ambiguous": False, "task_class": "diagnosis", "probabilities": {"diagnosis": 0.85}, "confidence": 0.85,
        "needs_detail": 0.05, "latency_ms": 150, "input_tokens": 60, "output_tokens": 12, "cost_usd": 0.00012,
        "error": None,
    },
    {
        "case_id": "s-01", "backend": "llm", "expected_class": "simple_change", "expected_depth": "quick",
        "ambiguous": False, "task_class": "simple_change", "probabilities": {"simple_change": 0.7},
        "confidence": 0.7, "needs_detail": 0.1, "latency_ms": 120, "input_tokens": 55, "output_tokens": 11,
        "cost_usd": 0.00011, "error": None,
    },
    {
        "case_id": "r-01", "backend": "llm", "expected_class": "refactor", "expected_depth": "full",
        "ambiguous": False, "task_class": "refactor", "probabilities": {"refactor": 0.95}, "confidence": 0.95,
        "needs_detail": 0.0, "latency_ms": 250, "input_tokens": 80, "output_tokens": 25, "cost_usd": 0.00025,
        "error": None,
    },
    # Misclassified as "question" (-> answer): expected a change ("full"). The worst-named direction
    # in reverse: a wasted turn, nothing changed ("change_as_answer").
    {
        "case_id": "f-01", "backend": "llm", "expected_class": "feature", "expected_depth": "full",
        "ambiguous": False, "task_class": "question", "probabilities": {"question": 0.85}, "confidence": 0.85,
        "needs_detail": 0.1, "latency_ms": 200, "input_tokens": 70, "output_tokens": 20, "cost_usd": 0.0002,
        "error": None,
    },
    # Misclassified as "small_operation" (-> quick): expected "answer". The worst error
    # ("answer_as_change"): an unwanted edit.
    {
        "case_id": "q-02", "backend": "llm", "expected_class": "question", "expected_depth": "answer",
        "ambiguous": False, "task_class": "small_operation", "probabilities": {"small_operation": 0.9},
        "confidence": 0.9, "needs_detail": 0.05, "latency_ms": 300, "input_tokens": 45, "output_tokens": 8,
        "cost_usd": 0.00008, "error": None,
    },
    # needs_detail clears the detail threshold: routed to intake regardless of task_class/confidence.
    # Also a true positive for the detail precision/recall check (ambiguous=True).
    {
        "case_id": "v-01", "backend": "llm", "expected_class": "other", "expected_depth": "full",
        "ambiguous": True, "task_class": "feature", "probabilities": {"feature": 0.4}, "confidence": 0.4,
        "needs_detail": 0.7, "latency_ms": 90, "input_tokens": 40, "output_tokens": 5, "cost_usd": 0.00005,
        "error": None,
    },
    # A false positive for the detail check: needs_detail clears the threshold but ambiguous=False.
    {
        "case_id": "o-01", "backend": "llm", "expected_class": "small_operation", "expected_depth": "quick",
        "ambiguous": False, "task_class": "small_operation", "probabilities": {"small_operation": 0.9},
        "confidence": 0.9, "needs_detail": 0.65, "latency_ms": 80, "input_tokens": 30, "output_tokens": 5,
        "cost_usd": 0.00003, "error": None,
    },
    # An error: no judgement at all, so `decide` sees None and routes to intake ("unavailable"). Its
    # cost is unknown, which makes the whole set's cost_per_100 unknown too.
    {
        "case_id": "x-01", "backend": "jev", "expected_class": "focused_fix", "expected_depth": "quick",
        "ambiguous": False, "task_class": None, "probabilities": {}, "confidence": None, "needs_detail": None,
        "latency_ms": 5000, "input_tokens": 0, "output_tokens": 0, "cost_usd": None, "error": "JevError: timeout",
    },
]


def test_summarise_every_field():
    summary = metrics.summarise(
        RECORDS, confidence_threshold=CONFIDENCE_THRESHOLD, detail_threshold=DETAIL_THRESHOLD
    )
    assert summary == {
        "n": 9,
        "errors": 1,
        "class_accuracy": 5 / 8,  # 8 non-error records; q-01, d-01, s-01, r-01, o-01 match
        "depth_accuracy": 4 / 9,  # q-01, d-01, s-01, r-01 route to their expected depth
        "intake_rate": 3 / 9,  # v-01 (needs_detail), o-01 (needs_detail), x-01 (error)
        "confusion": {
            "answer": {"answer": 2, "quick": 1},
            "quick": {"quick": 1, "intake": 2},
            "full": {"full": 1, "answer": 1, "intake": 1},
        },
        "answer_as_change": 1,  # q-02: expected answer, routed quick
        "change_as_answer": 1,  # f-01: expected full, routed answer
        "quick_as_full": 0,
        "full_as_quick": 0,
        "detail_precision": 0.5,  # TP=v-01, FP=o-01
        "detail_recall": 1.0,  # TP=v-01, FN=0
        "latency_p50_ms": 150,
        "latency_p95_ms": 5000,
        "cost_per_100": None,  # x-01's cost is unknown
    }


def test_sweep_replays_summarise_at_each_confidence_threshold():
    rows = metrics.sweep(RECORDS, thresholds=(0.9,), detail_threshold=DETAIL_THRESHOLD)
    assert rows == [{"threshold": 0.9, "depth_accuracy": 2 / 9, "intake_rate": 6 / 9}]


def test_sweep_default_thresholds():
    rows = metrics.sweep(RECORDS, detail_threshold=DETAIL_THRESHOLD)
    assert [row["threshold"] for row in rows] == [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
    assert all({"threshold", "depth_accuracy", "intake_rate"} == set(row) for row in rows)


def test_summarise_with_no_records():
    summary = metrics.summarise([], confidence_threshold=CONFIDENCE_THRESHOLD, detail_threshold=DETAIL_THRESHOLD)
    assert summary["n"] == 0
    assert summary["errors"] == 0
    assert summary["class_accuracy"] == 0.0
    assert summary["depth_accuracy"] == 0.0
    assert summary["intake_rate"] == 0.0
    assert summary["cost_per_100"] is None
