# Phil

Phil is a CLI coding agent built around explicit contracts between agents, managed context, and code-enforced gates. Run it inside a git repository; it plans a goal with you, then implements it in an isolated git worktree and leaves a tested, reviewed branch.

Design: `docs/superpowers/specs/2026-09-23-phil-v1-design.md`

## Development

    uv sync
    uv run pytest
    uv run phil --version

The original LangGraph prototype is kept in `prototype/` for reference and is not part of the package.
