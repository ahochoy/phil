"""Task classes, their descriptions and examples, and the depth each maps to (spec §3.1).

The routing backends and the benchmark all read the wording from here, so the questions a model
sees are the same everywhere."""

from dataclasses import dataclass

DEPTHS = ("answer", "quick", "full")


@dataclass(frozen=True)
class ClassInfo:
    description: str
    examples: tuple[str, str]


CLASSES: dict[str, ClassInfo] = {
    "question": ClassInfo(
        "Asks for information or an explanation about the code or project; nothing should change.",
        ("How does the retry logic in invoke.py work?", "Which file defines the CLI commands?"),
    ),
    "diagnosis": ClassInfo(
        "Asks why something is broken or behaves unexpectedly, wanting the cause found, not yet fixed.",
        ("Why does the build fail on a clean checkout?", "Figure out why login sometimes returns 500."),
    ),
    "small_operation": ClassInfo(
        "A small mechanical operation with an obvious result: rename, bump a version, move or delete a file.",
        ("Bump the version to 1.4.2.", "Rename utils.py to helpers.py and update the imports."),
    ),
    "simple_change": ClassInfo(
        "A small, well-specified edit in one or two places: copy, markup, a config value, one small function.",
        ("Fix the typo 'recieve' in the footer.", "Add a meta description tag to the home page."),
    ),
    "focused_fix": ClassInfo(
        "Fix a specific, located bug whose cause is known or obvious from the request.",
        ("divide() crashes on zero; return None instead.", "The date parser drops the timezone; keep it."),
    ),
    "feature": ClassInfo(
        "Add new behaviour that needs design choices, several files, or new tests.",
        ("Add CSV export to the reports page.", "Support login with GitHub."),
    ),
    "refactor": ClassInfo(
        "Restructure existing code without changing behaviour, across more than a couple of places.",
        ("Split controller.py into smaller modules.", "Replace the hand-written retry loops with one helper."),
    ),
    "design": ClassInfo(
        "Decide or document an architecture or approach before (or instead of) building it.",
        ("Design a plugin system for Phil.", "How should we structure offline sync? Write it up."),
    ),
    "broad_project": ClassInfo(
        "Large, multi-part work spanning many files or subsystems.",
        ("Migrate the app from REST to GraphQL.", "Build a full admin dashboard with auth and audit logs."),
    ),
    "other": ClassInfo(
        "None of the above, or impossible to tell from the request.",
        ("Hello!", "Thanks, that's all."),
    ),
}

DEPTH: dict[str, str] = {
    "question": "answer",
    "diagnosis": "answer",
    "small_operation": "quick",
    "simple_change": "quick",
    "focused_fix": "quick",
    "feature": "full",
    "refactor": "full",
    "design": "full",
    "broad_project": "full",
}

TASK_CLASS_QUESTION = (
    "Which kind of work does `request` ask a coding agent to do in this repository? "
    "Use `chat` only to understand references like 'it' or 'that'."
)
NEEDS_DETAIL_QUESTION = (
    "Is `request` too ambiguous for a coding agent to act on without first asking the user something "
    "(a missing target, conflicting goals, or no way to tell what done means)?"
)
NEEDS_DETAIL_CRITERIA = {
    "true": "A competent developer would have to ask the user a question before starting.",
    "false": "A competent developer could start now, making reasonable choices on minor details.",
}
