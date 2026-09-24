# Phil

Phil is a CLI coding agent built around explicit contracts between agents, managed context, and code-enforced gates. Run it inside a git repository; it plans a goal with you, then implements it in an isolated git worktree and leaves a tested, reviewed branch.

Design: `docs/superpowers/specs/2026-09-23-phil-v1-design.md`

## Development

    uv sync
    uv run pytest
    uv run phil --version

The original LangGraph prototype is kept in `prototype/` for reference and is not part of the package.

Live tests call a real model through OpenRouter and are skipped by default:

    set -a; source .env; set +a
    uv run pytest -m live

Tests run in parallel with pytest-xdist; use `uv run pytest -p no:xdist` to run serially when debugging.

Start a run from a plan file and follow it:

    uv run phil run plan.json
    uv run phil attach <run-id>
