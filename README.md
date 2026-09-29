# Phil

Phil is a CLI coding agent built around explicit contracts between agents, managed context, and code-enforced gates. Run it inside a git repository; it plans a goal with you, then implements it in an isolated git worktree and leaves a tested, reviewed branch.

Design: `docs/superpowers/specs/2026-09-23-phil-v1-design.md`

## Usage

Plan and start work from a chat in your repo (set models first — see phil.toml):

    cd your-repo
    phil                     # type a goal; approve the plan with y / edit / n

One chat follows one goal at a time — intake, plan, approval, then its run — and shows a live
bottom toolbar (the current step and its elapsed time, then the run's node and task progress).
The prompt stays usable while a goal is being planned or a run works in the background:

- If the run needs your input, the chat flags it right away (`⏸ <run> needs you: …`) and asks
  at the next idle prompt; jump to it any time with `/answer`.
- Ask a side question while work continues with `/btw <question>` — read-only, it never changes
  the plan or the run.
- If a run failed or was stopped, continue it with `/resume`.
- `/runs` lists runs, `/help` shows the commands, `/quit` (or Ctrl-D) leaves the chat — a run
  left running keeps going in the background.

Reopen a chat later:

    phil                      # lists this repo's open chats; pick a number or press Enter for a new one
    phil --resume <chat-id>   # reopen a specific chat directly
    phil --new                # skip the list and start a new chat

## Development

    uv sync
    uv run pytest
    uv run phil --version

The original LangGraph prototype is kept in `prototype/` for reference and is not part of the package.

Live tests call a real model through OpenRouter and are skipped by default:

    set -a; source .env; set +a
    uv run pytest -m live

Tests run in parallel with pytest-xdist; use `uv run pytest -n 0` to run serially when debugging.

Start a run from a plan file and follow it:

    uv run phil run plan.json
    uv run phil attach <run-id>
