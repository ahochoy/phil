import pytest

from phil.routing import CLASSES, DEPTH, Judgement, decide, parse_override


def judgement(task_class="simple_change", confidence=0.9, needs_detail=0.1, source="jev") -> Judgement:
    return Judgement(
        task_class=task_class, probabilities={task_class: 1.0}, confidence=confidence,
        needs_detail=needs_detail, source=source, latency_ms=10, usage=None,
    )


def test_every_class_has_a_depth_and_other_has_none():
    assert set(CLASSES) == set(DEPTH) | {"other"}
    assert DEPTH["question"] == DEPTH["diagnosis"] == "answer"
    assert {DEPTH[c] for c in ("small_operation", "simple_change", "focused_fix")} == {"quick"}
    assert {DEPTH[c] for c in ("feature", "refactor", "design", "broad_project")} == {"full"}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/ask what does calc do", ("answer", "what does calc do")),
        ("/QUICK fix the typo", ("quick", "fix the typo")),
        ("/full   add auth", ("full", "add auth")),
        ("/ask", ("answer", "")),
        ("fix the typo", (None, "fix the typo")),
        ("/asking is not an override", (None, "/asking is not an override")),
        ("/btw hi", (None, "/btw hi")),
        ("/ask\nwhat does calc do", ("answer", "what does calc do")),
        ("/ask\twhat does calc do", ("answer", "what does calc do")),
        ("/full\n\n add auth\nwith tokens", ("full", "add auth\nwith tokens")),
        ("  /quick  ", ("quick", "")),
    ],
)
def test_parse_override(text, expected):
    assert parse_override(text) == expected


T = {"confidence_threshold": 0.5, "detail_threshold": 0.6}


@pytest.mark.parametrize(
    ("j", "expected"),
    [
        (None, (None, "unavailable")),
        (judgement(needs_detail=0.6), (None, "needs_detail")),  # the threshold is inclusive
        (judgement(needs_detail=0.59), ("quick", "class")),
        (judgement(task_class="other"), (None, "other")),
        (judgement(confidence=0.49), (None, "low_confidence")),
        (judgement(confidence=0.5), ("quick", "class")),
        (judgement(task_class="question"), ("answer", "class")),
        (judgement(task_class="feature"), ("full", "class")),
        # needs_detail is checked before confidence and class
        (judgement(task_class="question", confidence=0.2, needs_detail=0.9), (None, "needs_detail")),
    ],
)
def test_decide(j, expected):
    assert decide(j, **T) == expected
