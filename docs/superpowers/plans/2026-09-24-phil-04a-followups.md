# Plan 4a (Chat): Follow-ups for Later Plans

Findings from plan 4a's task reviews and final review that were deliberately deferred. Earlier follow-ups: `2026-09-23-phil-01-followups.md`, `2026-09-23-phil-02-followups.md`, `2026-09-24-phil-03a-followups.md`, `2026-09-24-phil-03b-followups.md` (its "Plan 4" section lists the 4b work).

## Design rules learned in 4a (apply to every later plan)

- **Chat agents never read the working tree.** The architect reads a `git archive` snapshot of the base commit's tracked files (`phil.chat.snapshot.export_tree`), so untracked secrets and uncommitted changes never reach a model provider. Any new chat agent with file access gets the same snapshot.
- **One launch gate.** `phil.chat.approval.launch_problems` (run-role models + test command equal to `[project] test_cmd` or allowed by `[shell] allow`) guards every path that starts a run: the chat's approval and `phil run`. The worker runs the test command without the allowlist, so a new launch path must call it too.
- **Escape TOML section names in rich markup.** Rich treats `[models]` as a style tag and drops it; wrap literal section names in `escape(...)`.

## Plan 4b — chat ergonomics (user feedback from the first live chat, 2026-09-28)

**Status: done in plan 4b.** Design: `docs/superpowers/specs/2026-09-28-phil-04b-live-chat-design.md`. Implementation: `phil.chat.controller.ChatController`, `phil.chat.terminal` (`TerminalIO`/`LineIO`), `phil.ui.toolbar`, `phil.chat.watcher.RunWatcher`.

**Verdict:** wording is fine; the chat feels mechanical, transactional and disconnected. Principles: communicative, not chatty; always obvious what Phil is doing (status indicators, affordances); never an extra step to answer a basic question.

What happened:
1. While planning, the only feedback was the static word "Planning…" — nothing showed work in progress.
2. After approval the run went to the background and the chat lost track of it. Following it meant opening a second terminal and running `phil attach`.
3. When the run needed an approval, it had to be answered in that second terminal, not in the chat where the work started.
4. Approve-then-wait felt transactional; there was no sense of being able to keep working while the run proceeds.

Changes made:
- **Live progress for every step: done.** The bottom toolbar (`ui/toolbar.py`) shows a spinner and elapsed time for intake, snapshot, architect, critic, revision and `/btw` steps (e.g. "Architect drafting · 12s"), replacing static text.
- **Runs belong to the chat that started them: done.** `runs.chat_id` records the chat that launched a run (`prepare_run(..., chat_id=)`). The toolbar shows a live status line for the chat's own run (node, task n/m, elapsed) while the prompt stays usable.
- **Pauses come back to the chat: done.** A `run_paused` event prints the question immediately and flags the toolbar; the chat asks it at the next idle prompt or on `/answer`, and resumes the run with the answer (spawning a resume worker) — no second terminal needed. `phil attach` still works from anywhere.
- **Keep working meanwhile: done.** The prompt keeps accepting input while a goal job or a run proceeds; typing a new goal while one is in flight asks `Replace the current goal? [y/n]`.
- **Completion notice: done.** `run_done` prints the outcome, tasks done, tokens, cost, open issues, and the next action (`phil diff <run>`, the summary).
- **In-chat commands: partly done, differently.** `/btw <question>` and `/answer` were added; `/resume` (continue a failed/stopped run) was added too. There is no `/attach <id>` in the chat — the chat already follows its own run via `RunWatcher`, so streaming another run inline wasn't needed. `/runs` lists all of the repo's runs (`phil.ui.runs_view.render_runs`), same as `phil runs`; it does not list the chat's own runs first.
- **Separate windows stay separate: done.** Each `ChatController` only watches the run(s) it started (`self._run_id`); a `RunWatcher`'s events for a run the chat no longer follows are dropped (`RUN_EVENTS` check in `_handle`).

Implementation note (superseded by the 4b design's decisions): a `prompt_toolkit` prompt session with a bottom toolbar runs on the main thread; model-call jobs and one `RunWatcher` thread post `ChatEvent`s onto a `queue.Queue` the main loop drains between prompts (`phil.chat.terminal.TerminalIO`, `phil.chat.events`). Rich output is captured under `prompt_toolkit.patch_stdout(raw=True)` rather than a bespoke `ChatOutput`/ANSI adapter (see the design's §3 note on this deviation). `Brief`/`present()` landed for `/btw`'s answer; `/more` and `/park` moved to plan 4c (below).

## Plan 4c — carried items

Moved from plan 4b's scope (see the 4b design's §7 "Out of scope"):

- Token/cost accuracy: usage callback and cost reconciliation.
- Tool-call telemetry.
- OpenRouter SDK timeout, and treating a 200-with-error response as transient.
- `phil show` / `/more`.
- `/park`.
- `open_issues` dedup and summary cleanup (carried from 03b, still open).
- Consider a lean read-only architect: now that it reads a snapshot, the deep harness mostly adds tokens.

## Before a public release

- **Provider-agnostic models (user direction, 2026-09-28).** Phil must not depend solely on OpenRouter. Support bring-your-own provider: OpenRouter, OpenAI/ChatGPT/Codex, Anthropic/Claude, Google/Gemini, a local model (e.g. Ollama), and a **custom endpoint** (a model served on the user's own server, configured with a base URL, API style and key variable). Today `[models]` strings go to LangChain's `init_chat_model` ("provider:model"), and `phil.config.PROVIDER_KEYS` only knows the key variables for openrouter, openai, anthropic and google_genai; unknown providers are not key-checked. This needs its own design: a `[providers.<name>]` section (base_url, api_key_env, kind), per-provider capability checks (tool calling, structured output), and live evals per provider.
- **Developer vs end-user diagnostics (user direction, 2026-09-28).** Library warnings about Phil's internals (e.g. a dependency's deprecation or Python-compatibility warning) matter to Phil's developers, not its users, who can't act on them. Keep them visible in development (tests, a `--debug`/dev mode) and out of end users' terminals; if something needs a user's attention, say it plainly ("this version of Phil …"), never a raw library warning.

## Dependency policy

- **Keep dependencies current (user direction, 2026-09-28).** Upgrade everything regularly (`uv lock --upgrade`), raise the `pyproject.toml` floors to the locked versions, run the full suite and a live check. Record any held-back package here with its reason.
- Held back: `openrouter` stays at 0.10.x because 0.11 caps `pydantic<2.13`, and pydantic 2.12 prints a Python 3.14 compatibility warning on import (via `langchain_core`). `pyproject.toml` requires `pydantic>=2.13`; lift the hold when `openrouter` allows it.

## Later

- The snapshot follows `git archive` rules: `.gitattributes` `export-ignore`/`export-subst` apply, submodule contents are absent, LFS files arrive as pointers. It can differ slightly from the run's worktree; document it or export with `git worktree add --detach` instead.
- `export_tree` holds the whole tar in memory; stream with `tarfile.open(fileobj=proc.stdout, mode="r|")` via `Popen` for very large repos.
- Committed symlinks that point outside the repo are skipped in the snapshot (not followed); the architect sees them as missing.
- `ChatSession.create` checks then `mkdir`s: two chats started in the same second can race (`FileExistsError`); retry on `mkdir(exist_ok=False)`.
- `repo_overview` reads the whole README before slicing, and `git ls-files` C-quotes non-ASCII names (use `-z`).
- Blank lines at the idle prompt are not written to the transcript (everything else is stored raw).
- The architect and critic keep using the snapshot of the base resolved at plan time; if HEAD moves before approval, the run starts from the newer HEAD (the start message shows the commit).
